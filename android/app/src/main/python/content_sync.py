"""Синхронизация cars/ (только скрипты/инструкции, БЕЗ files/usb_files —
тяжёлые payload'ы качаются точечно перед конкретной установкой, отдельный
шаг) с сервера content/manifest.json. Порт desktop-версии
(app/content_sync.py) — та написана на чистом stdlib (urllib/json/pathlib/
concurrent.futures), поэтому переносится почти без изменений, только урезана
до того, что нужно для списка машин (без apk/-библиотеки и files/model —
это отдельные следующие шаги)."""
import json
import os
import shutil
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote, urlparse

import catalog_crypto
import catalog_key
import offline_pack

_DOWNLOAD_WORKERS = 16

# Скрытые (тестовые) модели — группы пользователей (server/user_groups.py): вошедший в аккаунт
# техник присылает cookie сессии со всеми запросами к каталогу, и сервер отдаёт ему модели его
# групп. Ставит WebBridge.kt (applyCatalogSession) при запуске, входе и выходе; cookie уходит
# только на сервер аккаунтов (host). То же, что desktop app/content_sync.py.
_auth_cookie = None
_auth_host = None


def set_auth_cookie(cookie, host=None) -> None:
    global _auth_cookie, _auth_host
    _auth_cookie, _auth_host = (cookie, host) if cookie and host else (None, None)


# Токен официальной сборки (app_token): сервер отдаёт каталог только с ним. Ставит WebBridge при старте,
# если в сборке есть секрет; без секрета (из исходников) — None, запрос идёт без токена.
_app_token = None


def set_app_token(token) -> None:
    global _app_token
    _app_token = token


def app_token_header() -> dict:
    """Заголовок токена официальной сборки для своих запросов к серверу (ИИ-мастер, ai_client.py); {} — без токена."""
    return _app_token.header() if _app_token is not None else {}


def open_url(url: str, timeout: float):
    """urlopen для запросов к каталогу — с токеном официальной сборки и cookie сессии техника."""
    request = urllib.request.Request(url)
    if _app_token is not None:
        for name, value in _app_token.header().items():
            request.add_header(name, value)
    if _auth_cookie and urlparse(url).hostname == _auth_host:
        request.add_header("Cookie", _auth_cookie)
    return urllib.request.urlopen(request, timeout=timeout)


class ContentSyncError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def _encode_path(path: str) -> str:
    return "/".join(quote(part) for part in path.split("/") if part)


