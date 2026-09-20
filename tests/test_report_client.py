"""«Сообщить о проблеме»: десктопный ReportApi (app/web/api/report_api.py) и Android-мост
(android/app/src/main/python/report_bridge.py) против заглушки сервера, повторяющей контракт
server/backend.py:_handle_report (ключ X-Submit-Key, обязательная причина, JSON-ответ)."""
from __future__ import annotations
import http.server
import json
import sys
import threading
from pathlib import Path

import pytest

from conftest import ROOT

ANDROID_PY = ROOT / "android/app/src/main/python"


class _ReportHandler(http.server.BaseHTTPRequestHandler):
    KEY = "secretkey"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        key = self.headers.get("X-Submit-Key", "")
        self.server.received.append((self.path, key, body))
        if key != self.KEY:
            code, payload = 401, {"ok": False, "error": "неверный ключ"}
        elif not str(body.get("reason") or "").strip():
            code, payload = 400, {"ok": False, "error": "не указана причина обращения"}
        else:
            code, payload = 200, {"ok": True}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def report_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ReportHandler)
    server.received = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def base(tmp_path, report_server):
    base = tmp_path / "app"
    base.mkdir()
    base.joinpath("submit.json").write_text(json.dumps({
        "submit_url": f"http://127.0.0.1:{report_server.server_address[1]}/submit",
        "submit_key": "secretkey",
    }), encoding="utf-8")
    return base


def test_desktop_report_carries_version_platform_and_client_id(base, report_server):
    from app.version import APP_VERSION
    from app.web.api.report_api import ReportApi
    api = ReportApi(base)
    assert api.get_info()["available"] is True

    result = api.send("Geely", "Monjaro — SE", "Не работает этап установки", "  описание  ", platform="windows")

    assert result["ok"] is True
    path, key, body = report_server.received[-1]
    assert path == "/report" and key == "secretkey"
    assert body["brand"] == "Geely" and body["model"] == "Monjaro — SE"
    assert body["app_version"] == APP_VERSION and body["platform"] == "windows"
    assert len(body["client_id"]) > 10


def test_desktop_report_about_the_app_has_empty_model(base, report_server):
    from app.web.api.report_api import ReportApi
    assert ReportApi(base).send("", "", "Ошибка в работе программы", "не открывается окно", platform="macos")["ok"]
    _, _, body = report_server.received[-1]
    assert body["brand"] == "" and body["model"] == "" and body["platform"] == "macos"


def test_desktop_report_errors_are_human_readable(base, report_server):
    from app.web.api.report_api import ReportApi
    api = ReportApi(base)
    assert api.send("A", "B", "", "x")["error"].endswith("не указана причина обращения")

    base.joinpath("submit.json").write_text(json.dumps({
        "submit_url": f"http://127.0.0.1:{report_server.server_address[1]}/submit", "submit_key": "wrong"}))
    assert "неверный ключ" in ReportApi(base).send("A", "B", "Другое", "x")["error"]

    base.joinpath("submit.json").write_text(json.dumps({"submit_url": "http://127.0.0.1:9/submit", "submit_key": "k"}))
    assert "Не удалось связаться" in ReportApi(base).send("A", "B", "Другое", "x")["error"]

    base.joinpath("submit.json").unlink()
    assert ReportApi(base).get_info()["available"] is False
    assert "submit.json" in ReportApi(base).send("A", "B", "Другое", "x")["error"]


def test_desktop_reasons_cover_app_wide_problems():
    from app.web.api.report_api import REASONS
    assert "Ошибка в работе программы" in REASONS and REASONS[-1] == "Другое"


@pytest.mark.skipif(not (ANDROID_PY / "report_bridge.py").exists(), reason="нет Android-моста")
def test_android_report_bridge(report_server):
    sys.path.insert(0, str(ANDROID_PY))
    try:
        import report_bridge
    finally:
        sys.path.remove(str(ANDROID_PY))
    url = f"http://127.0.0.1:{report_server.server_address[1]}/report"

    ok = json.loads(report_bridge.send_report("", "", "Ошибка в работе программы", "x", "1.0.16", "client123456789", url, "secretkey"))
    assert ok == {"ok": True}
    _, _, body = report_server.received[-1]
    assert body["platform"] == "android" and body["app_version"] == "1.0.16" and body["client_id"] == "client123456789"

    assert "не указана причина" in json.loads(report_bridge.send_report("", "", "", "x", "1.0.16", "c", url, "secretkey"))["error"]
    assert "неверный ключ" in json.loads(report_bridge.send_report("", "", "Другое", "x", "1.0.16", "c", url, "wrong"))["error"]
    assert "Не удалось связаться" in json.loads(report_bridge.send_report("", "", "Другое", "x", "1.0.16", "c", "http://127.0.0.1:9/report", "k"))["error"]
