"""ПК: журнал сессии продолжается после отправки (все этапы пройдены, а техник работает дальше — раньше эти строки на
сервер не попадали) и в шапке — компьютер. JS-сторона — tests/js/desktop_session_log.test.js."""
from __future__ import annotations
import sys
import types

from app import pending_install_logs
from app.web.api.install_api import InstallApi
from app.os_info import os_description


def test_continuation_gets_a_fresh_journal_and_a_clean_emergency_buffer(tmp_path):
    api = InstallApi("adb", tmp_path, types.SimpleNamespace(get_model=lambda key: None))
    api._session_meta = {"brand": "Belgee", "model": "S50", "modification": ""}
    first = pending_install_logs.start_session(tmp_path, "Belgee", "S50", "")
    api._session_log_token = first
    api._session_log_lines = ["Флешка: записано — файлы этапа."]
    api._session_flushed = False

    token = api.continue_log_session()

    assert token and token != first and api._session_log_token == token
    assert api.pending_session_log() is None  # при закрытии окна не уйдёт заново уже отправленное
    pending_install_logs.append_current(tmp_path, first, "старый токен — мимо", True)
    pending_install_logs.append_current(tmp_path, token, "QR ADB: пароль получен.", True)
    assert pending_install_logs._current_log_path(tmp_path).read_text(encoding="utf-8") == "QR ADB: пароль получен.\n"
    assert pending_install_logs._read_current_meta(tmp_path)["model"] == "S50"


def test_os_line_names_the_system():
    text = os_description()
    expected = {"darwin": "macOS", "win32": "Windows"}.get(sys.platform, "")
    assert text and text.startswith(expected), text
