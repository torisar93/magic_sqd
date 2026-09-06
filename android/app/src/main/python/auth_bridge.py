"""Аккаунт техника на Android — вход/регистрация + подтягивание СВОИХ
заявок на модерации (см. app/auth_client.py на desktop — тот же протокол
/auth/*, server/backend.py). В отличие от desktop, тут нет ни редактора
машин, ни отдельного "_pending/" стейджинга — своя заявка распаковывается
СРАЗУ в cars_dir/<Марка>/<Модель>/, тем же путём, что читают
list_cars/select_model (см. mobile_bridge.py), никакого специального UI не
нужно: список моделей и так показывает статус из версии, лежащей внутри."""
import json
import shutil
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote


class ZipSlipError(RuntimeError):
    pass


def _request(base_url: str, method: str, path: str, body: dict | None = None,
             cookie: str = "") -> tuple[dict, list[str]]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(base_url + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            cookies = [v.split(";", 1)[0] for v in resp.headers.get_all("Set-Cookie") or []]
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {"error": str(exc)}
        return {"ok": False, "error": payload.get("error", str(exc))}, []
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "error": f"Не удалось связаться с сервером: {exc}"}, []
    return payload, cookies


def register(base_url: str, email: str, password: str) -> str:
    payload, _ = _request(base_url, "POST", "/auth/register", body={"email": email, "password": password})
    return json.dumps(payload)


def login(base_url: str, email: str, password: str) -> str:
    """{"ok": true, "email", "is_admin", "user_cookie"} / {"ok": false, "error"}
    — Android не трогает ADMIN_SESSION_COOKIE вовсе (нет admin-режима на
    телефоне), поэтому вторая cookie из ответа сервера здесь просто не
    нужна и игнорируется."""
    payload, cookies = _request(base_url, "POST", "/auth/login", body={"email": email, "password": password})
    if not payload.get("ok"):
        return json.dumps(payload)
    user_cookie = next((c for c in cookies if c.startswith("magicsqd_user_session=")), None)
    if not user_cookie:
        return json.dumps({"ok": False, "error": "Сервер не выдал сессию входа."})
    payload["user_cookie"] = user_cookie
    return json.dumps(payload)


def logout(base_url: str, user_cookie: str) -> str:
    payload, _ = _request(base_url, "POST", "/auth/logout", cookie=user_cookie)
    return json.dumps(payload)


def forgot_password(base_url: str, email: str) -> str:
    """Всегда молча успешна (см. _handle_auth_forgot_password на сервере —
    он не подтверждает/опровергает существование почты в базе), письмо
    реально уходит только если аккаунт есть."""
    payload, _ = _request(base_url, "POST", "/auth/forgot-password", body={"email": email})
    return json.dumps(payload)


def my_cars(base_url: str, user_cookie: str) -> str:
    payload, _ = _request(base_url, "GET", "/auth/my-cars", cookie=user_cookie)
    return json.dumps(payload)


def _safe_extract(zip_path: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = (dest / info.filename).resolve()
            if not str(target).startswith(str(dest_resolved)):
                raise ZipSlipError(f"Небезопасный путь в архиве заявки: {info.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as out:
                shutil.copyfileobj(src, out)


def sync_my_cars(base_url: str, user_cookie: str, cars_dir: str) -> str:
    """Скачивает и распаковывает КАЖДУЮ свою заявку на модерации прямо в
    cars_dir/<Марка>/<Модель>/ — возвращает {"ok": true, "synced": [брэнды/
    модели]} или {"ok": false, "error"}. Вызывается при старте (если есть
    сохранённая сессия) и сразу после входа (см. WebBridge.kt)."""
    payload, _ = _request(base_url, "GET", "/auth/my-cars", cookie=user_cookie)
    if not payload.get("ok"):
        return json.dumps(payload)
    cars_path = Path(cars_dir)
    synced = []
    for item in payload.get("items", []):
        brand, model = item.get("brand"), item.get("model")
        if not brand or not model:
            continue  # очень старая заявка без метаданных — как и на desktop
        name = item["name"]
        model_dir = cars_path / brand / model
        tmp_zip = cars_path / f".{Path(name).stem}.download.zip"
        conn_request = urllib.request.Request(
            f"{base_url}/auth/my-cars/download?name={quote(name)}", headers={"Cookie": user_cookie})
        try:
            with urllib.request.urlopen(conn_request, timeout=60) as resp:
                tmp_zip.write_bytes(resp.read())
            _safe_extract(tmp_zip, model_dir)
            synced.append({"brand": brand, "model": model})
        except (urllib.error.URLError, TimeoutError, OSError, zipfile.BadZipFile, ZipSlipError):
            continue  # эта заявка не подтянулась — не блокируем остальные
        finally:
            tmp_zip.unlink(missing_ok=True)
    return json.dumps({"ok": True, "synced": synced})
