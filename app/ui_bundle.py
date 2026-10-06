"""Интерфейс программы с сервера (владелец, 2026-10-06: «чтобы обновлений приложения стало поменьше»).

Интерфейс ПК и Android — HTML/JS/CSS. Правка вёрстки, текста, окна раньше требовала нового выпуска программы; теперь
её можно выложить «бандлом интерфейса» для уже вышедшей версии (scripts/publish_ui.py): программа скачивает его при
запуске, проверяет и показывает со следующего запуска вместо встроенного.

Как это устроено:
- content/ui/manifest.json: {"schema": 1, "desktop": {"1.0.62": {"rev": 3, "file": "desktop-1.0.62-r3.zip",
  "size": …, "sha256": "…", "sig": "…"}}, "android": {…}}. Бандл — ровно под версию программы (её мост к Python/
  Kotlin тот же, publish_ui.py проверяет, что после выпуска менялся только интерфейс). Новая версия программы
  встроенным интерфейсом уже содержит все правки — старые бандлы к ней не подходят и убираются.
- Подпись Ed25519 (ed25519.py) над "magicsqd-ui|<платформа>|<версия>|<rev>|<sha256>"; ключ подписи — только у
  разработчика, в программе — открытый ключ ниже. Не сошлась подпись, размер, sha256, в архиве путь наружу или
  чужой тип файла — бандл не ставится, остаётся встроенный интерфейс.
- rev 0 или нет записи — «выключить»: со следующего запуска встроенный интерфейс (аварийный откат с сервера).
- Бандл, с которым интерфейс не запустился (программа не дождалась ui_ready), помечается плохим — до нового rev
  программа к нему не вернётся.
- ПК: архив накладывается на копию встроенного интерфейса (крупные картинки в архив не входят) и окно открывается из
  этой папки. Android: в папке только файлы архива, остальное WebView берёт из APK (MainActivity: UiBundleAssets).

Файл ОДИНАКОВЫЙ на ПК (app/ui_bundle.py) и Android (android/app/src/main/python/ui_bundle.py) — tests/test_ui_bundle.py."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

try:
    from . import ed25519  # ПК: модуль пакета app
except ImportError:  # Android (Chaquopy): модули верхнего уровня
    import ed25519

PUBLIC_KEY = bytes.fromhex("afb16ed28fbfb6260c10996e6b898237f72470270ed644ce3961572375404a46")
MANIFEST_PATH = "ui/manifest.json"  # от content/
SCHEMA = 1
STATE_NAME = "state.json"
PLATFORMS = ("desktop", "android")
MAX_BUNDLE_BYTES = 40 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
ALLOWED_SUFFIXES = frozenset({".html", ".js", ".css", ".json", ".svg", ".png", ".webp", ".jpg", ".jpeg", ".gif",
                              ".ico", ".ttf", ".otf", ".woff", ".woff2", ".txt", ".mp4"})
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def signed_message(platform: str, app_version: str, rev: int, sha256: str) -> bytes:
    return f"magicsqd-ui|{platform}|{app_version}|{int(rev)}|{sha256.lower()}".encode("ascii")


def bundle_dir_name(platform: str, app_version: str, rev: int) -> str:
    return f"{platform}-{app_version}-r{int(rev)}"


def _read_state(root: Path) -> dict:
    try:
        data = json.loads((Path(root) / STATE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(root: Path, state: dict) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / f".{STATE_NAME}.part"
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, root / STATE_NAME)


def _bad_revs(state: dict) -> list:
    bad = state.get("bad")
    return [rev for rev in bad if isinstance(rev, int)] if isinstance(bad, list) else []


def active_dir(root, platform: str, app_version: str):
    """Папка проверенного бандла для ЭТОЙ версии программы или None (тогда встроенный интерфейс)."""
    root = Path(root)
    state = _read_state(root)
    rev = state.get("rev")
    if state.get("platform") != platform or state.get("app") != app_version:
        return None
    if not isinstance(rev, int) or isinstance(rev, bool) or rev <= 0 or rev in _bad_revs(state):
        return None
    name = state.get("dir")
    if not isinstance(name, str) or name != bundle_dir_name(platform, app_version, rev):
        return None
    folder = root / name
    if platform == "desktop" and not (folder / "index.html").is_file():
        return None
    return folder if folder.is_dir() else None


def active_rev(root, platform: str, app_version: str) -> int:
    folder = active_dir(root, platform, app_version)
    return int(_read_state(Path(root)).get("rev") or 0) if folder else 0


def mark_bad(root, rev: int) -> None:
    """С этим бандлом интерфейс не запустился — больше его не включать (новый rev — включится)."""
    root = Path(root)
    state = _read_state(root)
    bad = _bad_revs(state)
    if rev not in bad:
        bad.append(int(rev))
    state["bad"] = bad
    _write_state(root, state)


def cleanup(root, platform: str, app_version: str, log=lambda m: None) -> None:
    """При запуске: убрать папки старых бандлов и бандлов от прошлых версий программы (кроме действующего)."""
    root = Path(root)
    if not root.is_dir():
        return
    keep = active_dir(root, platform, app_version)
    for item in root.iterdir():
        if item.name == STATE_NAME:
            continue
        if keep is not None and item.resolve() == keep.resolve():
            continue
        if item.is_dir():
            shutil.rmtree(item, ignore_errors=True)
        else:
            try:
                item.unlink()
            except OSError:
                pass
    state = _read_state(root)
    if state and state.get("app") != app_version:
        _write_state(root, {})  # бандл был к прошлой версии программы — новая версия уже содержит правки
        log("Интерфейс с сервера к прошлой версии программы убран — теперь встроенный.")


def _entry(manifest, platform: str, app_version: str):
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        return None
    section = manifest.get(platform)
    entry = section.get(app_version) if isinstance(section, dict) else None
    return entry if isinstance(entry, dict) else None


def _valid_entry(entry: dict) -> bool:
    rev, size, sha, sig, name = (entry.get(k) for k in ("rev", "size", "sha256", "sig", "file"))
    return (isinstance(rev, int) and not isinstance(rev, bool) and rev > 0
            and isinstance(size, int) and 0 < size <= MAX_BUNDLE_BYTES
            and isinstance(sha, str) and re.fullmatch(r"[0-9a-fA-F]{64}", sha) is not None
            and isinstance(sig, str) and re.fullmatch(r"[0-9a-fA-F]{128}", sig) is not None
            and isinstance(name, str) and _NAME_RE.fullmatch(name) is not None and name.endswith(".zip"))


def verify_entry(entry: dict, platform: str, app_version: str, data: bytes) -> str | None:
    """Почему этот архив нельзя ставить; None — всё сошлось."""
    if len(data) != entry["size"]:
        return f"размер {len(data)} вместо {entry['size']}"
    digest = hashlib.sha256(data).hexdigest()
    if digest != entry["sha256"].lower():
        return "контрольная сумма не сошлась"
    if not ed25519.verify(PUBLIC_KEY, signed_message(platform, app_version, entry["rev"], digest),
                          bytes.fromhex(entry["sig"])):
        return "подпись не сошлась"
    return None


def _safe_members(archive: zipfile.ZipFile) -> list:
    """Файлы архива или ValueError: путь наружу, абсолютный путь, чужой тип файла, слишком большой файл."""
    members = []
    total = 0
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename
        path = PurePosixPath(name)
        if name.startswith(("/", "\\")) or "\\" in name or ".." in path.parts or ":" in name:
            raise ValueError(f"недопустимый путь в архиве: {name}")
        if path.suffix.lower() not in ALLOWED_SUFFIXES:
            raise ValueError(f"недопустимый тип файла в архиве: {name}")
        if info.file_size > MAX_FILE_BYTES:
            raise ValueError(f"слишком большой файл в архиве: {name}")
        total += info.file_size
        if total > MAX_BUNDLE_BYTES * 3:
            raise ValueError("архив распаковывается в слишком большой объём")
        members.append(info)
    if not members:
        raise ValueError("пустой архив")
    return members


def install(root, platform: str, app_version: str, entry: dict, data: bytes, builtin_dir=None) -> Path:
    """Распаковать проверенный архив в папку бандла (ПК — поверх копии встроенного интерфейса) и сделать его
    действующим со следующего запуска. Бросает ValueError/OSError — тогда всё остаётся как было."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    name = bundle_dir_name(platform, app_version, entry["rev"])
    target = root / name
    tmp = root / f".{name}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        if builtin_dir is not None:
            shutil.copytree(builtin_dir, tmp)
        else:
            tmp.mkdir()
        import io
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in _safe_members(archive):
                dest = tmp.joinpath(*PurePosixPath(info.filename).parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        if platform == "desktop" and not (tmp / "index.html").is_file():
            raise ValueError("в интерфейсе нет index.html")
        if target.exists():
            shutil.rmtree(target)
        os.replace(tmp, target)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    state = _read_state(root)
    bad = _bad_revs(state) if state.get("app") == app_version else []
    _write_state(root, {"platform": platform, "app": app_version, "rev": int(entry["rev"]), "dir": name,
                        "sha256": entry["sha256"].lower(), "bad": bad})
    return target


def _get(url: str, timeout: float, limit: int) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise ValueError("слишком большой ответ сервера")
    return data


def refresh(base_url: str, root, platform: str, app_version: str, builtin_dir=None, timeout: float = 30,
            log=lambda m: None) -> str:
    """Сверить с сервером. Возвращает "updated" (новый бандл готов, со следующего запуска), "disabled" (сервер
    выключил бандл — со следующего запуска встроенный интерфейс), "same", "none" (для этой версии бандла нет) или
    "error" (сеть, проверка — остаётся как было)."""
    if platform not in PLATFORMS or not base_url or not app_version:
        return "none"
    root = Path(root)
    try:
        manifest = json.loads(_get(f"{base_url.rstrip('/')}/{MANIFEST_PATH}", timeout, 1024 * 1024).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            manifest = {"schema": SCHEMA}  # раздела ещё нет на сервере — как «бандлов нет»
        else:
            return "error"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return "error"
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        return "error"
    entry = _entry(manifest, platform, app_version)
    state = _read_state(root)
    current = active_dir(root, platform, app_version)
    if entry is None or entry.get("rev") == 0:
        if current is not None or (state.get("app") == app_version and state.get("rev")):
            bad = _bad_revs(state)
            _write_state(root, {"platform": platform, "app": app_version, "rev": 0, "bad": bad})
            log("Интерфейс с сервера выключен — со следующего запуска встроенный.")
            return "disabled"
        return "none"
    if not _valid_entry(entry):
        log("Интерфейс с сервера: запись в manifest.json не годится — пропускаю.")
        return "error"
    if entry["rev"] in _bad_revs(state) and state.get("app") == app_version:
        return "same"  # с этим rev интерфейс уже не запустился — ждём следующего
    if current is not None and state.get("rev") == entry["rev"]:
        if state.get("sha256") == entry["sha256"].lower():
            return "same"
        # Тот же rev с другим содержимым — publish_ui.py так не делает; папку, из которой сейчас открыт интерфейс,
        # на ходу не меняем.
        log("Интерфейс с сервера: тот же выпуск с другим содержимым — пропускаю.")
        return "error"
    try:
        data = _get(f"{base_url.rstrip('/')}/ui/{entry['file']}", timeout, MAX_BUNDLE_BYTES)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return "error"
    problem = verify_entry(entry, platform, app_version, data)
    if problem:
        log(f"Интерфейс с сервера не установлен: {problem}.")
        return "error"
    try:
        install(root, platform, app_version, entry, data, builtin_dir=builtin_dir)
    except (ValueError, OSError, zipfile.BadZipFile) as exc:
        log(f"Интерфейс с сервера не установлен: {exc}.")
        return "error"
    log(f"Обновление интерфейса (выпуск {entry['rev']}) скачано — применится при следующем запуске программы.")
    return "updated"
