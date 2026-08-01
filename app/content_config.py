"""Адрес своего сервера с cars/apk — настраивается вручную (замена
публичной папки Я.Диска, см. server/README.md).

Создайте файл server.json рядом с main.py/.exe:
    {"base_url": "https://ваш-домен/content"}

Структура на сервере (папки content/cars, content/apk) повторяет
структуру проекта:
    cars/<Марка>/<Модель>/install.py, instruction.html, files/..., usb_files/...
    apk/...

Если server.json нет или адрес не задан — вся синхронизация с сервером
просто молча пропускается, программа работает как обычно с локальными
файлами.
"""
import json
from pathlib import Path


def get_base_url(base_dir: Path) -> str | None:
    path = base_dir / "server.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    url = data.get("base_url") or None
    return url.rstrip("/") if url else None
