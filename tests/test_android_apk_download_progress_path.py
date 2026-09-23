"""Android (Chaquopy) apk_library.ensure_apks_downloaded: прогресс скачивания
приходит с ТОЙ ЖЕ строкой пути, что прислало окно установки.

Раньше путь проходил через resolve(), а каталог данных приложения на Android
лежит под ссылкой (context.filesDir = /data/user/0/… → /data/data/…): строка
очереди окна (progress08.js ищет её по пути) не находилась — кольцо с
процентом крутилось, а у самих приложений во время скачивания статус не
менялся. На ПК так было сделано сразу (content_sync.ensure_apks_downloaded:
on_file_progress с исходной строкой) — владелец: «должно быть всё одинаково
на всех платформах» (2026-09-23)."""
from __future__ import annotations
import http.server
import importlib.util
import json
import sys
import threading
import urllib.parse
from pathlib import Path

import pytest

ANDROID_PY = Path(__file__).resolve().parents[1] / "android/app/src/main/python"
APK_BYTES = b"PK\x03\x04" + b"x" * 5000
REMOTE = "apk/Навигация/Yandex.apk"


@pytest.fixture
def apk_library(monkeypatch):
    # apk_library делает "from content_sync import ..." — это андроидный content_sync из той же папки.
    for name in ("content_sync", "apk_library"):
        spec = importlib.util.spec_from_file_location(name, ANDROID_PY / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return sys.modules["apk_library"]


@pytest.fixture
def content_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = urllib.parse.unquote(self.path.lstrip("/"))
            if path == "manifest.json":
                body = json.dumps({"files": {REMOTE: {"size": len(APK_BYTES), "mtime": 0}}}).encode("utf-8")
            elif path == REMOTE:
                body = APK_BYTES
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_download_progress_uses_the_path_the_window_sent(tmp_path, apk_library, content_server):
    real_files = tmp_path / "data" / "ru.magicsqd.mobile" / "files"
    (real_files / "apk").mkdir(parents=True)
    (real_files / "cars").mkdir()
    user0 = tmp_path / "user0"
    try:
        user0.symlink_to(tmp_path / "data", target_is_directory=True)  # как /data/user/0 → /data/data
    except OSError:
        pytest.skip("символические ссылки недоступны")
    files_dir = user0 / "ru.magicsqd.mobile" / "files"  # так его отдаёт context.filesDir
    window_path = str(files_dir / "apk" / "Навигация" / "Yandex.apk")
    seen = []

    downloaded = apk_library.ensure_apks_downloaded(
        files_dir / "apk", files_dir / "cars", content_server, [window_path],
        on_file_progress=lambda path, done, total: seen.append((path, done, total)))

    assert downloaded == 1
    assert seen and {path for path, _, _ in seen} == {window_path}
    assert seen[-1][1:] == (len(APK_BYTES), len(APK_BYTES))
    assert (real_files / "apk" / "Навигация" / "Yandex.apk").read_bytes() == APK_BYTES
