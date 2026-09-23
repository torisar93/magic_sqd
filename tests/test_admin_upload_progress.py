"""Публикация модели из редактора (app/admin_client.py: upload_model) сообщает,
сколько байт архива уже ушло на сервер — окно «Сохранение модели» показывает
процент (владелец, 2026-09-23: модель с APK заливалась полторы минуты без
единого признака, и он решил, что файлы на сервер не дошли). Сервер —
локальный HTTP, принимает POST /admin/api/upload как настоящий backend.py."""
from __future__ import annotations
import http.server
import json
import os
import threading

from app import admin_client


class _UploadHandler(http.server.BaseHTTPRequestHandler):
    received = 0

    def do_POST(self):  # noqa: N802 - имя из http.server
        length = int(self.headers["Content-Length"])
        _UploadHandler.received = len(self.rfile.read(length))
        body = json.dumps({"files": 3}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_upload_model_reports_bytes_sent(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_client, "CHUNK_SIZE", 1024)
    cars = tmp_path / "cars"
    model_dir = cars / "Haval" / "Jolion" / "2026"
    model_dir.mkdir(parents=True)
    (model_dir / "stages.py").write_text("STAGES = []", encoding="utf-8")
    (model_dir / "big.apk").write_bytes(os.urandom(10_000))  # не сжимается — архив ~10 КБ, ~10 кусков
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _UploadHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    progress = []
    try:
        count = admin_client.upload_model(f"http://127.0.0.1:{server.server_address[1]}", "sid=1", cars, model_dir,
                                          on_progress=lambda sent, total: progress.append((sent, total)))
    finally:
        server.shutdown()
        server.server_close()

    assert count == 3
    total = progress[-1][1]
    assert progress[-1][0] == total == _UploadHandler.received  # всё ушло и дошло
    assert len(progress) > 1 and [sent for sent, _ in progress] == sorted(sent for sent, _ in progress)
