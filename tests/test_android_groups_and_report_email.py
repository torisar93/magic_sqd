"""Группы пользователей и ответ на обращение, Android (Chaquopy): сессия техника уходит со
запросами каталога только на свой сервер (content_sync.open_url — в том числе скачивание файлов
и описаний APK), обращение несёт почту для ответа или сессию (report_bridge.send_report).
Kotlin-часть (WebBridge.kt: applyCatalogSession, reportSend) — проверкой исходника."""
from __future__ import annotations
import http.server
import importlib
import json
import re
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"
WEB_BRIDGE = ROOT / "android/app/src/main/java/ru/magicsqd/mobile/WebBridge.kt"
SESSION = "magicsqd_user_session=tester-token"


@pytest.fixture
def android(monkeypatch):
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    for name in ("content_sync", "report_bridge"):
        sys.modules.pop(name, None)
    modules = {name: importlib.import_module(name) for name in ("content_sync", "report_bridge")}
    yield modules
    modules["content_sync"].set_auth_cookie(None)
    for name in modules:
        sys.modules.pop(name, None)


class _Recorder(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.seen.append((self.path, self.headers.get("Cookie")))
        body = json.dumps({"files": {"cars/A/B/stages.py": {"size": 1, "mtime": 1.0}}}).encode("utf-8") \
            if self.path.endswith("manifest.json") else b"x"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        self.server.seen.append((body, self.headers.get("Cookie")))
        data = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def server():
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    httpd.seen = []
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    httpd.base = f"http://127.0.0.1:{httpd.server_address[1]}"
    yield httpd
    httpd.shutdown()
    httpd.server_close()


def test_catalog_requests_carry_session_only_to_own_server(android, server, tmp_path):
    content_sync = android["content_sync"]
    content_sync.set_auth_cookie(SESSION, "127.0.0.1")
    assert content_sync.fetch_manifest(server.base + "/content") == {"cars/A/B/stages.py": {"size": 1, "mtime": 1.0}}
    content_sync.download_file(server.base + "/content", "cars/A/B/stages.py", tmp_path / "stages.py")
    content_sync.set_auth_cookie(SESSION, "magicsqd.ru")
    content_sync.fetch_manifest(server.base + "/content")
    content_sync.set_auth_cookie("", "127.0.0.1")  # WebBridge передаёт пустую строку, когда вышли
    content_sync.fetch_manifest(server.base + "/content")
    assert [cookie for _, cookie in server.seen] == [SESSION, SESSION, None, None]


def test_report_email_or_session(android, server):
    report_bridge = android["report_bridge"]
    url = server.base + "/report"
    assert json.loads(report_bridge.send_report("", "", "Другое", "x", "1.0.36", "client123456789", url, "k",
                                                "tech@example.com", "")) == {"ok": True}
    body, cookie = server.seen[-1]
    assert body["email"] == "tech@example.com" and cookie is None
    report_bridge.send_report("", "", "Другое", "x", "1.0.36", "client123456789", url, "k", "", SESSION)
    body, cookie = server.seen[-1]
    assert body["email"] == "" and cookie == SESSION
    report_bridge.send_report("", "", "Другое", "x", "1.0.36", "client123456789", url, "k")  # старый вызов
    assert server.seen[-1][0]["email"] == ""


def test_web_bridge_sets_catalog_session_and_resyncs():
    code = WEB_BRIDGE.read_text(encoding="utf-8")
    apply_calls = code.count("applyCatalogSession()")
    assert apply_calls >= 5  # init, startSync, вход, выход + само определение
    login = re.search(r"private fun authLogin\(.*?\n    }\n", code, flags=re.S).group(0)
    assert "applyCatalogSession()" in login and "startSync()" in login
    logout = re.search(r"private fun authLogout\(.*?\n    }\n", code, flags=re.S).group(0)
    assert logout.index("clearAuthSession()") < logout.index("applyCatalogSession()") and "startSync()" in logout
    report = re.search(r"private fun reportSend\(.*?\n    }\n", code, flags=re.S).group(0)
    assert 'if (cookie != null) "" else email.trim()' in report
    assert "replyEmail, cookie ?: \"\"" in report and 'putString("report_email", replyEmail)' in report
    assert '"report_info" -> JSONObject()' in code
