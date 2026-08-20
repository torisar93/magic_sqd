"""Адрес своего сервера с cars/apk — настраивается вручную (замена
публичной папки Я.Диска, см. server/README.md).

Создайте файл server.json рядом с main.py/.exe:
    {"base_url": "https://ваш-домен/content",
     "download_base_url": "https://ваш-домен/download"}

Структура на сервере (папки content/cars, content/apk) повторяет
структуру проекта:
    cars/<Марка>/<Модель>/install.py, instruction.html, files/..., usb_files/...
    apk/...

download_base_url — необязательный, отдельный от base_url (та же папка
site/download/, что и старая раздача MagicSQD_Setup.exe вручную через
админку, см. server/README.md §8-9) — оттуда app/web/api/update_api.py
берёт version.json и сам инсталлятор для автообновления программы. Не
задан — эта проверка молча пропускается (остаётся только GitHub Releases).

Если server.json нет или адрес не задан — вся синхронизация с сервером
просто молча пропускается, программа работает как обычно с локальными
файлами.
"""
from __future__ import annotations
import json
from pathlib import Path


def _read_server_json(base_dir: Path) -> dict:
    path = base_dir / "server.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def get_base_url(base_dir: Path) -> str | None:
    url = _read_server_json(base_dir).get("base_url") or None
    return url.rstrip("/") if url else None


def get_download_base_url(base_dir: Path) -> str | None:
    url = _read_server_json(base_dir).get("download_base_url") or None
    return url.rstrip("/") if url else None
