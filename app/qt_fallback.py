"""Резервный движок отображения на случай, когда WebView2 Runtime поставить
не удалось (см. app/webview2_check.py, main_web.py:_ensure_webview2) — а
пользователь всё равно хочет полнофункциональную программу, а не урезанный
интерфейс. pywebview умеет рендерить через Qt WebEngine "из коробки"
(webview/platforms/qt.py, идёт вместе с pywebview) — там свой встроенный
Chromium, никак не завязанный на системный WebView2/IE, поэтому это смена
ДВИЖКА ОТРИСОВКИ, а не отдельная реализация интерфейса: app/web/frontend/
используется как есть, без единой правки.

Сам PySide6 (Qt-биндинг с WebEngine) НЕ бандлится в инсталлятор — это
~200 МБ архив (~490 МБ распакованным), качаем отдельно и только если
реально понадобилось (см. download_and_extract). Хостится там же, где и
сам инсталлятор — content_config.get_download_base_url(), тот же
server.json.

Технический нюанс (см. историю разработки — поймано и проверено вживую на
заморозке PyInstaller): abi3-колесо PySide6 линкуется на python3.dll
(стабильный ABI-редирект), которого в обычной заморозке PyInstaller нет
(нужен только abi3-модулям, из статических импортов main_web.py такого не
видно) — без него загрузка падает с "DLL load failed" на ЛЮБОЙ чистой
заморозке. Кладём его в саму сборку (magic_sqd.spec/admin.spec:
binaries=[('assets/python3.dll', '.')]), не в этот скачиваемый архив — он
нужен независимо от того, будет ли вообще Qt-резерв использован."""
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .content_config import get_download_base_url

ASSET_NAME = "qt_fallback_win64.zip"
DOWNLOAD_TIMEOUT_SECONDS = 60
_DIR_NAME = "_qt_fallback"
# Один файл, который точно есть при успешной распаковке — отличает "ничего
# не скачано" от "скачивание оборвалось на середине" (не хотим, чтобы
# is_downloaded() соврал на битой папке).
_MARKER_FILE = Path("PySide6") / "QtCore.pyd"


def _target_dir(base_dir: Path) -> Path:
    return base_dir / _DIR_NAME


def is_downloaded(base_dir: Path) -> bool:
    return (_target_dir(base_dir) / _MARKER_FILE).exists()


def download_and_extract(base_dir: Path, log=lambda m: None, check_cancelled=lambda: None) -> bool:
    """Качает ASSET_NAME с get_download_base_url() и распаковывает в
    base_dir/_qt_fallback — атомарно (распаковка идёт во временную папку
    рядом, переименование в боевое имя — последним шагом), чтобы обрыв
    посреди скачивания/распаковки не оставил битую папку под именем,
    которое is_downloaded() посчитает готовой. Возвращает False на любой
    ошибке (сеть, место на диске, отмена) — молча, log() уже объяснил, что
    случилось, вызывающий (main_web.py) сам решает, что показать технику."""
    base_url = get_download_base_url(base_dir)
    if not base_url:
        log("Адрес сервера не настроен (server.json) — не могу скачать резервный движок.")
        return False

    with tempfile.TemporaryDirectory(dir=str(base_dir)) as tmp:
        archive_path = Path(tmp) / ASSET_NAME
        log(f"Скачиваю резервный движок отображения ({ASSET_NAME}, ~200 МБ)...")
        try:
            _download(f"{base_url}/{ASSET_NAME}", archive_path, log, check_cancelled)
        except Exception as exc:  # noqa: BLE001 - показываем технику любую причину сбоя
            log(f"Не удалось скачать: {exc}")
            return False

        extract_dir = Path(tmp) / "extracted"
        extract_dir.mkdir()
        log("Распаковываю...")
        try:
            with zipfile.ZipFile(archive_path) as zf:
                zf.extractall(extract_dir)
        except (zipfile.BadZipFile, OSError) as exc:
            log(f"Не удалось распаковать: {exc}")
            return False

        if not (extract_dir / _MARKER_FILE).exists():
            log("Скачанный архив выглядит повреждённым (нет ожидаемых файлов).")
            return False

        target = _target_dir(base_dir)
        shutil.rmtree(target, ignore_errors=True)
        try:
            shutil.move(str(extract_dir), str(target))
        except OSError as exc:
            log(f"Не удалось установить: {exc}")
            return False

    log("Резервный движок отображения установлен.")
    return True


def _download(url: str, dest: Path, log, check_cancelled) -> None:
    """Тот же приём, что и app/web/api/update_api.py:_download (кусками на
    .part, атомарная замена) — независимая копия: этот модуль должен уметь
    работать ДО того, как основной pywebview-стек вообще поднялся."""
    tmp = dest.with_name(dest.name + ".part")
    last_logged_mb = 0
    try:
        with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp, open(tmp, "wb") as f:
            while True:
                check_cancelled()
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                mb = f.tell() // (1024 * 1024)
                if mb - last_logged_mb >= 20:
                    last_logged_mb = mb
                    log(f"...{mb} МБ")
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)


def prepare_sys_path(base_dir: Path) -> None:
    """Делает qtpy/PySide6/shiboken6 импортируемыми — вызывается ДО первого
    `import qtpy`/`webview.start(gui='qt')`. add_dll_directory нужен явно:
    PySide6 сам регистрирует свою и соседнюю shiboken6 папку при импорте
    (см. PySide6/__init__.py:_setupQtDirectories), но это происходит уже
    ПОСЛЕ того, как Python нашёл сам PySide6/__init__.py через sys.path —
    сам этот первый шаг ничего специального не требует, add_dll_directory
    тут просто подстраховка (PySide6 всё равно сделает то же самое сам)."""
    import os

    target = _target_dir(base_dir)
    sys.path.insert(0, str(target))
    if hasattr(os, "add_dll_directory"):
        for sub in ("PySide6", "shiboken6"):
            d = target / sub
            if d.is_dir():
                os.add_dll_directory(str(d))
