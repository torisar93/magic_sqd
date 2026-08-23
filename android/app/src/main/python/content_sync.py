"""Синхронизация cars/ (только скрипты/инструкции, БЕЗ files/usb_files —
тяжёлые payload'ы качаются точечно перед конкретной установкой, отдельный
шаг) с сервера content/manifest.json. Порт desktop-версии
(app/content_sync.py) — та написана на чистом stdlib (urllib/json/pathlib/
concurrent.futures), поэтому переносится почти без изменений, только урезана
до того, что нужно для списка машин (без apk/-библиотеки и files/model —
это отдельные следующие шаги)."""
import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

_DOWNLOAD_WORKERS = 16


class ContentSyncError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def _encode_path(path: str) -> str:
    return "/".join(quote(part) for part in path.split("/") if part)


def fetch_manifest(base_url: str):
    """content/manifest.json — {"<путь>": {"size": int, "mtime": float}, ...}
    -> {"<путь>": {"size": int, "mtime": float}}."""
    try:
        with urllib.request.urlopen(f"{base_url}/manifest.json", timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    files = data.get("files")
    if not isinstance(files, dict):
        return None
    result = {}
    for path, entry in files.items():
        if isinstance(entry, dict) and isinstance(entry.get("size"), int):
            result[path] = {"size": entry["size"], "mtime": entry.get("mtime")}
    return result


def filter_manifest(manifest, subpath: str, skip_dirs=(), no_recurse_dirs=()):
    prefix = subpath.strip("/")
    result = []
    for path, entry in manifest.items():
        if prefix and path != prefix and not path.startswith(f"{prefix}/"):
            continue
        dir_segments = path.split("/")[:-1]
        if any(seg in skip_dirs for seg in dir_segments):
            continue
        if any(path.startswith(f"{nr}/") and "/" in path[len(nr) + 1:] for nr in no_recurse_dirs):
            continue
        result.append({"path": path, "size": entry["size"], "mtime": entry.get("mtime")})
    return result


def _is_stale(local_path: Path, item: dict) -> bool:
    """Файл нужно перекачать, если его нет локально, либо отличается размер,
    либо (при известном mtime с сервера) сохранённая при прошлой закачке
    mtime не совпадает — только на size раньше полагались, и правка файла
    без изменения байтовой длины (частый случай для текстовых инструкций)
    тихо не подхватывалась, см. download_file/_set_mtime ниже."""
    if not local_path.exists():
        return True
    st = local_path.stat()
    if st.st_size != item.get("size", -1):
        return True
    remote_mtime = item.get("mtime")
    if remote_mtime is not None and abs(st.st_mtime - remote_mtime) > 2:
        return True
    return False


def download_file(base_url: str, remote_path: str, dest: Path, chunk_size: int = 1024 * 1024,
                   mtime: float | None = None) -> None:
    url = f"{base_url}/{_encode_path(remote_path)}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(tmp_dest, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        try:
            tmp_dest.unlink()
        except OSError:
            pass
        raise ContentSyncError(f"Ошибка скачивания {remote_path}: {exc}") from exc
    except BaseException:
        try:
            tmp_dest.unlink()
        except OSError:
            pass
        raise
    tmp_dest.replace(dest)
    if mtime is not None:
        # Штампуем mtime сервера на локальный файл — это то, с чем следующий
        # sync сравнит manifest.json (см. _is_stale), а не время самого
        # скачивания.
        try:
            os.utime(dest, (mtime, mtime))
        except OSError:
            pass


def _download_one(base_url: str, remote_path: str, local_path: Path, log, mtime: float | None = None) -> bool:
    log(f"Скачиваю {remote_path}...")
    try:
        download_file(base_url, remote_path, local_path, mtime=mtime)
        return True
    except ContentSyncError as exc:
        log(f"Не удалось скачать {remote_path}: {exc}")
        return False


def sync_tree(base_url: str, remote_subpath: str, local_dir: Path, manifest, log=lambda m: None,
              skip_dirs=(), no_recurse_dirs=(), on_progress=lambda done, total: None) -> int:
    """Скачивает из manifest всё, чего в local_dir ещё нет (или отличается
    по размеру/mtime). on_progress(done, total) вызывается сразу с (0, N) —
    чтобы UI сразу знал общее число файлов, ещё до первой закачки — а затем
    после каждого завершённого файла (успешного или нет, чтобы бар всегда
    дошёл до конца). Возвращает число скачанных файлов."""
    remote_subpath = remote_subpath.strip("/")
    items = filter_manifest(manifest, remote_subpath, skip_dirs=skip_dirs, no_recurse_dirs=no_recurse_dirs)

    to_download = []
    for item in items:
        rel = item["path"][len(remote_subpath):].lstrip("/") if remote_subpath else item["path"]
        if not rel:
            continue
        local_path = local_dir / rel
        if not _is_stale(local_path, item):
            continue
        to_download.append((item["path"], local_path, item.get("mtime")))

    total = len(to_download)
    on_progress(0, total)
    done_count = 0
    downloaded = 0
    if to_download:
        with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as executor:
            futures = [executor.submit(_download_one, base_url, path, local_path, log, mtime)
                       for path, local_path, mtime in to_download]
            for future in futures:
                if future.result():
                    downloaded += 1
                done_count += 1
                on_progress(done_count, total)
    if downloaded:
        log(f"Скачано файлов: {downloaded}.")
    return downloaded


def sync_shared_folder(base_url: str, cars_dir: Path, folder_name: str, log=lambda m: None,
                        on_progress=lambda done, total: None) -> int:
    """Скачивает cars/_shared/<folder_name>/ целиком — общие наборы файлов
    для "usb"-этапов многих моделей (StepSpec.usb_shared_folder), которые
    sync_scripts НЕ качает (no_recurse_dirs пропускает подпапки _shared/,
    см. sync_tree — это данные, не Python-скрипты)."""
    manifest = fetch_manifest(base_url)
    if manifest is None:
        log("Не удалось получить manifest.json с сервера — работаем с тем, что уже скачано локально.")
        return 0
    # Без skip_dirs/no_recurse_dirs — тут нет вложенных files/usb_files-по-
    # модельному смыслу, качаем ВСЁ дерево общей папки как есть.
    return sync_tree(base_url, f"cars/_shared/{folder_name}", cars_dir / "_shared" / folder_name, manifest,
                      log=log, on_progress=on_progress)


def sync_scripts(base_url: str, cars_dir: Path, log=lambda m: None,
                  on_progress=lambda done, total: None) -> int:
    """Автообновление скриптов/инструкций всех моделей (cars/), без
    files/usb_files (тяжёлые payload'ы, отдельный шаг перед установкой)."""
    manifest = fetch_manifest(base_url)
    if manifest is None:
        log("Не удалось получить manifest.json с сервера — работаем с тем, что уже скачано локально.")
        return 0
    return sync_tree(base_url, "cars", cars_dir, manifest, log=log,
                      skip_dirs=("files", "usb_files"), no_recurse_dirs=("cars/_shared",),
                      on_progress=on_progress)


def sync_model_subfolder(base_url: str, cars_dir: Path, local_dir: Path, log=lambda m: None,
                          on_progress=lambda done, total: None, manifest=None) -> int:
    """Синхронизирует ОДНУ конкретную подпапку модели (например
    files/instruction_N) — не всю files/+usb_files разом (см.
    sync_model_payload ниже). Портовая копия desktop app/content_sync.py:
    sync_model_subfolder — вызывается при ОТКРЫТИИ модели (см.
    mobile_bridge.sync_payload) для того, что нужно показать сразу
    (инструкции); остальное (APK apps-этапа, usb_files, прикреплённые к
    adb/actions файлы) качается по клику на соответствующем этапе — см.
    apk_library.ensure_apks_downloaded, вызывается из WebBridge.kt."""
    if manifest is None:
        manifest = fetch_manifest(base_url)
        if manifest is None:
            log("Не удалось получить manifest.json с сервера — работаем с тем, что уже скачано локально.")
            return 0
    remote_subpath = "cars/" + local_dir.relative_to(cars_dir).as_posix()
    return sync_tree(base_url, remote_subpath, local_dir, manifest, log=log, on_progress=on_progress)


def sync_model_payload(base_url: str, cars_dir: Path, model_dir: Path, log=lambda m: None,
                        on_progress=lambda done, total: None) -> int:
    """Точечно скачивает files/ и usb_files/ КОНКРЕТНОЙ модели (APK, файлы
    для флешки, инструкции с фото) — вызывается перед открытием мастера
    установки для этой модели, а не при общей sync_scripts (которая их
    специально пропускает, см. sync_tree: skip_dirs=("files","usb_files"))."""
    manifest = fetch_manifest(base_url)
    if manifest is None:
        log("Не удалось получить manifest.json с сервера — работаем с тем, что уже скачано локально.")
        return 0
    remote_subpath = "cars/" + model_dir.relative_to(cars_dir).as_posix()

    to_download = []
    for sub in ("files", "usb_files"):
        items = filter_manifest(manifest, f"{remote_subpath}/{sub}")
        for item in items:
            rel = item["path"][len(remote_subpath) + 1:]
            local_path = model_dir / rel
            if not _is_stale(local_path, item):
                continue
            to_download.append((item["path"], local_path, item.get("mtime")))

    total = len(to_download)
    on_progress(0, total)
    done_count = 0
    downloaded = 0
    if to_download:
        with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as executor:
            futures = [executor.submit(_download_one, base_url, path, local_path, log, mtime)
                       for path, local_path, mtime in to_download]
            for future in futures:
                if future.result():
                    downloaded += 1
                done_count += 1
                on_progress(done_count, total)
    if downloaded:
        log(f"Скачано файлов модели: {downloaded}.")
    return downloaded