def fetch_manifest(base_url: str):
    """content/manifest.json — {"<путь>": {"size": int, "mtime": float}, ...}
    -> {"<путь>": {"size": int, "mtime": float}}."""
    global _last_early_access
    try:
        with open_url(f"{base_url}/manifest.json", timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    files = data.get("files")
    if not isinstance(files, dict):
        return None
    early = data.get("early_access")
    _last_early_access = early if isinstance(early, dict) else {}
    result = {}
    for path, entry in files.items():
        if isinstance(entry, dict) and isinstance(entry.get("size"), int):
            result[path] = {"size": entry["size"], "mtime": entry.get("mtime")}
    return result


# Раздел "early_access" последнего манифеста (см. desktop app/content_sync.py — то же самое).
_last_early_access: dict = {}


def last_early_access() -> dict:
    return _last_early_access


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
    # Зашифрованный файл модели (catalog_key) больше на catalog_crypto.OVERHEAD — это та же версия.
    if not catalog_crypto.stored_size_matches(local_path, st.st_size, item.get("size", -1)):
        return True
    remote_mtime = item.get("mtime")
    if remote_mtime is not None and abs(st.st_mtime - remote_mtime) > 2:
        return True
    return False


def local_copy_is_current(path: Path, item: dict) -> bool:
    """Локальная копия — та же версия, что на сервере: тот же размер и время изменения. Время сервера ставится при
    скачивании (download_file(mtime=…)); APK, докачанные до 1.0.56, его не получали — у них время скачивания: скачан
    ПОСЛЕ последней правки файла на сервере — та же версия (время сервера ставим), раньше — устарел. Копия desktop
    app/content_sync.py:local_copy_is_current."""
    try:
        st = path.stat()
    except OSError:
        return False
    if not catalog_crypto.stored_size_matches(path, st.st_size, item.get("size", -1)):
        return False
    remote_mtime = item.get("mtime")
    if remote_mtime is None or abs(st.st_mtime - remote_mtime) <= 2:
        return True
    if st.st_mtime > remote_mtime:
        try:
            os.utime(path, (st.st_atime, remote_mtime))
        except OSError:
            pass
        return True
    return False


def download_file(base_url: str, remote_path: str, dest: Path, chunk_size: int = 1024 * 1024,
                   mtime: float | None = None, on_progress=None, check_cancelled=lambda: None) -> None:
    url = f"{base_url}/{_encode_path(remote_path)}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest.with_name(dest.name + ".part")
    try:
        check_cancelled()
        with open_url(url, timeout=60) as resp, open(tmp_dest, "wb") as f:
            expected = resp.headers.get("Content-Length")
            expected = int(expected) if expected is not None and expected.isdigit() else None
            received = 0
            if on_progress:
                on_progress(received, expected or 0)
            while True:
                check_cancelled()
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
                if on_progress:
                    on_progress(received, expected or 0)
            # Реальный случай (пуш большого .apk на магнитолу с обрубленным
            # на телефоне файлом): resp.read() у urllib на некоторых обрывах
            # соединения молча возвращает "конец потока" вместо исключения —
            # без сверки с Content-Length получаем НЕПОЛНЫЙ файл, который
            # выглядит как успешно скачанный (see ensure_apks_downloaded —
            # она потом больше никогда не перескачает "уже существующий"
            # обрубленный файл).
            if expected is not None and received != expected:
                raise ContentSyncError(
                    f"Скачано {received} из {expected} байт — соединение оборвалось")
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
    # Собственные файлы модели (спека, скрипты, инструкции — catalog_key.is_encrypted_path) храним
    # зашифрованными ключом этой установки; без ключа (из исходников) — как есть.
    if catalog_key.is_configured() and catalog_key.is_encrypted_path(remote_path):
        tmp_dest.write_bytes(catalog_key.encrypt_bytes(tmp_dest.read_bytes()))
    tmp_dest.replace(dest)
    if mtime is not None:
        # Штампуем mtime сервера на локальный файл — это то, с чем следующий
        # sync сравнит manifest.json (см. _is_stale), а не время самого
        # скачивания.
        try:
            os.utime(dest, (mtime, mtime))
        except OSError:
            pass


def _download_one(base_url: str, remote_path: str, local_path: Path, log, mtime: float | None = None,
                  on_chunk=None) -> bool:
    log(f"Скачиваю {remote_path}...")
    try:
        download_file(base_url, remote_path, local_path, mtime=mtime, on_progress=on_chunk)
        return True
    except ContentSyncError as exc:
        log(f"Не удалось скачать {remote_path}: {exc}")
        return False


def sync_tree(base_url: str, remote_subpath: str, local_dir: Path, manifest, log=lambda m: None,
              skip_dirs=(), no_recurse_dirs=(), on_progress=lambda done, total: None, on_bytes=None) -> int:
    """Скачивает из manifest всё, чего в local_dir ещё нет (или отличается
    по размеру/mtime). on_progress(done, total) вызывается сразу с (0, N) —
    чтобы UI сразу знал общее число файлов, ещё до первой закачки — а затем
    после каждого завершённого файла (успешного или нет, чтобы бар всегда
    дошёл до конца). on_bytes(получено, всего) — байты по всем файлам сразу, по мере прихода (из потоков
    закачки): комплект для флешки бывает под полгигабайта, и без этого окно записи минутами просто крутилось
    (Belgee S50, 28.09). Возвращает число скачанных файлов."""
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
        to_download.append((item["path"], local_path, item.get("mtime"), int(item.get("size") or 0)))

    received: dict[str, int] = {}
    received_lock = threading.Lock()
    total_bytes = sum(size for *_rest, size in to_download)

    def chunk_reporter(path: str):
        if on_bytes is None:
            return None

        def report(done: int, _expected: int) -> None:
            with received_lock:
                received[path] = done
                got = sum(received.values())
            on_bytes(got, max(total_bytes, got))
        return report

    total = len(to_download)
    on_progress(0, total)
    done_count = 0
    downloaded = 0
    if to_download:
        if on_bytes is not None:
            on_bytes(0, total_bytes)
        with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as executor:
            futures = [executor.submit(_download_one, base_url, path, local_path, log, mtime, chunk_reporter(path))
                       for path, local_path, mtime, _size in to_download]
            for future in futures:
                if future.result():
                    downloaded += 1
                done_count += 1
                on_progress(done_count, total)
    if downloaded:
        log(f"Скачано файлов: {downloaded}.")
    return downloaded


def sync_shared_folder(base_url: str, cars_dir: Path, folder_name: str, log=lambda m: None,
                        on_progress=lambda done, total: None, on_bytes=None) -> int:
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
                      log=log, on_progress=on_progress, on_bytes=on_bytes)


