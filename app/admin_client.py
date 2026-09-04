"""Загрузка локальных cars/ и apk/ на сервер из админ-сборки (см.
admin_main.py, app/admin_gui.py) — использует ровно тот же HTTP-протокол,
что и веб-админка (server/admin/index.html): POST /admin/login за cookie-
сессией, затем POST /admin/api/upload?target=cars|apk с телом .zip
(server/backend.py:_handle_admin_upload). Никакого отдельного эндпоинта не
понадобилось заводить.

Только стандартная библиотека (http.client, не urllib.request/requests) —
та же причина, что и в app/submit_client.py: стримим архив кусками с
сокета, гигабайтные cars/apk не должны разбухать в памяти целиком, и есть
возможность проверять отмену между кусками."""
from __future__ import annotations
import http.client
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote, urlsplit

from .content_sync import LOCAL_EDIT_MARKER_FILENAME as _LOCAL_EDIT_MARKER_FILENAME

CHUNK_SIZE = 1024 * 1024


class AdminClientError(RuntimeError):
    pass


class AdminUploadCancelled(RuntimeError):
    pass


# Кеш cookie-сессии в памяти процесса (не на диске — сессия и так живёт,
# пока запущена программа) — чтобы диалог "Добавить машину..." мог залить
# только что созданную модель на сервер сразу по кнопке "Создать" (см.
# add_car_dialog.py), не спрашивая логин/пароль на КАЖДОЕ сохранение,
# только один раз за запуск (или заново, если сервер отверг сессию как
# истёкшую — см. clear_cached_session).
_session_cache: dict[str, str] = {}


def get_cached_session(base_url: str) -> str | None:
    return _session_cache.get(base_url)


def set_cached_session(base_url: str, cookie: str) -> None:
    _session_cache[base_url] = cookie


def clear_cached_session(base_url: str) -> None:
    _session_cache.pop(base_url, None)


def _connect(base_url: str):
    parts = urlsplit(base_url)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    return conn_cls(parts.netloc, timeout=120)


