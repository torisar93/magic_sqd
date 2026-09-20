"""Общие фикстуры для тестов десктопного клиента (pytest, см. requirements-dev.txt).

Запуск: `pytest -q tests` из корня репозитория (CI: .github/workflows/tests.yml). Тесты не ходят в
интернет и не трогают настоящий magicsqd.ru: вместо сервера контента поднимается локальный HTTP-сервер
(`content_server`) с тем же устройством, что у nginx `location /content/` — статические файлы плюс
`manifest.json` в формате server/backend.py:write_manifest. Ни pywebview, ни ADB для этих тестов не нужны."""
from __future__ import annotations
import functools
import http.server
import json
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):  # noqa: D401 - не засорять вывод pytest
        pass


class ContentServer:
    """Локальная копия magicsqd.ru/content: файлы кладутся через add(), manifest.json — write_manifest()."""

    def __init__(self, root: Path):
        self.root = root
        self.content = root / "content"
        self.content.mkdir(parents=True, exist_ok=True)
        handler = functools.partial(_QuietHandler, directory=str(root))
        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self._server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/content"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def add(self, rel: str, data: bytes) -> Path:
        path = self.content / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def write_manifest(self, extra: dict | None = None) -> dict:
        files = {}
        for path in self.content.rglob("*"):
            if path.is_file() and path.name != "manifest.json":
                st = path.stat()
                files[path.relative_to(self.content).as_posix()] = {"size": st.st_size, "mtime": st.st_mtime}
        files.update(extra or {})
        (self.content / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")
        return files

    def close(self):
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture
def content_server(tmp_path):
    server = ContentServer(tmp_path / "web")
    yield server
    server.close()


@pytest.fixture
def app_base(tmp_path, content_server):
    """Папка «установленной программы»: server.json смотрит на локальный content_server."""
    base = tmp_path / "app"
    (base / "cars").mkdir(parents=True)
    (base / "apk").mkdir()
    (base / "server.json").write_text(json.dumps({"base_url": content_server.url}), encoding="utf-8")
    return base


def wait_until(predicate, timeout: float = 10.0, step: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False