def sync_scripts(base_url: str, cars_dir: Path, log=lambda m: None,
                  on_progress=lambda done, total: None, manifest=None) -> int:
    """Автообновление скриптов/инструкций всех моделей (cars/), без
    files/usb_files (тяжёлые payload'ы, отдельный шаг перед установкой).
    manifest — если вызывающий уже скачал его сам (см. mobile_bridge.py:
    sync_cars — нужен ещё и для prune_removed_models ниже, незачем качать
    дважды), передаётся готовым; иначе качаем сами, как раньше."""
    if manifest is None:
        manifest = fetch_manifest(base_url)
    if manifest is None:
        log("Не удалось получить manifest.json с сервера — работаем с тем, что уже скачано локально.")
        return 0
    downloaded = sync_tree(base_url, "cars", cars_dir, manifest, log=log,
                           skip_dirs=("files", "usb_files"), no_recurse_dirs=("cars/_shared",),
                           on_progress=on_progress)
    for name in STARTUP_SHARED_FOLDERS:
        downloaded += sync_tree(base_url, f"cars/_shared/{name}", cars_dir / "_shared" / name, manifest, log=log)
    return downloaded


# Служебные подпапки cars/_shared для кнопок «Доп. действий» (не payload usb-этапов) — качаем при каждом запуске:
# у кнопки телефон может быть уже в Wi-Fi магнитолы без интернета. Лог №1985 (Dargo, 1.0.53): ключ «Работы в
# движении» не доходил — no_recurse_dirs пропускал его. Копия desktop app/content_sync.py:STARTUP_SHARED_FOLDERS.
STARTUP_SHARED_FOLDERS = ("motion_cert",)


_KNOWN_MODELS_FILENAME = "known_models.json"
_MODEL_MARKER = "_wizard_spec.json"  # см. desktop app/car_generator.py: SPEC_FILENAME


def _model_prefixes_from_manifest(manifest) -> set:
    suffix = f"/{_MODEL_MARKER}"
    return {path[:-len(suffix)] for path in manifest
            if path.startswith("cars/") and path.endswith(suffix)}


def _known_models_path(base_dir: Path) -> Path:
    return base_dir / _KNOWN_MODELS_FILENAME