def login(base_url: str, username: str, password: str) -> str:
    """POST /admin/login — возвращает cookie-заголовок (magicsqd_admin_
    session=...) для последующих upload_dir(). Бросает AdminClientError с
    понятным пользователю текстом при неверном логине/пароле или недоступном
    сервере."""
    conn = _connect(base_url)
    try:
        body = json.dumps({"username": username, "password": password}).encode("utf-8")
        conn.request("POST", "/admin/login", body=body,
                      headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
        response = conn.getresponse()
        raw = response.read()
        if response.status != 200:
            try:
                error = json.loads(raw).get("error", raw.decode("utf-8", "replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                error = raw.decode("utf-8", "replace")
            raise AdminClientError(f"Не удалось войти: {error}")
        cookie = response.getheader("Set-Cookie")
        if not cookie:
            raise AdminClientError("Сервер не выдал сессию входа.")
        return cookie.split(";", 1)[0]  # только имя=значение, без Path/HttpOnly/...
    except (OSError, http.client.HTTPException) as exc:
        raise AdminClientError(f"Не удалось связаться с сервером: {exc}") from exc
    finally:
        conn.close()


def upload_dir(base_url: str, session_cookie: str, target: str, source_dir: Path,
                log=lambda m: None, check_cancelled=lambda: None) -> int:
    """Архивирует СОДЕРЖИМОЕ source_dir (не саму папку — на сервере .zip
    распаковывается прямо в content/<target>/, поэтому корень архива должен
    быть тем же, что и корень cars/apk) и заливает через POST /admin/api/
    upload?target=cars|apk. Возвращает число файлов, которые сервер
    распаковал (для лога)."""
    if target not in ("cars", "apk"):
        raise AdminClientError(f"Неизвестная цель загрузки: {target}")

    def build_archive(tmp: Path) -> Path:
        log(f"Архивирую {target}/...")
        archive_base = tmp / target
        try:
            return Path(shutil.make_archive(str(archive_base), "zip", root_dir=source_dir))
        except OSError as exc:
            raise AdminClientError(f"Не удалось собрать архив {target}/: {exc}") from exc

    return _build_and_send(base_url, session_cookie, target, build_archive, log, check_cancelled)


def upload_model(base_url: str, session_cookie: str, cars_dir: Path, model_dir: Path,
                  extra_dirs=(), log=lambda m: None, check_cancelled=lambda: None) -> int:
    """Заливает ОДНУ модель (model_dir — cars_dir/<Марка>/<Модель>/... или
    .../<Модификация>/), а не весь cars/ — для автоматической выгрузки сразу
    по кнопке "Создать"/"Сохранить" в мастере (см. add_car_dialog.py), без
    отдельного похода в "Выгрузить на сервер...". В отличие от upload_dir
    (архивирует содержимое source_dir как есть) здесь имена файлов внутри
    архива — путь ОТНОСИТЕЛЬНО cars_dir (например "Chery/Tiggo 7/
    install.py"), чтобы на сервере файлы легли туда же, где и обычно, а не
    прямо в корень content/cars/. extra_dirs — дополнительные папки ВНУТРИ
    cars_dir (например cars/_shared/<name>/ — общий набор файлов USB-этапа,
    см. StepSpec.usb_shared_folder), которые физически лежат вне model_dir,
    но должны попасть на сервер вместе с моделью, иначе content_sync.py на
    других машинах не найдёт их вовсе."""

    def build_archive(tmp: Path) -> Path:
        log(f"Архивирую {model_dir.relative_to(cars_dir)}...")
        archive_path = tmp / "model.zip"
        try:
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file in model_dir.rglob("*"):
                    # _local_edit.json — чисто локальная пометка "эта копия
                    # разошлась с сервером" (см. content_sync.py:
                    # mark_local_edit), server её вообще не должен видеть.
                    if file.is_file() and file.name != _LOCAL_EDIT_MARKER_FILENAME:
                        zf.write(file, file.relative_to(cars_dir))
                for extra_dir in extra_dirs:
                    if not extra_dir.is_dir():
                        continue
                    for file in extra_dir.rglob("*"):
                        if file.is_file():
                            zf.write(file, file.relative_to(cars_dir))
        except OSError as exc:
            raise AdminClientError(f"Не удалось собрать архив: {exc}") from exc
        return archive_path

    return _build_and_send(base_url, session_cookie, "cars", build_archive, log, check_cancelled)


def upload_model_as(base_url: str, session_cookie: str, cars_dir: Path, model_dir: Path,
                     brand: str, model: str, modification: str = "",
                     extra_dirs=(), log=lambda m: None, check_cancelled=lambda: None) -> int:
    """Как upload_model, но путь публикации (brand/model[/modification])
    задан явно, а не выводится из расположения model_dir под cars_dir —
    нужно для публикации заявки клиента (см. app/web/api/submissions_api.py:
    publish), чья застейдженная копия лежит в base_dir/_pending/<...>/, а не
    внутри cars_dir (см. app/pending_submissions.py). extra_dirs по-прежнему
    разрешаются относительно cars_dir — тот же смысл, что и в upload_model
    (общие cars/_shared/<name>/ публикующего админа)."""
    dest_root = f"{brand}/{model}/{modification}" if modification else f"{brand}/{model}"

    def build_archive(tmp: Path) -> Path:
        log(f"Архивирую {dest_root}...")
        archive_path = tmp / "model.zip"
        try:
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file in model_dir.rglob("*"):
                    if file.is_file() and file.name != _LOCAL_EDIT_MARKER_FILENAME:
                        zf.write(file, f"{dest_root}/{file.relative_to(model_dir)}")
                for extra_dir in extra_dirs:
                    if not extra_dir.is_dir():
                        continue
                    for file in extra_dir.rglob("*"):
                        if file.is_file():
                            zf.write(file, file.relative_to(cars_dir))
        except OSError as exc:
            raise AdminClientError(f"Не удалось собрать архив: {exc}") from exc
        return archive_path

    return _build_and_send(base_url, session_cookie, "cars", build_archive, log, check_cancelled)


def _build_and_send(base_url: str, session_cookie: str, target: str, build_archive,
                     log, check_cancelled) -> int:
    """Общая часть upload_dir/upload_model — build_archive(tmp_dir) -> Path
    собирает .zip самостоятельно (по-разному для целой папки и для одной
    модели), дальше отправка одинаковая."""
    with tempfile.TemporaryDirectory() as tmp:
        archive_path = build_archive(Path(tmp))

        size = archive_path.stat().st_size
        log(f"Отправляю ({size / (1024 * 1024):.1f} МБ)...")

        parts = urlsplit(base_url)
        conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(parts.netloc, timeout=600)
        try:
            conn.putrequest("POST", f"/admin/api/upload?target={target}")
            conn.putheader("Cookie", session_cookie)
            conn.putheader("Content-Length", str(size))
            conn.putheader("Content-Type", "application/zip")
            conn.endheaders()

            with open(archive_path, "rb") as f:
                while True:
                    check_cancelled()
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    conn.send(chunk)

            response = conn.getresponse()
            raw = response.read()
            if response.status == 401:
                raise AdminClientError("Сессия входа истекла — войдите заново.")
            if response.status != 200:
                try:
                    error = json.loads(raw).get("error", raw.decode("utf-8", "replace"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    error = raw.decode("utf-8", "replace")
                raise AdminClientError(f"Сервер отклонил {target}/ ({response.status}): {error}")
            try:
                count = json.loads(raw).get("files", 0)
            except (json.JSONDecodeError, UnicodeDecodeError):
                count = 0
            log(f"{target}/ загружено: {count} файлов.")
            return count
        except (OSError, http.client.HTTPException) as exc:
            raise AdminClientError(f"Не удалось связаться с сервером: {exc}") from exc
        finally:
            conn.close()


def _request(base_url: str, session_cookie: str, method: str, path: str) -> dict:
    """GET/DELETE без тела — общая часть list_cars_path/delete_cars_path
    (см. server/backend.py: /admin/api/cars/list, /admin/api/cars — то же
    HTTP-соединение и разбор ответа, что и _build_and_send, но без отправки
    файла)."""
    parts = urlsplit(base_url)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parts.netloc, timeout=30)
    try:
        conn.putrequest(method, path)
        conn.putheader("Cookie", session_cookie)
        conn.endheaders()
        response = conn.getresponse()
        raw = response.read()
        if response.status == 401:
            raise AdminClientError("Сессия входа истекла — войдите заново.")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            data = {}
        if response.status != 200:
            raise AdminClientError(f"Сервер отклонил запрос ({response.status}): "
                                    f"{data.get('error', raw.decode('utf-8', 'replace'))}")
        return data
    except (OSError, http.client.HTTPException) as exc:
        raise AdminClientError(f"Не удалось связаться с сервером: {exc}") from exc
    finally:
        conn.close()


def list_cars_path(base_url: str, session_cookie: str, rel_path: str) -> list[dict]:
    """Содержимое content/cars/<rel_path> на сервере — папки моделей,
    _shared/, отдельные файлы внутри них (см. server/backend.py:
    list_cars_path). rel_path пустой — корень cars/ (марки + _shared)."""
    data = _request(base_url, session_cookie, "GET",
                     f"/admin/api/cars/list?path={quote(rel_path)}")
    return data.get("items", [])


def delete_cars_path(base_url: str, session_cookie: str, rel_path: str) -> None:
    """Удаляет файл или папку (рекурсивно) content/cars/<rel_path> на
    сервере — единственный способ убрать уже опубликованное: upload_dir/
    upload_model льют только слиянием и никогда сами не удаляют лишнее."""
    _request(base_url, session_cookie, "DELETE", f"/admin/api/cars?path={quote(rel_path)}")


# -- единый файловый менеджер (см. server/backend.py: GET/DELETE
# /admin/api/browse, POST /admin/api/move) — то же самое, что list_cars_path/
# delete_cars_path выше, но параметризовано по дереву (cars или apk) вместо
# отдельных ручек только под cars/; move — новая операция, которой раньше
# не было вовсе (upload_dir/upload_model только сливают файлы, никогда не
# переименовывают/переносят), см. app/web/api/admin_api.py: browse_tree/
# move_path/delete_tree_path.

def browse_tree(base_url: str, session_cookie: str, root: str, rel_path: str) -> list[dict]:
    """Содержимое одной папки под деревом root ("cars" или "apk") — как
    list_cars_path, только на оба дерева."""
    data = _request(base_url, session_cookie, "GET",
                     f"/admin/api/browse?root={quote(root)}&path={quote(rel_path)}")
    return data.get("items", [])


def delete_tree_path(base_url: str, session_cookie: str, root: str, rel_path: str) -> None:
    """Удаляет файл или папку (рекурсивно) под деревом root — как
    delete_cars_path, только на оба дерева (cars и apk)."""
    _request(base_url, session_cookie, "DELETE",
              f"/admin/api/browse?root={quote(root)}&path={quote(rel_path)}")


def move_path(base_url: str, session_cookie: str, root: str, from_rel: str, to_rel: str) -> None:
    """Переносит/переименовывает файл или папку ВНУТРИ одного дерева (root)
    — POST /admin/api/move, {"root", "from", "to"}. Без тихой перезаписи:
    сервер отказывает, если по to_rel уже что-то есть."""
    body = json.dumps({"root": root, "from": from_rel, "to": to_rel}).encode("utf-8")
    parts = urlsplit(base_url)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parts.netloc, timeout=30)
    try:
        conn.putrequest("POST", "/admin/api/move")
        conn.putheader("Cookie", session_cookie)
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        conn.send(body)
        response = conn.getresponse()
        raw = response.read()
        if response.status == 401:
            raise AdminClientError("Сессия входа истекла — войдите заново.")
        if response.status != 200:
            try:
                error = json.loads(raw).get("error", raw.decode("utf-8", "replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                error = raw.decode("utf-8", "replace")
            raise AdminClientError(f"Сервер отклонил перенос {from_rel} -> {to_rel} ({response.status}): {error}")
    except (OSError, http.client.HTTPException) as exc:
        raise AdminClientError(f"Не удалось связаться с сервером: {exc}") from exc
    finally:
        conn.close()


def copy_path(base_url: str, session_cookie: str, root: str, from_rel: str, to_rel: str) -> None:
    """Копирует файл или папку ВНУТРИ одного дерева (root) — POST
    /admin/api/copy, тот же формат тела и та же защита от тихой
    перезаписи, что и у move_path выше, но исходник остаётся на месте."""
    body = json.dumps({"root": root, "from": from_rel, "to": to_rel}).encode("utf-8")
    parts = urlsplit(base_url)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parts.netloc, timeout=30)
    try:
        conn.putrequest("POST", "/admin/api/copy")
        conn.putheader("Cookie", session_cookie)
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        conn.send(body)
        response = conn.getresponse()
        raw = response.read()
        if response.status == 401:
            raise AdminClientError("Сессия входа истекла — войдите заново.")
        if response.status != 200:
            try:
                error = json.loads(raw).get("error", raw.decode("utf-8", "replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                error = raw.decode("utf-8", "replace")
            raise AdminClientError(f"Сервер отклонил копирование {from_rel} -> {to_rel} ({response.status}): {error}")
    except (OSError, http.client.HTTPException) as exc:
        raise AdminClientError(f"Не удалось связаться с сервером: {exc}") from exc
    finally:
        conn.close()


def create_folder(base_url: str, session_cookie: str, root: str, rel_path: str) -> None:
    """Создаёт пустую папку под деревом root — POST /admin/api/mkdir,
    {"root", "path"}."""
    body = json.dumps({"root": root, "path": rel_path}).encode("utf-8")
    parts = urlsplit(base_url)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parts.netloc, timeout=30)
    try:
        conn.putrequest("POST", "/admin/api/mkdir")
        conn.putheader("Cookie", session_cookie)
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        conn.send(body)
        response = conn.getresponse()
        raw = response.read()
        if response.status == 401:
            raise AdminClientError("Сессия входа истекла — войдите заново.")
        if response.status != 200:
            try:
                error = json.loads(raw).get("error", raw.decode("utf-8", "replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                error = raw.decode("utf-8", "replace")
            raise AdminClientError(f"Сервер отклонил создание папки {rel_path} ({response.status}): {error}")
    except (OSError, http.client.HTTPException) as exc:
        raise AdminClientError(f"Не удалось связаться с сервером: {exc}") from exc
    finally:
        conn.close()


# -- очередь заявок клиентов (см. app/web/api/submissions_api.py) -----------
# Тот же протокол, которым уже пользуется веб-админка (server/admin/
# index.html) — см. server/backend.py: GET /admin/api/submissions[/peek],
# GET /admin/download/<имя>, DELETE /admin/api/submissions. Публикация
# заявки НЕ использует отдельный POST /admin/api/submissions/approve (он
# распаковывает ИСХОДНЫЙ присланный .zip как есть, без учёта правок,
# внесённых админом через визуальный редактор) — вместо этого
# submissions_api.py заливает текущее (возможно, отредактированное)
# содержимое застейдженной копии через upload_model_as() выше и только
# затем убирает исходную заявку через delete_submission().

def list_submissions(base_url: str, session_cookie: str) -> list[dict]:
    """Очередь заявок — то же самое, что видит веб-админка (см.
    server/backend.py:list_submissions): {name, stamp, label, size, brand,
    model, modification, client_id}, brand/model — None у очень старых
    заявок без сайдкар-метаданных."""
    data = _request(base_url, session_cookie, "GET", "/admin/api/submissions")
    return data.get("items", [])


def peek_submission(base_url: str, session_cookie: str, name: str) -> list[dict]:
    """Список файлов внутри заявки без скачивания (см. server/backend.py:
    _handle_submission_peek) — для быстрого превью до полного стейджинга."""
    data = _request(base_url, session_cookie, "GET",
                     f"/admin/api/submissions/peek?name={quote(name)}")
    return data.get("items", [])


def delete_submission(base_url: str, session_cookie: str, name: str) -> None:
    """Отклонить заявку — насовсем удаляет .zip и метаданные на сервере
    (см. server/backend.py:_handle_delete_submission); сервер не хранит ни
    причину отклонения, ни архивную копию — это окончательно."""
    _request(base_url, session_cookie, "DELETE", f"/admin/api/submissions?name={quote(name)}")


def download_submission(base_url: str, session_cookie: str, name: str, dest_path: Path,
                         log=lambda m: None, check_cancelled=lambda: None) -> None:
    """Скачивает заявку (GET /admin/download/<имя>) в dest_path кусками, для
    последующей распаковки (см. app/pending_submissions.py:stage) — тот же
    протокол, что и upload_model/_build_and_send, только в обратную сторону
    (читаем из сокета в файл, а не наоборот)."""
    parts = urlsplit(base_url)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parts.netloc, timeout=120)
    try:
        conn.putrequest("GET", f"/admin/download/{quote(name)}")
        conn.putheader("Cookie", session_cookie)
        conn.endheaders()
        response = conn.getresponse()
        if response.status == 401:
            response.read()
            raise AdminClientError("Сессия входа истекла — войдите заново.")
        if response.status != 200:
            raw = response.read()
            try:
                error = json.loads(raw).get("error", raw.decode("utf-8", "replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                error = raw.decode("utf-8", "replace")
            raise AdminClientError(f"Сервер отклонил скачивание {name} ({response.status}): {error}")
        size = int(response.getheader("Content-Length") or 0)
        log(f"Скачиваю {name} ({size / (1024 * 1024):.1f} МБ)...")
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
        with open(tmp_path, "wb") as f:
            while True:
                check_cancelled()
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                f.write(chunk)
        tmp_path.replace(dest_path)
    except (OSError, http.client.HTTPException) as exc:
        raise AdminClientError(f"Не удалось связаться с сервером: {exc}") from exc
    finally:
        conn.close()


def upload_single_apk(base_url: str, session_cookie: str, category: str, filename: str,
                       file_path: Path, log=lambda m: None, check_cancelled=lambda: None) -> None:
    """Заливает ОДИН .apk точечно (см. server/backend.py:
    POST /admin/api/apks/upload?category=&filename=) — в отличие от
    upload_dir(apk/...), который каждый раз архивирует и заново заливает
    ВСЮ общую библиотеку целиком, это лишь один файл независимо от того,
    сколько APK уже накопилось на сервере (см. AdminApi.add_apk — раньше
    каждое добавление одного APK через "Добавить APK..." перезаливало всю
    apk/, и с ростом библиотеки каждая следующая загрузка становилась
    тяжелее предыдущей)."""
    size = file_path.stat().st_size
    log(f"Отправляю {filename} ({size / (1024 * 1024):.1f} МБ)...")
    parts = urlsplit(base_url)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parts.netloc, timeout=600)
    try:
        path = f"/admin/api/apks/upload?category={quote(category)}&filename={quote(filename)}"
        conn.putrequest("POST", path)
        conn.putheader("Cookie", session_cookie)
        conn.putheader("Content-Length", str(size))
        conn.putheader("Content-Type", "application/vnd.android.package-archive")
        conn.endheaders()
        with open(file_path, "rb") as f:
            while True:
                check_cancelled()
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                conn.send(chunk)
        response = conn.getresponse()
        raw = response.read()
        if response.status == 401:
            raise AdminClientError("Сессия входа истекла — войдите заново.")
        if response.status != 200:
            try:
                error = json.loads(raw).get("error", raw.decode("utf-8", "replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                error = raw.decode("utf-8", "replace")
            raise AdminClientError(f"Сервер отклонил {filename} ({response.status}): {error}")
    except (OSError, http.client.HTTPException) as exc:
        raise AdminClientError(f"Не удалось связаться с сервером: {exc}") from exc
    finally:
        conn.close()


def edit_apk_metadata(base_url: str, session_cookie: str, category: str, filename: str,
                       name: str, description: str) -> None:
    """Пишет/переносит категорию и <файл>.json с именем/описанием (см.
    server/backend.py: POST /admin/api/apks/edit?category=&filename=) —
    вызывается сразу после upload_single_apk, чтобы "красивое" имя,
    введённое в "Добавить APK...", попало на сервер вместе с файлом."""
    body = json.dumps({"category": category, "name": name, "description": description}).encode("utf-8")
    parts = urlsplit(base_url)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(parts.netloc, timeout=30)
    try:
        path = f"/admin/api/apks/edit?category={quote(category)}&filename={quote(filename)}"
        conn.putrequest("POST", path)
        conn.putheader("Cookie", session_cookie)
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders()
        conn.send(body)
        response = conn.getresponse()
        raw = response.read()
        if response.status == 401:
            raise AdminClientError("Сессия входа истекла — войдите заново.")
        if response.status != 200:
            try:
                error = json.loads(raw).get("error", raw.decode("utf-8", "replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                error = raw.decode("utf-8", "replace")
            raise AdminClientError(f"Сервер отклонил метаданные {filename} ({response.status}): {error}")
    except (OSError, http.client.HTTPException) as exc:
        raise AdminClientError(f"Не удалось связаться с сервером: {exc}") from exc
    finally:
        conn.close()
