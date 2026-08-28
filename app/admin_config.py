"""Настройка админ-сборки (см. admin_main.py) — admin.json рядом с .exe/
main.py, тот же паттерн, что и server.json/submit.json (app/content_config.py,
app/submit_config.py), но своё поле: голый адрес сервера (схема+хост, без
пути), потому что админ-клиент сам обращается к нескольким путям на этом
хосте (/admin/login, /admin/api/upload) — в отличие от submit.json, где
submit_url указывает сразу на один конкретный путь.

    {"base_url": "https://magicsqd.ru"}

Если файла нет — загрузка на сервер в админ-сборке просто недоступна (кнопка
неактивна), остальная часть программы (просмотр/редактирование локальных
cars/apk) работает как обычно."""
from __future__ import annotations
import json
from pathlib import Path


def get_admin_base_url(base_dir: Path) -> str | None:
    path = base_dir / "admin.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    url = data.get("base_url")
    return url.rstrip("/") if isinstance(url, str) and url else None


# -- "Запомнить меня" при разблокировке функций администратора ------------
# (см. app/web/api/admin_api.py:try_saved_login/login_only, вызывается при
# каждом старте — см. app/web/bridge.py: WebApi.__init__). Сессия входа на
# сервере живёт всего 24
# часа и не переживает рестарт бэкенда (server/backend.py: _SESSIONS только
# в памяти процесса) — просто запомнить cookie не даёт длительного "не
# спрашивать пароль", поэтому храним сам логин/пароль. Без шифрования — тот
# же уровень доверия, что и у keystore.properties в этом проекте: файл живёт
# только на машине техника рядом с .exe, и она и так уже держит adbkey,
# admin.json и т.п.
_SAVED_LOGIN_FILE = "admin_saved_login.json"


def load_saved_login(base_dir: Path) -> dict | None:
    path = base_dir / _SAVED_LOGIN_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    username, password = data.get("username"), data.get("password")
    if not (isinstance(username, str) and isinstance(password, str) and username and password):
        return None
    return {"username": username, "password": password}


def save_saved_login(base_dir: Path, username: str, password: str) -> None:
    path = base_dir / _SAVED_LOGIN_FILE
    path.write_text(
        json.dumps({"username": username, "password": password}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def clear_saved_login(base_dir: Path) -> None:
    (base_dir / _SAVED_LOGIN_FILE).unlink(missing_ok=True)
