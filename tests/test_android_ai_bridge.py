"""ИИ-мастер на Android: python/ai_bridge.py — вызов сервера с токеном сборки из content_sync, «нет связи» как
network_error для ядра чата, проверка команд той же политикой, что на ПК и сервере."""
import importlib
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import ROOT

ANDROID_PY = ROOT / "android/app/src/main/python"
SEEN = []


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
        SEEN.append((self.path, dict(self.headers), body))
        raw = json.dumps({"ok": True, "status": "ok", "session": "S1"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class Token:
    def header(self):
        return {"X-App-Token": "T"}


@pytest.fixture
def bridge(monkeypatch):
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    return importlib.import_module("ai_bridge"), importlib.import_module("content_sync")


def test_call_sends_token_and_returns_json(bridge, monkeypatch):
    ai_bridge, content_sync = bridge
    SEEN.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(content_sync, "_app_token", Token())
        out = json.loads(ai_bridge.call(f"http://127.0.0.1:{httpd.server_address[1]}/chat", "start", "KEY", "sess=1",
                                        json.dumps({"model_key": "Haval/H3"})))
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert out["session"] == "S1"
    path, headers, body = SEEN[0]
    assert path == "/chat/ai/start" and headers["X-App-Token"] == "T" and headers["Cookie"] == "sess=1"
    assert body == {"model_key": "Haval/H3"}


def test_no_connection_is_network_error(bridge):
    ai_bridge, _ = bridge
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    out = json.loads(ai_bridge.call(f"http://127.0.0.1:{port}/chat", "status", "KEY", "", ""))
    assert "network_error" in out


def test_check_shell(bridge):
    ai_bridge, _ = bridge
    assert json.loads(ai_bridge.check_shell("shell  getprop ro.build.version.sdk", "auto", False)) == {
        "ok": True, "cmd": "getprop ro.build.version.sdk", "reason": ""}
    assert json.loads(ai_bridge.check_shell("settings put global x 1", "auto", False))["ok"] is False
    assert json.loads(ai_bridge.check_shell("settings put global x 1", "confirm", False))["ok"] is True
    assert json.loads(ai_bridge.check_shell("pm list packages", "auto", True))["ok"] is False
