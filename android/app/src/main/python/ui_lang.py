"""Язык интерфейса программы для запросов к серверу (английская версия, с 1.1.4): письма подтверждения и сброса
пароля, обвязка ответа на обращение и ответы ИИ приходят на этом языке (server/backend.py: _request_lang). Язык ставит
мост (ПК — app/web/bridge.py, Android — WebBridge.kt через mobile_bridge) при запуске и при смене настройки «Язык».

Файл общий для ПК (app/ui_lang.py) и Android (android/app/src/main/python/ui_lang.py) — побайтно."""
from __future__ import annotations

import re

_language = "ru"
# Описание выпуска (сообщение тега → GitHub-релиз → version.json зеркала): английская часть — после строки «[en]».
_EN_MARKER = re.compile(r"^\s*\[en\]\s*$", re.MULTILINE | re.IGNORECASE)


def set_language(value: str) -> None:
    global _language
    _language = "en" if str(value or "").lower() == "en" else "ru"


def language() -> str:
    return _language


def header() -> dict:
    """{"X-UI-Language": "en"|"ru"} — в запросы к серверу."""
    return {"X-UI-Language": _language}


def localized_changelog(text: str) -> str:
    """Часть описания выпуска на языке программы: английская — после строки «[en]», русская — до неё. Без «[en]» —
    текст как есть (старые выпуски)."""
    text = str(text or "")
    parts = _EN_MARKER.split(text, maxsplit=1)
    if len(parts) < 2:
        return text.strip()
    russian, english = parts[0].strip(), parts[1].strip()
    return (english or russian) if _language == "en" else (russian or english)
