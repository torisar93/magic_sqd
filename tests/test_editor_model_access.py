"""Редактор моделей (ПК), «Кто видит»: доступ модели ставится на сервере ДО загрузки файлов, при
переименовании скрытой модели переезжает на новый путь, со старым сервером (без групп) публикация
идёт как раньше (app/web/api/car_editor_api.py: _apply_access, get_access; app/admin_client.py)."""
from __future__ import annotations
import http.server
import json
import sys
import threading
import types
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app import admin_client

# car_editor_api импортирует pywebview (диалоги выбора файлов), которого нет в requirements-dev.txt
# (CI) — здесь он не нужен: тестируются только вызовы админ-API. Заглушку убираем сразу после
# импорта, чтобы не влиять на другие тесты (они сами пропускаются без pywebview).
_WEBVIEW_STUBBED = "webview" not in sys.modules
if _WEBVIEW_STUBBED:
    sys.modules["webview"] = types.ModuleType("webview")
from app.web.api.car_editor_api import CarEditorApi, _clean_access  # noqa: E402
if _WEBVIEW_STUBBED:
    del sys.modules["webview"]


class _AdminHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _reply(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        self.server.calls.append(("GET", parsed.path, parse_qs(parsed.query), self.headers.get("Cookie")))
        if parsed.path != "/admin/api/access" or not self.server.supports_access:
            self._reply(404, {"ok": False, "error": "not found"})
            return
        path = parse_qs(parsed.query).get("path", [""])[0]
        rule = self.server.rules.get(path)
        payload = {"ok": True, "all_groups": [{"id": 1, "name": "Тестировщики"}]}
        if path:
            payload.update({"path": path, "restricted": rule is not None, "groups": rule or []})
        self._reply(200, payload)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        self.server.calls.append(("POST", self.path, body, self.headers.get("Cookie")))
        if self.server.fail_post:
            self._reply(400, {"ok": False, "error": "Нет такой группы"})
            return
        if body["restricted"]:
            self.server.rules[body["path"]] = body["groups"]
        else:
            self.server.rules.pop(body["path"], None)
        self._reply(200, {"ok": True})


@pytest.fixture
def admin_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _AdminHandler)
    server.calls, server.rules, server.supports_access, server.fail_post = [], {}, True, False
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server.url = f"http://127.0.0.1:{server.server_address[1]}"
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def editor(tmp_path):
    api = CarEditorApi.__new__(CarEditorApi)  # без webview/сканера — нужны только методы доступа
    api.cars_dir = tmp_path / "cars"
    api._log = lambda message: None
    return api


COOKIE = "magicsqd_admin_session=t"


def test_explicit_choice_is_sent_before_upload(editor, admin_server):
    editor._apply_access(admin_server.url, COOKIE, "Geely/Новая", None, {"restricted": True, "groups": [1]})
    assert admin_server.calls == [("POST", "/admin/api/access", {"path": "Geely/Новая", "restricted": True, "groups": [1]}, COOKIE)]
    assert admin_server.rules == {"Geely/Новая": [1]}


def test_rename_of_hidden_model_keeps_it_hidden(editor, admin_server):
    admin_server.rules["Haval/Jolion 2026 test"] = [1]
    editor._apply_access(admin_server.url, COOKIE, "Haval/Jolion beta", Path("Haval/Jolion 2026 test"), None)
    assert admin_server.rules["Haval/Jolion beta"] == [1]


def test_rename_of_public_model_and_unchanged_choice_touch_nothing(editor, admin_server):
    editor._apply_access(admin_server.url, COOKIE, "Haval/Jolion 2", Path("Haval/Jolion"), None)
    editor._apply_access(admin_server.url, COOKIE, "Haval/Jolion", None, None)
    assert [call for call in admin_server.calls if call[0] == "POST"] == []


def test_old_server_without_groups_publishes_as_before(editor, admin_server):
    admin_server.supports_access = False
    editor._apply_access(admin_server.url, COOKIE, "Haval/Jolion 2", Path("Haval/Jolion"), None)  # без ошибки


def test_failed_access_stops_publishing(editor, admin_server):
    admin_server.fail_post = True
    with pytest.raises(admin_client.AdminClientError, match="Нет такой группы"):
        editor._apply_access(admin_server.url, COOKIE, "Geely/Новая", None, {"restricted": True, "groups": [9]})


def test_access_info_for_editor(editor, admin_server, tmp_path, monkeypatch):
    model_dir = editor.cars_dir / "Haval" / "Jolion 2026 test"
    model_dir.mkdir(parents=True)

    class _Model:
        dir = model_dir
        is_pending = False

    editor._scanner_api = type("Scanner", (), {"get_model": staticmethod(lambda key: _Model)})()
    editor.base_dir = tmp_path
    monkeypatch.setattr("app.web.api.car_editor_api.get_admin_base_url", lambda base: admin_server.url)
    admin_server.rules["Haval/Jolion 2026 test"] = [1]

    admin_client.clear_cached_session(admin_server.url)
    assert editor.get_access("key", admin_mode=True) == {"ok": True, "available": False}  # нет сессии админки
    assert editor.get_access("key", admin_mode=False)["available"] is False

    admin_client.set_cached_session(admin_server.url, COOKIE)
    try:
        info = editor.get_access("key", admin_mode=True)
        assert info["available"] and info["restricted"] and info["groups"] == [1]
        assert info["path"] == "Haval/Jolion 2026 test" and info["all_groups"][0]["name"] == "Тестировщики"
        new_model = editor.get_access(None, admin_mode=True)
        assert new_model["available"] and new_model["restricted"] is False and new_model["path"] is None
    finally:
        admin_client.clear_cached_session(admin_server.url)


@pytest.mark.parametrize("raw, expected", [
    (None, None), ({"restricted": "yes"}, None), ({"restricted": True, "groups": ["1"]}, None),
    ({"restricted": True, "groups": [True]}, None),
    ({"restricted": False, "groups": [1]}, {"restricted": False, "groups": []}),
    ({"restricted": True}, {"restricted": True, "groups": []}),
])
def test_access_payload_is_validated(raw, expected):
    assert _clean_access(raw) == expected
