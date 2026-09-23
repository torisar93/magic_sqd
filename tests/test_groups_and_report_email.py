"""Группы пользователей и ответ на обращение (владелец, 2026-09-23), десктоп:
- вошедший в аккаунт техник присылает cookie сессии со всеми запросами к каталогу — сервер
  отдаёт ему скрытые (тестовые) модели его групп; cookie уходит только на свой сервер;
- после входа каталог скачивается заново (скрытые модели появляются), после выхода —
  тоже (пропадают, см. content_sync.prune_removed_models);
- «Сообщить о проблеме»: почта для ответа — вписанная (запоминается) или почта аккаунта
  (тогда вместо неё уходит сессия)."""
from __future__ import annotations
import http.server
import json
import threading
from pathlib import Path

import pytest

from app import content_sync
from app.web.api import sync_api
from app.web.api.report_api import ReportApi
from app.web.api.sync_api import SyncApi

SESSION = "magicsqd_user_session=tester-token"
HIDDEN = "cars/Hidden/Test"


class _CatalogHandler(http.server.BaseHTTPRequestHandler):
    """Как nginx + backend: каталог и файлы скрытой модели — только с cookie сессии."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.server.requests.append((self.path, self.headers.get("Cookie")))
        allowed = self.headers.get("Cookie") == SESSION
        files = self.server.files
        if self.path == "/content/manifest.json":
            listed = {rel: {"size": len(data), "mtime": 1.0} for rel, data in files.items()
                      if allowed or not rel.startswith(HIDDEN + "/")}
            self._send(200, json.dumps({"files": listed}).encode("utf-8"))
            return
        rel = self.path[len("/content/"):]
        if rel.startswith(HIDDEN + "/") and not allowed:
            self._send(403, b"")
        elif rel in files:
            self._send(200, files[rel])
        else:
            self._send(404, b"")

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def catalog(tmp_path):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _CatalogHandler)
    server.requests = []
    server.files = {"cars/Public/M/_wizard_spec.json": b"{}", "cars/Public/M/stages.py": b"x",
                    f"{HIDDEN}/_wizard_spec.json": b"{}", f"{HIDDEN}/stages.py": b"x"}
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = tmp_path / "app"
    (base / "cars").mkdir(parents=True)
    (base / "apk").mkdir()
    (base / "server.json").write_text(json.dumps({"base_url": f"http://127.0.0.1:{server.server_address[1]}/content"}),
                                      encoding="utf-8")
    yield server, base
    content_sync.set_auth_cookie(None)
    server.shutdown()
    server.server_close()


def test_cookie_goes_only_to_the_account_server(catalog):
    server, base = catalog
    url = f"http://127.0.0.1:{server.server_address[1]}/content/manifest.json"
    content_sync.set_auth_cookie(SESSION, "127.0.0.1")
    with content_sync.open_url(url, timeout=5):
        pass
    content_sync.set_auth_cookie(SESSION, "magicsqd.ru")  # каталог на другом адресе — сессию не отдаём
    with content_sync.open_url(url, timeout=5):
        pass
    content_sync.set_auth_cookie(None)
    with content_sync.open_url(url, timeout=5):
        pass
    assert [cookie for _, cookie in server.requests] == [SESSION, None, None]


def test_login_and_logout_resync_the_catalog(catalog, monkeypatch):
    server, base = catalog
    events = []
    monkeypatch.setattr(sync_api.event_bridge, "push", events.append)
    api = SyncApi(base, base / "cars", base / "apk", scanner_api=None)
    hidden_dir = base / HIDDEN

    def resync():
        events.clear()
        api._resync_catalog_worker()
        assert {"kind": "catalog_resynced"} in events

    resync()  # аноним: только открытая модель
    assert (base / "cars/Public/M/stages.py").is_file() and not hidden_dir.exists()

    content_sync.set_auth_cookie(SESSION, "127.0.0.1")  # вошёл участник группы
    resync()
    assert (hidden_dir / "stages.py").is_file()
    assert all(cookie == SESSION for path, cookie in server.requests[-3:])

    content_sync.set_auth_cookie(None)  # вышел — тестовая модель убирается
    resync()
    assert not hidden_dir.exists() and (base / "cars/Public/M/stages.py").is_file()


class _ReportHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
        self.server.received.append((body, self.headers.get("Cookie")))
        data = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def report_base(tmp_path):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ReportHandler)
    server.received = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = tmp_path / "app"
    base.mkdir()
    (base / "submit.json").write_text(json.dumps({
        "submit_url": f"http://127.0.0.1:{server.server_address[1]}/submit", "submit_key": "k"}), encoding="utf-8")
    yield server, base
    server.shutdown()
    server.server_close()


def test_report_without_account_sends_and_remembers_typed_email(report_base):
    server, base = report_base
    api = ReportApi(base)
    assert api.get_info()["saved_email"] == ""
    result = api.send("", "", "Ошибка в работе программы", "текст", email="  tech@example.com ",
                      session_cookie="", account_email="")
    assert result["ok"] and "tech@example.com" in result["message"]
    body, cookie = server.received[-1]
    assert body["email"] == "tech@example.com" and cookie is None
    assert ReportApi(base).get_info()["saved_email"] == "tech@example.com"  # в следующий раз подставится


def test_report_with_account_sends_session_instead_of_email(report_base):
    server, base = report_base
    api = ReportApi(base)
    info = api.get_info(account_email="owner@example.com")
    assert info["account_email"] == "owner@example.com"
    result = api.send("Haval", "Jolion", "Другое", "", email="typed@example.com",
                      session_cookie=SESSION, account_email="owner@example.com")
    assert result["ok"] and "owner@example.com" in result["message"]
    body, cookie = server.received[-1]
    assert body["email"] == "" and cookie == SESSION
    assert not (base / "report_contact.json").exists()


def test_report_without_any_email_still_goes(report_base):
    server, base = report_base
    result = ReportApi(base).send("", "", "Другое", "", email="", session_cookie="", account_email="")
    assert result["ok"] and result["message"] == "Спасибо! Обращение отправлено."
    assert server.received[-1][0]["email"] == ""
