"""Список «Спасибо вам» для окна «Готово!» — content/supporters.json (его при каждой правке в таблице
пользователей админки пишет сервер, см. server/backend.py: build_supporters). Раздаётся обычным
статическим `location /content/`, отдельного эндпоинта нет. Только имена и цвет карточки — без почт и
сумм. Любой сбой (нет сети, старый сервер без файла, битый JSON) — None: блок просто не показывается."""
from __future__ import annotations
import json
import urllib.error
import urllib.request
from pathlib import Path

from .content_config import get_base_url

MAX_PEOPLE = 500
MAX_NAME = 40


def fetch_supporters(base_dir: Path, timeout: float = 8) -> dict | None:
    base_url = get_base_url(base_dir)
    if not base_url:
        return None
    try:
        with urllib.request.urlopen(f"{base_url}/supporters.json", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    return _clean(data)


def _clean(data) -> dict | None:
    if not isinstance(data, dict) or not isinstance(data.get("people"), list):
        return None
    people = []
    for item in data["people"][:MAX_PEOPLE]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:MAX_NAME]
        if not name:
            continue
        people.append({"name": name, "kind": "sub" if item.get("kind") == "sub" else "don",
                       "top": item.get("top") is True})
    return {"people": people}
