"""Иконка стороннего APK для новых списков в интерфейсе (см.
app/web/bridge.py:WebApi.scanner_apk_icon и разделы "Получение иконки APK"/
"Иконки сторонних APK и серверная часть" в docs UI-transfer пакета,
2026-09-14). Два источника, в этом порядке:

    1. content-сервер — см. server/apk_icon_catalog.py, публикует готовые
       PNG в поле "apk_icons" manifest.json (тот же манифест, что и
       content_sync.fetch_manifest, — см. content_config.get_base_url для
       адреса). Так получают иконку общие APK из apk/ и APK моделей из
       cars/, даже если сам файл ещё не скачан локально (remote_only,
       см. scanner.scan_apks) — сервер уже извлёк иконку из своей копии.
    2. локально, через tools/aapt.exe dump badging — для личных APK
       техника, которых на сервере нет вовсе. В отличие от серверного
       apk_icon_catalog.py (Pillow + опционально CairoSVG, см. его
       docstring), здесь берётся только готовый РАСТРОВЫЙ launcher-ресурс
       (.png/.webp/.jpg) прямо из .apk как zip — без Pillow/CairoSVG,
       никакого рендеринга adaptive-icon/vector XML. Личные APK с только
       векторной иконкой остаются без превью — тот же нейтральный значок,
       что и при отсутствии aapt.exe вовсе.

apk_icon(path) никогда не бросает исключение — любая проблема (сеть, нет
tools/aapt.exe, битый APK) превращается в None, чтобы интерфейс просто
показал нейтральную иконку вместо падения. Результат кэшируется в памяти
процесса ~300 секунд по (путь, размер, mtime); отрицательный результат
(None) — короткую паузу (см. _ERROR_BACKOFF_SECONDS), чтобы список из
полусотни APK без иконки не долбил aapt.exe/сеть на каждый повторный показ
экрана."""
from __future__ import annotations
import base64
import json
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from .content_config import get_base_url
from .platform_paths import bundled_tools_root

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

_MANIFEST_TTL_SECONDS = 300   # см. docs: "нативные мосты кешируют индекс примерно 5 минут"
_RESULT_TTL_SECONDS = 300     # см. docs: "Ответ кэшируется на 300 секунд"
_ERROR_BACKOFF_SECONDS = 20   # см. docs: "ошибка — короткая пауза перед повтором"

