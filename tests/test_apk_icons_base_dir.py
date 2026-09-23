"""app/apk_icons.py:apk_icon — серверная иконка ищется относительно ПАПКИ
ДАННЫХ программы (WebApi.base_dir), а не папки рядом с исполняемым файлом.

Реальный случай (жалоба владельца, 2026-09-23): на macOS с v1.0.30 cars/ и
apk/ живут в ~/Library/Application Support/MagicSQD (см. main_web.py:
get_base_dir), а apk_icons.py вычислял базовую папку сам — своей копией
старого правила «рядом с exe», то есть Contents/MacOS/ собранного .app. Ни
один APK не оказывался «внутри» неё, ключ поля "apk_icons" манифеста не
строился, и в списках приложений на macOS были одни заглушки. Из
исходников (как запускают при разработке) обе папки совпадали — поэтому
баг был виден только в собранной программе."""
from __future__ import annotations
import http.server
import json
import sys
import threading
from pathlib import Path

import pytest

from app import apk_icons

ICON_REL = "icons/" + "ab" * 32 + ".png"


@pytest.fixture
def manifest_server():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"files": {}, "apk_icons": {
                "apk/Навигация/Yandex.apk": ICON_REL,
                "cars/Geely/Preface/Обычная/files/pack/optional/climator.apk": ICON_REL,
            }}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
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


@pytest.fixture(autouse=True)
def empty_caches(monkeypatch):
    monkeypatch.setattr(apk_icons, "_manifest_cache", {})
    monkeypatch.setattr(apk_icons, "_manifest_cache_at", 0.0)
    monkeypatch.setattr(apk_icons, "_manifest_cache_base_url", None)
    monkeypatch.setattr(apk_icons, "_result_cache", {})


def test_macos_app_bundle_finds_server_icons_for_data_in_application_support(tmp_path, monkeypatch, manifest_server):
    """Собранный .app: исполняемый файл в /Applications/…/Contents/MacOS, данные — в Application Support."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/Magic SQD.app/Contents/MacOS/magic_sqd")
    # server.json на macOS лежит в Contents/Resources бандла — здесь адрес подставляем напрямую
    monkeypatch.setattr(apk_icons, "get_base_url", lambda base_dir: manifest_server)
    data_dir = tmp_path / "Library" / "Application Support" / "MagicSQD"
    shared_apk = data_dir / "apk" / "Навигация" / "Yandex.apk"  # remote_only: файла ещё нет — так и в жизни
    model_apk = data_dir / "cars" / "Geely" / "Preface" / "Обычная" / "files" / "pack" / "optional" / "climator.apk"
    model_apk.parent.mkdir(parents=True)
    model_apk.write_bytes(b"not a real apk")

    assert apk_icons.apk_icon(str(shared_apk), data_dir) == f"{manifest_server}/{ICON_REL}"
    assert apk_icons.apk_icon(str(model_apk), data_dir) == f"{manifest_server}/{ICON_REL}"


def test_apk_outside_data_dir_gets_no_server_icon(tmp_path, monkeypatch, manifest_server):
    """Личный APK техника не из cars/apk — ключа в манифесте для него нет и быть не может."""
    monkeypatch.setattr(apk_icons, "get_base_url", lambda base_dir: manifest_server)
    data_dir = tmp_path / "data"
    elsewhere = tmp_path / "Downloads" / "Yandex.apk"

    assert apk_icons.apk_icon(str(elsewhere), data_dir) is None


def test_bridge_passes_its_data_dir(monkeypatch):
    """WebApi.scanner_apk_icon отдаёт в apk_icon свою base_dir — ту, в которую синхронизируются cars/apk.
    Мост целиком не создаём (тянет pywebview) — берём метод с подставным self."""
    bridge = pytest.importorskip("app.web.bridge")
    seen = {}
    monkeypatch.setattr(apk_icons, "apk_icon", lambda path, base_dir: seen.update(path=path, base_dir=base_dir) or "ok")
    fake_self = type("FakeApi", (), {"base_dir": Path("/Users/x/Library/Application Support/MagicSQD")})()

    assert bridge.WebApi.scanner_apk_icon(fake_self, "/p/a.apk") == "ok"
    assert seen == {"path": "/p/a.apk", "base_dir": Path("/Users/x/Library/Application Support/MagicSQD")}
