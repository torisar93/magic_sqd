"""Таблица «магнитола → модель» для подсказки «Похоже, это другая машина» (js/components/device_hint.js; владелец,
2026-09-30). Сервер пересчитывает её по логам установок (server/device_models.py) и раздаёт как
content/device_models.json — тем же `location /content/`, что supporters.json. Программа скачивает её при запуске и
держит копию рядом с собой: при подключении по Wi-Fi ADB компьютер обычно в сети магнитолы, без интернета.
Любой сбой (нет сети, старый сервер без файла, битый JSON) — остаётся прежняя копия или пусто: подсказки просто нет."""
from __future__ import annotations
import json
import urllib.error
import urllib.request
from pathlib import Path

from .content_config import get_base_url

CACHE_NAME = "device_models.json"


def _valid(data) -> bool:
    return isinstance(data, dict) and isinstance(data.get("devices"), dict)


def refresh(base_dir: Path, timeout: float = 10) -> bool:
    """Скачать свежую таблицу и заменить копию. True — обновлено."""
    base_url = get_base_url(base_dir)
    if not base_url:
        return False
    try:
        with urllib.request.urlopen(f"{base_url}/{CACHE_NAME}", timeout=timeout) as resp:
            raw = resp.read()
        if not _valid(json.loads(raw.decode("utf-8"))):
            return False
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False
    tmp = Path(base_dir) / f".{CACHE_NAME}.part"
    try:
        tmp.write_bytes(raw)
        tmp.replace(Path(base_dir) / CACHE_NAME)
    except OSError:
        return False
    return True


def load(base_dir: Path) -> dict:
    """Копия таблицы; нет или битая — {}."""
    try:
        data = json.loads((Path(base_dir) / CACHE_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if _valid(data) else {}
