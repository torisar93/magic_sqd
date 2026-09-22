"""app/qr_adb_debug_client.py (десктоп) — против заглушки сервера, повторяющей
контракт server/backend.py:_handle_qr_adb_debug (X-Submit-Key, метаданные в
query, сырое тело). Плюс сквозной сценарий через QrAdbApi.get_password:
2026-09-22 — владелец попросил отправлять bugreport-*.zip НА СЕРВЕР вместо
только локального сохранения (жалоба "пароль выдался, но был неверный"),
локальное сохранение остаётся запасным путём на случай сбоя отправки."""
from __future__ import annotations
import http.server
import json
import threading
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from app.qr_adb_debug_client import QrAdbDebugUploadError, upload_debug_copy
from app.submit_config import SubmitConfig


class _QrAdbDebugHandler(http.server.BaseHTTPRequestHandler):
    KEY = "secretkey"

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        key = self.headers.get("X-Submit-Key", "")
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.server.received.append((parsed.path, key, query, body))
        if key != self.KEY:
            code, payload = 401, {"ok": False, "error": "неверный ключ"}
        else:
            code, payload = 200, {"ok": True}
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def qr_debug_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _QrAdbDebugHandler)
    server.received = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


@pytest.fixture
def config(qr_debug_server):
    return SubmitConfig(submit_url=f"http://127.0.0.1:{qr_debug_server.server_address[1]}/submit",
                         submit_key="secretkey")


# --- upload_debug_copy напрямую ---------------------------------------------

def test_upload_sends_raw_bytes_and_metadata_in_query(qr_debug_server, config):
    upload_debug_copy(config, "client-abc123", "macos", "logs_20260921_120000", b"PK\x03\x04fakezipbytes")

    path, key, query, body = qr_debug_server.received[0]
    assert path == "/qr_adb_debug"
    assert key == "secretkey"
    assert query == {"client_id": "client-abc123", "platform": "macos", "logs_folder": "logs_20260921_120000"}
    assert body == b"PK\x03\x04fakezipbytes"


def test_upload_raises_on_wrong_key(qr_debug_server):
    bad_config = SubmitConfig(submit_url=f"http://127.0.0.1:{qr_debug_server.server_address[1]}/submit",
                               submit_key="wrong")
    with pytest.raises(QrAdbDebugUploadError):
        upload_debug_copy(bad_config, "client-abc123", "macos", "logs_x", b"data")


def test_upload_raises_when_server_unreachable():
    unreachable = SubmitConfig(submit_url="http://127.0.0.1:1/submit", submit_key="secretkey")
    with pytest.raises(QrAdbDebugUploadError):
        upload_debug_copy(unreachable, "client-abc123", "macos", "logs_x", b"data")


# --- сквозной сценарий: QrAdbApi.get_password ------------------------------

def _make_drive_with_zip(drive: Path, content: bytes = b"fake zip bytes") -> Path:
    logs_folder = drive / "logs_20260921_120000"
    logs_folder.mkdir(parents=True)
    zip_path = logs_folder / "bugreport-1.zip"
    zip_path.write_bytes(content)
    return zip_path


def test_get_password_uploads_to_server_and_skips_local_copy_on_success(tmp_path, qr_debug_server, config, monkeypatch):
    from app.web.api import qr_adb_api

    base_dir = tmp_path / "app"
    base_dir.mkdir()
    base_dir.joinpath("submit.json").write_text(
        json.dumps({"submit_url": config.submit_url, "submit_key": config.submit_key}), encoding="utf-8")
    cars_dir = tmp_path / "cars"
    cars_dir.mkdir()
    drive = tmp_path / "drive"
    _make_drive_with_zip(drive, b"real bugreport content")

    api = qr_adb_api.QrAdbApi(base_dir, cars_dir, platform="macos")
    result = api.get_password(str(drive))

    # Разбор полей всё равно не удастся (это не настоящий bugreport), но
    # ключевая проверка здесь — что отправка на сервер реально произошла...
    assert len(qr_debug_server.received) == 1
    path, key, query, body = qr_debug_server.received[0]
    assert key == "secretkey"
    assert query["platform"] == "macos"
    assert body == b"real bugreport content"
    # ...и локальная копия при этом НЕ создавалась (отправка удалась).
    assert not (base_dir / "qr_adb_debug").exists()


def test_get_password_falls_back_to_local_copy_when_upload_fails(tmp_path, monkeypatch):
    from app.web.api import qr_adb_api

    base_dir = tmp_path / "app"
    base_dir.mkdir()
    # submit.json указывает на заведомо недоступный сервер — отправка должна
    # упасть, и код обязан откатиться на локальное сохранение, а не
    # потерять диагностику молча.
    base_dir.joinpath("submit.json").write_text(
        json.dumps({"submit_url": "http://127.0.0.1:1/submit", "submit_key": "secretkey"}), encoding="utf-8")
    cars_dir = tmp_path / "cars"
    cars_dir.mkdir()
    drive = tmp_path / "drive"
    zip_path = _make_drive_with_zip(drive, b"real bugreport content")

    api = qr_adb_api.QrAdbApi(base_dir, cars_dir, platform="macos")
    api.get_password(str(drive))

    saved = list((base_dir / "qr_adb_debug").glob("*.zip"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == zip_path.read_bytes()


def test_get_password_uses_local_copy_when_submit_json_missing(tmp_path):
    """Поведение до этой правки — без submit.json ничего не отправляем
    (get_submit_config возвращает None), сразу локально, как раньше."""
    from app.web.api import qr_adb_api

    base_dir = tmp_path / "app"
    base_dir.mkdir()
    cars_dir = tmp_path / "cars"
    cars_dir.mkdir()
    drive = tmp_path / "drive"
    _make_drive_with_zip(drive)

    api = qr_adb_api.QrAdbApi(base_dir, cars_dir, platform="macos")
    api.get_password(str(drive))

    assert list((base_dir / "qr_adb_debug").glob("*.zip"))
