"""ИИ-мастер: HTTP-клиент программы к /chat/ai/* (app/ai_client.py = Android-копия) на локальном сервере."""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import ai_client
from conftest import ROOT

SEEN = []


class Handler(BaseHTTPRequestHandler):
    def _reply(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        SEEN.append(("GET", self.path, dict(self.headers), None))
        self._reply(200, {"ok": True, "status": "ok", "used": 1, "limit": 10})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode())
        SEEN.append(("POST", self.path, dict(self.headers), body))
        if body.get("session") == "big":
            self._reply(413, {"ok": False, "error": "слишком большой запрос"})
        elif body.get("session") == "down":
            self._reply(502, {"ok": False})
        else:
            self._reply(200, {"ok": True, "status": "ok", "messages": [{"text": "привет", "chips": []}]})

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    SEEN.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/chat"
    httpd.shutdown()
    httpd.server_close()


def test_status_and_turn_carry_key_cookie_and_token(server):
    status = ai_client.call(server, "status", "KEY", cookie="sess=1", headers={"X-App-Token": "TOK"})
    assert status["status"] == "ok" and status["limit"] == 10
    method, path, headers, _ = SEEN[0]
    assert (method, path) == ("GET", "/chat/ai/status")
    assert headers["X-Submit-Key"] == "KEY" and headers["Cookie"] == "sess=1" and headers["X-App-Token"] == "TOK"
    turn = ai_client.call(server, "turn", "KEY", payload={"session": "S1", "req": "1", "input": [{"type": "text", "text": "да"}]})
    assert turn["messages"][0]["text"] == "привет"
    assert SEEN[1][1] == "/chat/ai/turn" and SEEN[1][3]["input"][0]["text"] == "да"


def test_client_errors_are_answers_and_server_errors_are_offline(server):
    assert ai_client.call(server, "turn", "KEY", payload={"session": "big"}) == {"status": "error", "error": "слишком большой запрос"}
    with pytest.raises(ai_client.AiNetworkError):
        ai_client.call(server, "turn", "KEY", payload={"session": "down"})


def test_no_connection_is_offline():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(ai_client.AiNetworkError):
        ai_client.call(f"http://127.0.0.1:{port}/chat", "status", "KEY", timeout=2)


def test_android_copy_is_identical():
    assert (ROOT / "android/app/src/main/python/ai_client.py").read_bytes() == (ROOT / "app/ai_client.py").read_bytes()
