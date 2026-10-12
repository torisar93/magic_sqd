"""Запуск программы на ПК: мост WebApi собирается целиком (main_web.py → WebApi(base_dir)). 12.10.2026 в 1.1.4 язык
интерфейса читался в __init__ раньше, чем создавались настройки, — программа падала при запуске у всех
(AttributeError: 'WebApi' object has no attribute '_settings'), а ни один тест WebApi целиком не собирал."""
from __future__ import annotations

import sys
import types

from app import ui_lang

# Мост тянет pywebview (диалоги файлов в car_editor_api, закрытие окна в update_api), которого нет в
# requirements-dev.txt (CI) — при сборке моста он не вызывается. Заглушку убираем сразу после импорта, как в
# tests/test_editor_model_access.py.
_WEBVIEW_STUBBED = "webview" not in sys.modules
if _WEBVIEW_STUBBED:
    sys.modules["webview"] = types.ModuleType("webview")
from app.web.bridge import WebApi  # noqa: E402
if _WEBVIEW_STUBBED:
    del sys.modules["webview"]


def test_webapi_starts_on_a_clean_install(tmp_path):
    api = WebApi(tmp_path)
    assert api.ui_language() in ("ru", "en")
    assert ui_lang.language() == api.ui_language()


def test_webapi_starts_with_english_chosen_in_settings(tmp_path):
    WebApi(tmp_path).settings_set_preferences({"ui_language": "en"})
    api = WebApi(tmp_path)  # второй запуск — настройка уже на диске
    assert api.ui_language() == "en" and ui_lang.language() == "en"
    ui_lang.set_language("ru")
