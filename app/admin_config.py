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
