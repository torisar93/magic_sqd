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

from .platform_paths import bundled_tools_root


def get_admin_base_url(base_dir: Path) -> str | None:
    # На macOS admin.json лежит в Contents/Resources/ (см. datas= в
    # magic_sqd_mac.spec), не рядом с исполняемым файлом — см.
    # app/content_config.py:_read_server_json за тем же паттерном.
    # admin_saved_login.json ниже — наоборот, пишем/читаем рядом с exe как и
    # раньше: это runtime-файл самой программы, а не файл из сборки.
    path = bundled_tools_root(base_dir) / "admin.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    url = data.get("base_url")
    return url.rstrip("/") if isinstance(url, str) and url else None


# -- след старого входа в админку по логину/паролю веб-кабинета ----------
# До единого входа (11.10) «Запомнить меня» хранило здесь логин и пароль веб-кабинета без шифрования.
# Теперь права администратора — только по аккаунту (app/web/api/auth_api.py), а файл стирается при каждом
# запуске (app/web/bridge.py: WebApi.__init__ → clear_saved_login).
_SAVED_LOGIN_FILE = "admin_saved_login.json"


def clear_saved_login(base_dir: Path) -> None:
    (base_dir / _SAVED_LOGIN_FILE).unlink(missing_ok=True)
