"""Проверка обновлений через своё зеркало magicsqd.ru/download/ (version.json с картой assets — пишет
scripts/mirror_release.sh): desktop app/web/api/update_api.py и Android mobile_bridge.py. Раньше macOS и
Android ходили только на GitHub, который из РФ доступен через раз. Плюс пульс с версией/платформой."""
from __future__ import annotations
import json
import sys
import threading
import http.server

import pytest

from conftest import ROOT

ANDROID_PY = ROOT / "android/app/src/main/python"
FULL_ASSETS = {"windows": "MagicSQD_Setup.exe", "win7": "MagicSQD_Setup_Win7.exe", "android": "MagicSQD_Android.apk",
               "macos_arm64": "MagicSQD_arm64.dmg", "macos_x86_64": "MagicSQD_x86_64.dmg"}


@pytest.fixture
def download_server(tmp_path):
    """Локальное зеркало: отдаёт version.json (и что положат рядом)."""
    root = tmp_path / "download"
    root.mkdir()
    handler = type("H", (http.server.SimpleHTTPRequestHandler,), {
        "__init__": lambda self, *a, **k: http.server.SimpleHTTPRequestHandler.__init__(self, *a, directory=str(root), **k),
        "log_message": lambda self, *a: None})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    server.url = f"http://127.0.0.1:{server.server_address[1]}"
    server.root = root
    yield server
    server.shutdown()
    server.server_close()


def write_version(server, version="v99.0.0", assets=FULL_ASSETS, **extra):
    data = {"version": version, "changelog": "что нового", **extra}
    if assets is not None:
        data["assets"] = assets
    (server.root / "version.json").write_text(json.dumps(data), encoding="utf-8")


def make_desktop_api(tmp_path, monkeypatch, server, *, is_win7=False, is_mac=False, machine="arm64"):
    from app.web.api import update_api
    monkeypatch.setattr(update_api, "get_download_base_url", lambda base_dir: server.url)
    monkeypatch.setattr(update_api.platform, "machine", lambda: machine)
    monkeypatch.setattr(update_api.sys, "platform", "darwin" if is_mac else "win32")
    return update_api.UpdateApi(tmp_path, is_win7=is_win7)


@pytest.mark.parametrize("kind,expected", [
    (dict(), "MagicSQD_Setup.exe"),
    (dict(is_win7=True), "MagicSQD_Setup_Win7.exe"),
    (dict(is_mac=True, machine="arm64"), "MagicSQD_arm64.dmg"),
    (dict(is_mac=True, machine="x86_64"), "MagicSQD_x86_64.dmg"),
])
def test_desktop_picks_asset_for_its_platform(tmp_path, monkeypatch, download_server, kind, expected):
    write_version(download_server)
    api = make_desktop_api(tmp_path, monkeypatch, download_server, **kind)
    result = api._check_own_server()
    assert result and result["available"] is True and result["version"] == "v99.0.0"
    assert result["asset_name"] == expected and result["download_url"] == f"{download_server.url}/{expected}"


def test_desktop_without_assets_map_is_windows_only(tmp_path, monkeypatch, download_server):
    write_version(download_server, assets=None)  # version.json от старой карточки админки
    assert make_desktop_api(tmp_path, monkeypatch, download_server)._check_own_server()["asset_name"] == "MagicSQD_Setup.exe"
    assert make_desktop_api(tmp_path, monkeypatch, download_server, is_win7=True)._check_own_server() is None
    assert make_desktop_api(tmp_path, monkeypatch, download_server, is_mac=True)._check_own_server() is None


def test_desktop_ignores_older_and_broken_manifests(tmp_path, monkeypatch, download_server):
    write_version(download_server, version="v0.0.1")
    assert make_desktop_api(tmp_path, monkeypatch, download_server)._check_own_server() is None
    (download_server.root / "version.json").write_text("не json", encoding="utf-8")
    assert make_desktop_api(tmp_path, monkeypatch, download_server)._check_own_server() is None
    write_version(download_server, assets={"windows": "../evil.exe"})
    assert make_desktop_api(tmp_path, monkeypatch, download_server)._check_own_server() is None


@pytest.fixture
def mobile_bridge(monkeypatch):
    if not (ANDROID_PY / "mobile_bridge.py").exists():
        pytest.skip("нет Android-моста")
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    import mobile_bridge
    return mobile_bridge


def test_android_uses_own_mirror_and_prefers_newer_source(monkeypatch, download_server, mobile_bridge):
    write_version(download_server, version="v1.2.3")
    monkeypatch.setattr(mobile_bridge, "_OWN_SERVER_VERSION_URL", f"{download_server.url}/version.json")
    monkeypatch.setattr(mobile_bridge, "_OWN_SERVER_DOWNLOAD_BASE", f"{download_server.url}/")
    monkeypatch.setattr(mobile_bridge, "_check_update_github", lambda cur: None)

    own = mobile_bridge._check_update_own_server("1.0.16")
    assert own == {"available": True, "version": "1.2.3", "changelog": "что нового",
                   "download_url": f"{download_server.url}/MagicSQD_Android.apk"}
    assert mobile_bridge._check_update_own_server("1.2.3") is None, "та же версия — не обновление"
    assert json.loads(mobile_bridge.check_update("1.0.16"))["version"] == "1.2.3"

    # GitHub нашёл ещё новее — берётся он
    monkeypatch.setattr(mobile_bridge, "_check_update_github",
                        lambda cur: {"available": True, "version": "1.3.0", "changelog": "", "download_url": "gh"})
    assert json.loads(mobile_bridge.check_update("1.0.16"))["download_url"] == "gh"

    # зеркала APK нет (старый version.json) и GitHub молчит — обновления нет, без исключений
    write_version(download_server, version="v9.9.9", assets={"windows": "MagicSQD_Setup.exe"})
    monkeypatch.setattr(mobile_bridge, "_check_update_github", lambda cur: None)
    assert json.loads(mobile_bridge.check_update("1.0.16")) == {"available": False}


def test_ping_carries_version_and_platform(tmp_path):
    from app.ping_client import send_ping
    from app.submit_config import SubmitConfig
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, self.headers.get("X-Submit-Key"), body))
            data = b'{"ok": true}'
            self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        config = SubmitConfig(submit_url=f"http://127.0.0.1:{server.server_address[1]}/submit", submit_key="k")
        send_ping("client-1", config, app_version="1.0.17", platform="macos")
    finally:
        server.shutdown(); server.server_close()
    assert received == [("/ping", "k", {"client_id": "client-1", "app_version": "1.0.17", "platform": "macos"})]
