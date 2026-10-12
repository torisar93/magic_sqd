"""Английская версия интерфейса (владелец, 12.10.2026): переводчик js/i18n.js со словарём js/i18n_en.js на обеих
платформах и настройка «Язык» (авто / русский / English). Сам перевод — tests/js/i18n.test.js."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.web.api.settings_api import SettingsApi

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "app/web/frontend"
ANDROID = ROOT / "android/app/src/main/assets"


@pytest.mark.parametrize("index", [DESKTOP / "index.html", ANDROID / "index.html"])
def test_translator_is_loaded_in_head_before_markup(index):
    # В <head>, до разметки: переводчик ловит узлы с самого начала (Android — ещё до первого кадра), словарь — раньше.
    html = index.read_text(encoding="utf-8")
    head = html[: html.index("</head>")]
    assert head.index('src="js/i18n_en.js"') < head.index('src="js/i18n.js"')


def test_language_preference_defaults_to_auto_and_keeps_valid_choice(tmp_path):
    api = SettingsApi(tmp_path, tmp_path / "cars", tmp_path / "apk")
    assert api.preferences()["ui_language"] == "auto"
    assert api.set_preferences({"ui_language": "en"})["ui_language"] == "en"
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["ui_language"] == "en"
    # Чужое значение не записывается, остальные настройки не трогаются.
    result = api.set_preferences({"ui_language": "de", "compact_log": False})
    assert result["ui_language"] == "en" and result["compact_log"] is False
    (tmp_path / "settings.json").write_text(json.dumps({"ui_language": 5}), encoding="utf-8")
    assert api.preferences()["ui_language"] == "auto"


def test_language_setting_is_offered_on_both_platforms():
    desktop = (DESKTOP / "js/components/settings.js").read_text(encoding="utf-8")
    assert "languageRow(preferences.ui_language" in desktop and "ui_language_effective" in desktop
    android = (ANDROID / "js/app.js").read_text(encoding="utf-8")
    assert '"data-preference": "ui_language"' in android and "ui_language_effective" in android
    # Мост Android: язык при загрузке страницы (i18n.js), в app_version и после смены настройки.
    bridge = (ROOT / "android/app/src/main/java/ru/magicsqd/mobile/WebBridge.kt").read_text(encoding="utf-8")
    assert '"ui_language" -> JSONObject().put("language", uiLanguage())' in bridge
    assert '.put("ui_language", uiLanguage())' in bridge and '"ui_language_effective"' in bridge
    assert "window.AndroidBridge.call(\"ui_language\"" in (DESKTOP / "js/i18n.js").read_text(encoding="utf-8")
