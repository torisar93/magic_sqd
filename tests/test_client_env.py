"""Признаки системы для правила доступа по региону (app/client_env.py, с 1.1.4): языки, раскладки, часовой пояс —
и выбор языка интерфейса программы."""
from __future__ import annotations

from app import client_env

MAC_LANGUAGES = '(\n    "ru-RU",\n    "en-RU"\n)\n'
MAC_SOURCES = '''(
        {
        InputSourceKind = "Keyboard Layout";
        "KeyboardLayout ID" = 252;
        "KeyboardLayout Name" = ABC;
    },
        {
        InputSourceKind = "Keyboard Layout";
        "KeyboardLayout ID" = 19458;
        "KeyboardLayout Name" = RussianWin;
    },
        {
        InputSourceKind = "Keyboard Layout";
        "KeyboardLayout Name" = "Arabic - QWERTY";
    }
)'''


def test_mac_outputs_are_parsed():
    assert client_env.parse_mac_list(MAC_LANGUAGES) == ["ru-RU", "en-RU"]
    assert client_env.parse_mac_list("(\n    en\n)") == ["en"]
    assert client_env.parse_mac_layouts(MAC_SOURCES) == ["en", "ru", "ar"]


def test_collect_has_the_expected_shape():
    env = client_env.collect()
    assert set(env) == {"ui", "region", "kbd", "tz", "os"}
    assert isinstance(env["tz"], int) and -14 * 60 <= env["tz"] <= 14 * 60
    assert env["os"] in ("windows", "macos", "linux")


def test_ui_language_russian_if_any_of_our_languages():
    assert client_env.ui_language({"ui": ["en-US"], "region": "en-US", "kbd": ["en-US", "ru-RU"]}) == "ru"
    assert client_env.ui_language({"ui": ["kk-KZ"], "region": "", "kbd": []}) == "ru"
    assert client_env.ui_language({"ui": ["fr-FR"], "region": "fr-DZ", "kbd": ["fr-FR", "ar-DZ"]}) == "en"
    assert client_env.ui_language({"ui": [], "region": "", "kbd": []}) == "en"
