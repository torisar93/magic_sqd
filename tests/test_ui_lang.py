"""Язык программы в запросах к серверу (английская версия, с 1.1.4): письма, ответы ИИ, ответ на обращение
приходят на нём (server/backend.py: _request_lang). Модуль общий для ПК и Android."""
from pathlib import Path

from app import ui_lang

ROOT = Path(__file__).resolve().parents[1]


def test_copies_identical_and_header():
    assert (ROOT / "app/ui_lang.py").read_bytes() == (ROOT / "android/app/src/main/python/ui_lang.py").read_bytes()
    try:
        ui_lang.set_language("EN")
        assert ui_lang.header() == {"X-UI-Language": "en"}
        ui_lang.set_language("de")
        assert ui_lang.language() == "ru"
    finally:
        ui_lang.set_language("ru")


def test_requests_carry_the_language():
    for path in ("app/auth_client.py", "app/report_client.py", "app/chat_client.py", "app/web/api/ai_api.py",
                 "android/app/src/main/python/auth_bridge.py", "android/app/src/main/python/report_bridge.py",
                 "android/app/src/main/python/chat_bridge.py", "android/app/src/main/python/ai_bridge.py"):
        assert "ui_lang.header()" in (ROOT / path).read_text(encoding="utf-8"), path
    bridge = (ROOT / "android/app/src/main/java/ru/magicsqd/mobile/WebBridge.kt").read_text(encoding="utf-8")
    assert 'callAttr("set_ui_language", uiLanguage())' in bridge


def test_changelog_part_follows_the_language():
    text = "Что нового:\n- Английская версия\n\n[en]\nWhat's new:\n- English version"
    try:
        assert ui_lang.localized_changelog(text) == "Что нового:\n- Английская версия"
        ui_lang.set_language("en")
        assert ui_lang.localized_changelog(text) == "What's new:\n- English version"
        assert ui_lang.localized_changelog("Только русский") == "Только русский"  # старые выпуски
    finally:
        ui_lang.set_language("ru")