def _load_known_models(base_dir: Path) -> set:
    try:
        data = json.loads(_known_models_path(base_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return set(data) if isinstance(data, list) else set()


def _save_known_models(base_dir: Path, prefixes) -> None:
    try:
        _known_models_path(base_dir).write_text(
            json.dumps(sorted(prefixes), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _prune_empty_ancestors(start: Path, stop_at: Path) -> None:
    stop_at = stop_at.resolve()
    current = start.resolve()
    while current != stop_at and stop_at in current.parents:
        try:
            if any(current.iterdir()):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent


def model_on_server(cars_dir: Path, model_dir: Path, manifest) -> bool:
    """Модель опубликована на сервере и не правится локально (_local_edit.json, как на ПК) — её файлы те же, что там."""
    if manifest is None or (model_dir / "_local_edit.json").exists():
        return False
    try:
        rel = "cars/" + model_dir.resolve().relative_to(cars_dir.resolve()).as_posix()
    except ValueError:
        return False
    return f"{rel}/{_MODEL_MARKER}" in manifest


def prune_model_to_server(cars_dir: Path, model_dir: Path, manifest, log=lambda m: None) -> list:
    """Сверка опубликованной модели с сервером при открытии (владелец, 2026-10-02: «при открытии программа должна
    всегда сверяться с сервером, удалять локально всякие старые апк, если они были скачаны в прошлой версии инструкции,
    и быть готовой загрузить новые; файлы не должны дублироваться»). Из files/ и usb_files/ убирается всё, чего на
    сервере нет (переименовано или убрано в админке), и то, что отличается от серверного (старая версия), — нужное
    скачается заново перед использованием. Копия desktop app/content_sync.py:prune_model_stale_files."""
    if not model_on_server(cars_dir, model_dir, manifest):
        return []
    remote_base = "cars/" + model_dir.resolve().relative_to(cars_dir.resolve()).as_posix()
    removed = []
    for subfolder in ("files", "usb_files"):
        root = model_dir / subfolder
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            # .part — идущая сейчас докачка
            if not path.is_file() or path.name.endswith(".part"):
                continue
            # Переподписанная копия (InstallEngine.kt: X_resigned.apk) живёт, пока жив свой исходный APK
            if path.name.endswith("_resigned.apk") and path.with_name(path.name[:-len("_resigned.apk")] + ".apk").is_file():
                continue
            rel = path.relative_to(model_dir).as_posix()
            item = manifest.get(f"{remote_base}/{rel}")
            if item is not None and local_copy_is_current(path, item):
                continue
            try:
                path.unlink()
            except OSError:
                continue
            removed.append(rel)
            _prune_empty_ancestors(path.parent, model_dir)
    if removed:
        names = ", ".join(rel.rsplit("/", 1)[-1] for rel in removed[:5])
        more = f" и ещё {len(removed) - 5}" if len(removed) > 5 else ""
        log(f"Модель сверена с сервером: убраны устаревшие файлы ({names}{more}) — нужные скачаются заново.")
        # Скачанная заранее модель — теперь «обновить», а не «офлайн» (как на ПК: content_sync.prune_model_stale_files)
        offline_pack.refresh_marker(manifest, cars_dir, model_dir)
    return removed


def prune_closed_stages(cars_dir: Path, manifest, log=lambda m: None) -> list:
    """Закрытые этапы (как ПК: app/content_sync.py:prune_closed_stages): доступ сняли (подписка, группа) или
    этапы открыли всем — скачанная прежде <модель>/_closed/ убирается. Без сети — не трогаем."""
    if manifest is None or not cars_dir.is_dir():
        return []
    removed = []
    for closed in sorted(cars_dir.rglob("_closed")):
        model_dir = closed.parent
        if not closed.is_dir() or "_shared" in closed.relative_to(cars_dir).parts or model_dir == cars_dir:
            continue
        if (model_dir / "_local_edit.json").exists():
            continue
        rel = "cars/" + closed.relative_to(cars_dir).as_posix()
        if any(path.startswith(rel + "/") for path in manifest):
            continue
        shutil.rmtree(closed, ignore_errors=True)
        removed.append(model_dir.relative_to(cars_dir).as_posix())
    if removed:
        log(f"Закрытые этапы больше не доступны — убраны: {', '.join(removed)}")
    return removed


EARLY_ACCESS_MARKER = "_early_access.json"  # то же имя в scanner.py
_OWN_SUBMISSION_MARKER = ".submission_status"  # своя заявка техника (auth_bridge.sync_my_cars) — не трогаем


def _early_marker_dirs(cars_dir: Path) -> list:
    return [marker.parent for pattern in (f"*/*/{EARLY_ACCESS_MARKER}", f"*/*/*/{EARLY_ACCESS_MARKER}")
            for marker in cars_dir.glob(pattern)]


def sync_early_access(base_url: str, cars_dir: Path, manifest, early=None, log=lambda m: None) -> None:
    """Порт desktop app/content_sync.py:sync_early_access — модели раннего доступа: подписчику
    отметка «ранний доступ», остальным витрина (version.json, logo, hero) под замком; файлы
    инструкции без подписки удаляются; отметки закончившегося раннего доступа снимаются."""
    if not base_url or manifest is None:
        return
    early = last_early_access() if early is None else early
    wanted = set()
    for path, entry in (early or {}).items():
        parts = [part for part in str(path).split("/") if part]
        if (not isinstance(entry, dict) or isinstance(entry.get("open_at"), bool)
                or not isinstance(entry.get("open_at"), (int, float)) or not 2 <= len(parts) <= 3
                or any(part in (".", "..") or part.startswith((".", "_")) for part in parts)):
            continue
        model_rel = "cars/" + "/".join(parts)
        teaser = {rel: meta for rel, meta in (entry.get("files") or {}).items()
                  if isinstance(meta, dict) and isinstance(meta.get("size"), int)
                  and rel.startswith(model_rel + "/") and ".." not in rel.split("/")}
        locked = not any(rel.startswith(model_rel + "/") and rel not in teaser for rel in manifest)
        model_dir = cars_dir.joinpath(*parts)
        teaser_local = {cars_dir / rel[len("cars/"):] for rel in teaser}
        if locked and model_dir.is_dir() and not (model_dir / _OWN_SUBMISSION_MARKER).exists():
            removed = 0
            for file in sorted(model_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if file.is_file() and file not in teaser_local and file.name != EARLY_ACCESS_MARKER:
                    file.unlink(missing_ok=True)
                    removed += 1
                elif file.is_dir():
                    try:
                        file.rmdir()
                    except OSError:
                        pass
            if removed:
                log(f"Ранний доступ без подписки — убираю файлы инструкции: {model_rel}")
        for rel, meta in teaser.items():
            local = cars_dir / rel[len("cars/"):]
            if _is_stale(local, meta):
                try:
                    download_file(base_url, rel, local, mtime=meta.get("mtime"))
                except (urllib.error.URLError, OSError) as exc:
                    log(f"Не удалось скачать {rel}: {exc}")
        model_dir.mkdir(parents=True, exist_ok=True)
        marker = {"open_at": float(entry["open_at"]), "locked": locked}
        marker_path = model_dir / EARLY_ACCESS_MARKER
        try:
            if not marker_path.exists() or json.loads(marker_path.read_text(encoding="utf-8")) != marker:
                marker_path.write_text(json.dumps(marker), encoding="utf-8")
        except (OSError, json.JSONDecodeError):
            marker_path.write_text(json.dumps(marker), encoding="utf-8")
        wanted.add(model_dir)
    for model_dir in _early_marker_dirs(cars_dir):
        if model_dir not in wanted and model_dir.is_dir():
            (model_dir / EARLY_ACCESS_MARKER).unlink(missing_ok=True)
            if not any((d / name).exists() for d in (model_dir, *model_dir.iterdir()) if d.is_dir()
                       for name in ("stages.py", "install.py")) and not (model_dir / _OWN_SUBMISSION_MARKER).exists():
                shutil.rmtree(model_dir, ignore_errors=True)
                _prune_empty_ancestors(model_dir.parent, cars_dir)


def prune_removed_models(base_dir: Path, cars_dir: Path, manifest, log=lambda m: None) -> list:
    """Порт desktop app/content_sync.py:prune_removed_models — sync_scripts
    выше только докачивает, сам никогда ничего не удаляет, поэтому модель,
    переименованную/убранную на сервере (см. car_generator.py:update_car),
    отдельно убираем здесь, иначе она вечно висела бы в списке марок на
    телефоне техника дублем со старым названием. Сравниваем со СНИМКОМ
    прошлого манифеста (base_dir/known_models.json), а не с текущим
    содержимым cars_dir — иначе под удаление попала бы и модель, которую
    техник только что создал локально и ещё не опубликовал. Первый запуск
    после обновления приложения (снимка ещё нет) ничего не удаляет, только
    заводит базовую линию."""
    if manifest is None:
        return []
    current = _model_prefixes_from_manifest(manifest)
    previous = _load_known_models(base_dir)
    removed = []
    if previous:
        for prefix in sorted(previous - current):
            model_dir = base_dir / prefix
            if not (model_dir / _MODEL_MARKER).exists():
                continue
            log(f"Модель больше не публикуется на сервере, убираю локальную копию: {prefix}")
            shutil.rmtree(model_dir, ignore_errors=True)
            _prune_empty_ancestors(model_dir.parent, cars_dir)
            removed.append(prefix)
    _save_known_models(base_dir, current)
    return removed


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
