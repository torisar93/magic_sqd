"""app/apk_icons.py:_fetch_apk_icons — один запрос manifest.json на всех.

Список приложений просит иконку для КАЖДОГО APK почти одновременно
(app/web/frontend/js/refinement05.js: LabUI.appIcon), а pywebview выполняет
каждый вызов моста в отдельном потоке. Раньше при пустом кэше все эти потоки
разом качали один и тот же manifest.json: реальный отчёт о сбое на macOS
(2026-09-23, v1.0.30) — 253 потока, из них 194 висели в чтении HTTPS, и
выход через Cmd+Q уронил процесс на очистке OpenSSL. Сервер здесь —
локальный http.server с задержкой ответа, чтобы запросы гарантированно
пересеклись во времени."""
from __future__ import annotations
import http.server
import json
import threading
import time

import pytest

from app import apk_icons

ICONS = {"apk/Навигация/Yandex.apk": "icons/yandex.png"}


@pytest.fixture
def manifest_server():
    counter = {"requests": 0}
    lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            with lock:
                counter["requests"] += 1
            time.sleep(0.3)
            body = json.dumps({"files": {}, "apk_icons": ICONS}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}", counter
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def empty_manifest_cache(monkeypatch):
    monkeypatch.setattr(apk_icons, "_manifest_cache", {})
    monkeypatch.setattr(apk_icons, "_manifest_cache_at", 0.0)
    monkeypatch.setattr(apk_icons, "_manifest_cache_base_url", None)


def _fetch_concurrently(base_url: str, count: int) -> list:
    barrier = threading.Barrier(count)
    results = []

    def worker():
        barrier.wait()
        results.append(apk_icons._fetch_apk_icons(base_url))

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


def test_many_simultaneous_icon_requests_download_manifest_once(manifest_server):
    base_url, counter = manifest_server
    results = _fetch_concurrently(base_url, 40)
    assert counter["requests"] == 1
    assert results == [ICONS] * 40


def test_cached_manifest_is_reused_without_new_requests(manifest_server):
    base_url, counter = manifest_server
    apk_icons._fetch_apk_icons(base_url)
    _fetch_concurrently(base_url, 10)
    assert counter["requests"] == 1
