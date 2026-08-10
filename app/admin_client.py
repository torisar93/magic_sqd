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
import http.client
import json
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import quote, urlsplit

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
                    if file.is_file():
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