_MAX_ENTRY_BYTES = 2_000_000  # тот же лимит, что и на сервере (apk_icon_catalog._MAX_ENTRY_BYTES) — не читаем в память гигантский ресурс
_RASTER_SUFFIXES = (".png", ".webp", ".jpg", ".jpeg")
_MIME_BY_SUFFIX = {".png": "image/png", ".webp": "image/webp", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
_ICON_REL_RE = re.compile(r"icons/[0-9a-f]{64}\.png")  # см. docs: "Клиенты принимают только icons/[0-9a-f]{64}.png ... Не принимать произвольный URL из манифеста"

_lock = threading.Lock()
_manifest_cache: dict = {}
_manifest_cache_at = 0.0
_manifest_cache_base_url: str | None = None
_result_cache: dict[tuple, tuple[float, "str | None"]] = {}  # (путь, size, mtime_ns) -> (истекает, результат)
_MAX_RESULT_CACHE_ENTRIES = 2000  # за одну сессию программы столько разных APK не смотрят — простая защита от роста


def _base_dir() -> Path:
    """Папка рядом с exe/скриптом — та же, что main_web.py:get_base_dir(),
    но не импортируем main_web напрямую: у него есть побочные эффекты при
    самом импорте (переключение SSL-контекста по умолчанию — см. верх
    main_web.py), а этот модуль должен оставаться лёгким и безопасным для
    импорта где угодно."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _find_aapt(base_dir: Path) -> str | None:
    """В отличие от find_adb_path (app/adb_utils.py) НЕ откатываемся на
    голое имя из PATH — на технической машине aapt туда в принципе не
    ставят, лишний PATH-lookup на каждый некэшированный APK только вносит
    задержку без шанса на успех. tools/aapt.exe на Windows, tools_mac/aapt
    (Mach-O без расширения, из тех же build-tools Android SDK) на macOS —
    см. tools_mac/README.txt."""
    if sys.platform == "win32":
        bundled = base_dir / "tools" / "aapt.exe"
    else:
        bundled = bundled_tools_root(base_dir) / "tools_mac" / "aapt"
    return str(bundled) if bundled.is_file() else None


def _fetch_apk_icons(base_url: str) -> dict:
    """manifest.json целиком уже качает content_sync.fetch_manifest (поле
    "files") — здесь отдельный, отдельно кэшируемый мини-клиент только для
    поля "apk_icons": вызывается на каждый APK в списке, чаще, чем обычная
    синхронизация при старте, поэтому свой TTL и без похода в content_sync
    (там этого поля вовсе нет)."""
    global _manifest_cache, _manifest_cache_at, _manifest_cache_base_url
    now = time.monotonic()
    with _lock:
        if _manifest_cache_base_url == base_url and now - _manifest_cache_at < _MANIFEST_TTL_SECONDS:
            return _manifest_cache
    icons: dict = {}
    try:
        with urllib.request.urlopen(f"{base_url}/manifest.json", timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        candidate = data.get("apk_icons")
        if isinstance(candidate, dict):
            icons = candidate
    except (urllib.error.URLError, ValueError, OSError):
        icons = {}
    with _lock:
        _manifest_cache = icons
        _manifest_cache_at = now
        _manifest_cache_base_url = base_url
    return icons


def _relative_to_base(path: Path, base_dir: Path) -> str | None:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return None  # путь вне base_dir (не должно происходить для apk_dir/cars_dir, но не повод падать)


def _server_icon_url(path: Path, base_dir: Path) -> str | None:
    base_url = get_base_url(base_dir)
    if not base_url:
        return None  # server.json не настроен — вся серверная синхронизация молча пропускается, как и везде в проекте
    rel = _relative_to_base(path, base_dir)
    if rel is None:
        return None
    icon_rel = _fetch_apk_icons(base_url).get(rel)
    if not icon_rel or not _ICON_REL_RE.fullmatch(icon_rel):
        return None
    return f"{base_url}/{icon_rel}"


def _extract_local(path: Path, base_dir: Path) -> str | None:
    """tools/aapt.exe dump badging — тот же список кандидатов, что и в
    server/apk_icon_catalog.py:extract_icon, но без Pillow/CairoSVG: берём
    первый подходящий растровый ресурс как есть, без масштабирования (сам
    launcher-значок и так маленький — 48-192px)."""
    if not path.is_file():
        return None  # remote_only-запись (см. scanner.scan_apks) — файла ещё нет, извлекать нечего
    aapt = _find_aapt(base_dir)
    if not aapt:
        return None
    try:
        result = subprocess.run(
            [aapt, "dump", "badging", str(path)], capture_output=True, timeout=12,
            creationflags=CREATE_NO_WINDOW,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    output = result.stdout.decode("utf-8", "replace")
    candidates = re.findall(r"application-icon-(\d+):'([^']+)'", output)
    candidates = [entry for _, entry in sorted(candidates, key=lambda pair: int(pair[0]), reverse=True)]
    candidates += re.findall(r"application:.*?icon='([^']+)'", output)
    try:
        with zipfile.ZipFile(path) as archive:
            for entry in dict.fromkeys(candidates):
                suffix = Path(entry).suffix.lower()
                if suffix not in _RASTER_SUFFIXES:
                    continue  # .xml (vector/adaptive-icon) — без Pillow/CairoSVG клиент их не рендерит, см. docstring модуля
                try:
                    info = archive.getinfo(entry)
                except KeyError:
                    continue
                if info.file_size > _MAX_ENTRY_BYTES:
                    continue
                data = archive.read(entry)
                return f"data:{_MIME_BY_SUFFIX[suffix]};base64,{base64.b64encode(data).decode('ascii')}"
    except (zipfile.BadZipFile, OSError):
        return None
    return None


def apk_icon(path: str) -> "str | None":
    """См. app/web/bridge.py:WebApi.scanner_apk_icon. path — тот же
    абсолютный путь, что ApkInfo.path отдаёт наружу как строку (см.
    app/web/api/scanner_api.py:apk_to_dict) — для remote_only записей файл
    может ещё не существовать на диске, тогда остаётся только серверный
    путь. Возвращает URL (готовая серверная иконка), data:-изображение
    (локальное извлечение) или None (нет иконки нигде/любая ошибка)."""
    try:
        apk_path = Path(path)
        base_dir = _base_dir()
        try:
            stat = apk_path.stat()
            cache_key = (str(apk_path), stat.st_size, stat.st_mtime_ns)
        except OSError:
            cache_key = (str(apk_path), -1, -1)  # ещё не скачан (remote_only) — кэшируем всё равно, просто по имени

        now = time.monotonic()
        with _lock:
            cached = _result_cache.get(cache_key)
        if cached is not None and now < cached[0]:
            return cached[1]

        result = _server_icon_url(apk_path, base_dir)
        if result is None:
            result = _extract_local(apk_path, base_dir)

        ttl = _RESULT_TTL_SECONDS if result is not None else _ERROR_BACKOFF_SECONDS
        with _lock:
            if len(_result_cache) >= _MAX_RESULT_CACHE_ENTRIES:
                _result_cache.clear()
            _result_cache[cache_key] = (now + ttl, result)
        return result
    except Exception:
        return None
