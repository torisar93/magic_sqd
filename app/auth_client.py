"""HTTP-клиент аккаунтов техников (регистрация/вход/свои заявки на
модерации, см. server/backend.py: протокол /auth/*). Только стандартная
библиотека (http.client) — тот же приём, что и app/admin_client.py:
никакого cookie jar, cookie-строка передаётся руками между вызовами.

Единый вход (11.10): сессия аккаунта (magicsqd_user_session) — единственная. У аккаунта с правами
администратора сервер принимает её и в /admin/api/* (см. BackendHandler._is_authenticated), поэтому
auth_api кладёт ЕЁ в кеш admin_client (set_cached_session) — отдельная админ-сессия и вход по
логину/паролю веб-кабинета программе больше не нужны. Ответы /auth/login и /auth/me передаются в
интерфейс целиком (_account) — новые поля аккаунта доходят до окна аккаунта без выпуска программы."""
from __future__ import annotations
import http.client
import json
from urllib.parse import quote, urlsplit


class AuthClientError(RuntimeError):
    def __init__(self, message: str = "", status: int | None = None):
        super().__init__(message)
        self.status = status  # HTTP-код ответа сервера (401 — вход истёк); None — до сервера не достучались


def _connect(base_url: str, timeout: int = 30):
    parts = urlsplit(base_url)
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    return conn_cls(parts.netloc, timeout=timeout)


def _request(base_url: str, method: str, path: str, *, body: dict | None = None,
             cookie: str | None = None) -> tuple[dict, list[str]]:
    """Возвращает (json-тело, список cookie-строк "имя=значение" из всех
    Set-Cookie заголовков ответа)."""
    conn = _connect(base_url)
    try:
        headers = {}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(data))
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, body=data, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        cookies = [v.split(";", 1)[0] for v in response.msg.get_all("Set-Cookie", [])]
        if response.status != 200:
            raise AuthClientError(payload.get("error") or raw.decode("utf-8", "replace"), status=response.status)
        return payload, cookies
    except (OSError, http.client.HTTPException) as exc:
        raise AuthClientError(f"Не удалось связаться с сервером: {exc}") from exc
    finally:
        conn.close()


def register(base_url: str, email: str, password: str) -> None:
    _request(base_url, "POST", "/auth/register", body={"email": email, "password": password})


def _account(payload: dict, email: str | None = None) -> dict:
    """Ответ сервера об аккаунте целиком (кроме ok): email, is_admin, subscriber, session_expires_at и всё, что
    сервер добавит позже, — интерфейс получает новые поля без нового выпуска программы."""
    data = {key: value for key, value in payload.items() if key != "ok"}
    data["email"] = payload.get("email") or email
    data["is_admin"] = bool(payload.get("is_admin"))
    data["subscriber"] = bool(payload.get("subscriber"))
    return data


def login(base_url: str, email: str, password: str) -> dict:
    """Ответ /auth/login (см. _account) + "user_cookie" — сессия аккаунта, она же для админки."""
    payload, cookies = _request(base_url, "POST", "/auth/login", body={"email": email, "password": password})
    user_cookie = next((c for c in cookies if c.startswith("magicsqd_user_session=")), None)
    if not user_cookie:
        raise AuthClientError("Сервер не выдал сессию входа.")
    return {**_account(payload, email), "user_cookie": user_cookie}


def logout(base_url: str, user_cookie: str) -> None:
    _request(base_url, "POST", "/auth/logout", cookie=user_cookie)


def change_password(base_url: str, user_cookie: str, current_password: str, new_password: str) -> str:
    """Возвращает НОВУЮ сессионную cookie — смена пароля на сервере
    инвалидирует все существующие сессии (см. set_user_password), включая
    ту, которой был сделан этот самый запрос, так что сервер сразу же
    выписывает свежую взамен (см. _handle_auth_change_password)."""
    _payload, cookies = _request(
        base_url, "POST", "/auth/change-password",
        body={"current_password": current_password, "new_password": new_password}, cookie=user_cookie)
    new_cookie = next((c for c in cookies if c.startswith("magicsqd_user_session=")), None)
    if not new_cookie:
        raise AuthClientError("Сервер не выдал новую сессию после смены пароля.")
    return new_cookie


def forgot_password(base_url: str, email: str) -> None:
    """Всегда молча успешна (см. _handle_auth_forgot_password — сервер не
    подтверждает/опровергает существование почты в базе), письмо реально
    уходит только если аккаунт есть."""
    _request(base_url, "POST", "/auth/forgot-password", body={"email": email})


def me(base_url: str, user_cookie: str) -> dict:
    """Ответ /auth/me (см. _account) — бросает AuthClientError (в т.ч. на 401), если сессия невалидна/истекла."""
    payload, _ = _request(base_url, "GET", "/auth/me", cookie=user_cookie)
    return _account(payload)


def my_cars(base_url: str, user_cookie: str, all_states: bool = False) -> list[dict]:
    """Свои заявки. all_states=True — вместе со статусом каждой (на модерации/
    одобрена/отклонена, поле "status"); сервер без этой возможности молча отдаёт
    только ожидающие — статус тогда считаем «на модерации» (см. вызывающего)."""
    path = "/auth/my-cars?all=1" if all_states else "/auth/my-cars"
    payload, _ = _request(base_url, "GET", path, cookie=user_cookie)
    return payload.get("items", [])


def download_my_car(base_url: str, user_cookie: str, name: str, dest_path) -> None:
    """Скачивает свою заявку (см. /auth/my-cars/download) в dest_path —
    тот же стриминг-приём, что и admin_client.download_submission, но с
    сессией техника вместо админской и без промежуточного .part-файла
    (заявки маленькие, в отличие от целых прошивок)."""
    conn = _connect(base_url, timeout=120)
    try:
        conn.putrequest("GET", f"/auth/my-cars/download?name={quote(name)}")
        conn.putheader("Cookie", user_cookie)
        conn.endheaders()
        response = conn.getresponse()
        raw_error = None
        if response.status != 200:
            raw_error = response.read()
        else:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(dest_path, "wb") as f:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
        if raw_error is not None:
            try:
                error = json.loads(raw_error).get("error", raw_error.decode("utf-8", "replace"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                error = raw_error.decode("utf-8", "replace")
            raise AuthClientError(f"Не удалось скачать {name}: {error}")
    except (OSError, http.client.HTTPException) as exc:
        raise AuthClientError(f"Не удалось связаться с сервером: {exc}") from exc
    finally:
        conn.close()
