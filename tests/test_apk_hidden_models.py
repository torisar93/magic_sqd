"""Общий APK, скрытый админом на моделях (<файл>.json "hidden_models", владелец 2026-09-25): программы
читают список и отдают его мастеру — ПК (app/scanner.py → scanner_api.apk_to_dict) и Android
(python/apk_library.py), в том числе для ещё не скачанных APK. Сам фильтр по модели — tests/js/apk_visibility.test.js."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app.scanner import scan_apks
from app.web.api.scanner_api import apk_to_dict

ANDROID_PY = Path(__file__).resolve().parents[1] / "android/app/src/main/python"
MONJI = "Geely/Cityray/после 2026 (без Wi-Fi, Monji)"


def _library(tmp_path: Path) -> Path:
    apk_dir = tmp_path / "apk"
    (apk_dir / "Утилиты").mkdir(parents=True)
    (apk_dir / "Утилиты" / "gsplit.apk").write_bytes(b"PK")
    (apk_dir / "Утилиты" / "gsplit.json").write_text(
        json.dumps({"name": "GSplit", "hidden_models": [MONJI, 5], "exclusive_group": " Лаунчер "}, ensure_ascii=False),
        encoding="utf-8")
    (apk_dir / "Утилиты" / "plain.apk").write_bytes(b"PK")
    # Ещё не скачан: есть только сайдкар (content_sync.sync_shared_apk_metadata тянет их отдельно).
    (apk_dir / "Утилиты" / "remote.json").write_text(json.dumps({"hidden_models": ["Haval/Jolion"]}), encoding="utf-8")
    return apk_dir


def test_desktop_reads_hidden_models_for_local_and_remote_apks(tmp_path):
    apk_dir = _library(tmp_path)
    remote = [{"rel_path": "Утилиты/remote.apk", "size": 10}]
    by_name = {Path(a.path).name: apk_to_dict(a) for a in scan_apks(apk_dir, remote)}

    assert by_name["gsplit.apk"]["hidden_models"] == [MONJI]  # не-строки отброшены
    assert by_name["plain.apk"]["hidden_models"] == []
    assert by_name["gsplit.apk"]["exclusive_group"] == "Лаунчер" and by_name["plain.apk"]["exclusive_group"] == ""
    assert by_name["remote.apk"]["remote_only"] is True and by_name["remote.apk"]["hidden_models"] == ["Haval/Jolion"]


@pytest.fixture
def apk_library(monkeypatch):
    for name in ("content_sync", "apk_library"):  # apk_library импортирует андроидный content_sync
        spec = importlib.util.spec_from_file_location(name, ANDROID_PY / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return sys.modules["apk_library"]


def test_android_reads_hidden_models_for_local_apks(tmp_path, apk_library, monkeypatch):
    apk_dir = _library(tmp_path)
    monkeypatch.setattr(apk_library, "fetch_manifest", lambda base_url: None)  # без сети — только локальные

    items = {Path(a["path"]).name: a for a in json.loads(apk_library.list_apks(apk_dir, "http://127.0.0.1:9"))}
    assert items["gsplit.apk"]["hidden_models"] == [MONJI]
    assert items["plain.apk"]["hidden_models"] == []
    assert items["gsplit.apk"]["exclusive_group"] == "Лаунчер" and items["plain.apk"]["exclusive_group"] == ""


def test_android_reads_hidden_models_for_apks_not_downloaded_yet(tmp_path, apk_library):
    """На телефоне почти вся библиотека ещё не скачана: имя, описание и hidden_models приходят из <файл>.json с сервера."""
    import http.server
    import threading
    import urllib.parse

    files = {
        "manifest.json": json.dumps({"files": {"apk/Утилиты/gsplit.apk": {"size": 5, "mtime": 0},
                                               "apk/Утилиты/gsplit.json": {"size": 1, "mtime": 0}}}).encode("utf-8"),
        "apk/Утилиты/gsplit.json": json.dumps({"name": "GSplit", "hidden_models": [MONJI], "exclusive_group": "Лаунчер"},
                                              ensure_ascii=False).encode("utf-8"),
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = files.get(urllib.parse.unquote(self.path.lstrip("/")))
            if body is None:
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
    try:
        items = json.loads(apk_library.list_apks(tmp_path / "apk", f"http://127.0.0.1:{server.server_address[1]}"))
    finally:
        server.shutdown()
    assert len(items) == 1 and items[0]["remote_only"] is True
    assert items[0]["name"] == "GSplit" and items[0]["hidden_models"] == [MONJI]
    assert items[0]["exclusive_group"] == "Лаунчер"
