""""Запомнить меня" для аккаунта техника (см. app/web/api/auth_api.py) —
в отличие от admin_saved_login.json (app/admin_config.py, хранит логин и
ПАРОЛЬ в открытом виде), тут хранится только уже выданный сервером
session-токен — логаут на сервере (или просто истёкший срок) делает файл
бесполезным сам по себе, пароль нигде на диске не лежит. admin_cookie —
если у аккаунта есть права администратора (см. set_user_is_admin на
сервере), заодно выданная при последнем входе ADMIN_SESSION_COOKIE; та
живёт всего 24 часа (SESSION_TTL_SECONDS на сервере), так что после
долгого перерыва она может быть уже недействительна — это нормально,
первый же вызов admin_client.py сам это обнаружит (401 -> clear_cached_
session) и админ-функции просто снова спрячутся до следующего явного
входа, без падений."""
from __future__ import annotations
import json
from pathlib import Path

_SAVED_SESSION_FILE = "auth_saved_session.json"


def load_saved_session(base_dir: Path) -> dict | None:
    path = base_dir / _SAVED_SESSION_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    email, user_cookie = data.get("email"), data.get("user_cookie")
    if not (isinstance(email, str) and isinstance(user_cookie, str) and email and user_cookie):
        return None
    return {"email": email, "user_cookie": user_cookie, "admin_cookie": data.get("admin_cookie") or None}


def save_saved_session(base_dir: Path, email: str, user_cookie: str, admin_cookie: str | None) -> None:
    path = base_dir / _SAVED_SESSION_FILE
    path.write_text(
        json.dumps({"email": email, "user_cookie": user_cookie, "admin_cookie": admin_cookie},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def clear_saved_session(base_dir: Path) -> None:
    (base_dir / _SAVED_SESSION_FILE).unlink(missing_ok=True)
