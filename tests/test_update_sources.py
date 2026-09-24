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
                   "download_url": f"{download_server.url}/MagicSQD_Android.apk", "mandatory": False}
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


# --- Обязательные релизы (владелец, 2026-09-25): строка «Обязательное обновление» в описании релиза;
# действует на все версии НИЖЕ помеченной (min_version в version.json зеркала, описания релизов на GitHub).

class _FakeResponse:
    def __init__(self, data):
        self._raw = json.dumps(data).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _releases(newest="v1.0.42"):
    return [
        {"tag_name": newest, "body": f"{newest}\n\n- обычные правки, обязательное обновление сертификата не нужно",
         "assets": [{"name": f"MagicSQD_Setup_{newest[1:]}.exe", "browser_download_url": "gh-exe"},
                    {"name": f"MagicSQD_Android_{newest[1:]}.apk", "browser_download_url": "gh-apk"}]},
        {"tag_name": "v1.0.41", "body": "v1.0.41\n\nОбязательное обновление.\n- исправлено важное", "assets": []},
        {"tag_name": "v1.0.40", "body": "v1.0.40\n\nОбязательное обновление", "assets": []},
    ]


def test_mandatory_release_on_desktop(tmp_path, monkeypatch, download_server):
    from app.web.api import update_api
    monkeypatch.setattr(update_api, "APP_VERSION", "1.0.40")
    api = make_desktop_api(tmp_path, monkeypatch, download_server)

    write_version(download_server, version="v1.0.42", min_version="v1.0.41")
    assert api._check_own_server()["mandatory"] is True, "наша 1.0.40 ниже обязательной 1.0.41"
    write_version(download_server, version="v1.0.42", min_version="v1.0.40")
    assert api._check_own_server()["mandatory"] is False, "не ниже обязательной — можно отложить"
    write_version(download_server, version="v1.0.42", changelog="v1.0.42\n\n Обязательное обновление! \n- правки")
    assert api._check_own_server()["mandatory"] is True, "метка в описании самого нового релиза"
    write_version(download_server, version="v1.0.42", changelog="- обязательное обновление сертификата не нужно")
    assert api._check_own_server()["mandatory"] is False, "метка — отдельной строкой, не внутри фразы"

    monkeypatch.setattr(update_api.urllib.request, "urlopen", lambda req, timeout=None: _FakeResponse(_releases()))
    github = api._check_github()
    assert github["version"] == "v1.0.42" and github["mandatory"] is True, "1.0.41 помечен и новее нашей"
    monkeypatch.setattr(update_api, "APP_VERSION", "1.0.41")
    assert api._check_github()["mandatory"] is False, "помеченный 1.0.41 уже стоит — 1.0.42 обычное"

    # check(): самая новая версия из двух источников, обязательность — если сказал хоть один.
    monkeypatch.setattr(api, "_check_own_server", lambda: {"available": True, "version": "v1.0.43", "mandatory": False})
    monkeypatch.setattr(api, "_check_github", lambda: {"available": True, "version": "v1.0.42", "mandatory": True})
    best = api.check()
    assert best["version"] == "v1.0.43" and best["mandatory"] is True


def test_mandatory_release_on_android(monkeypatch, download_server, mobile_bridge):
    monkeypatch.setattr(mobile_bridge, "_OWN_SERVER_VERSION_URL", f"{download_server.url}/version.json")
    write_version(download_server, version="v1.0.42", min_version="v1.0.41")
    assert mobile_bridge._check_update_own_server("1.0.40")["mandatory"] is True
    assert mobile_bridge._check_update_own_server("1.0.41")["mandatory"] is False

    monkeypatch.setattr(mobile_bridge.urllib.request, "urlopen", lambda req, timeout=None: _FakeResponse(_releases()))
    assert mobile_bridge._check_update_github("1.0.40")["mandatory"] is True
    assert mobile_bridge._check_update_github("1.0.41")["mandatory"] is False
    monkeypatch.setattr(mobile_bridge, "_check_update_own_server", lambda cur: {"available": True, "version": "1.0.42", "mandatory": False})
    assert json.loads(mobile_bridge.check_update("1.0.40"))["mandatory"] is True


def test_mirror_script_writes_min_version(tmp_path):
    """scripts/mirror_release.sh: min_version в version.json — самый новый релиз с меткой (не новее зеркалируемого)."""
    import subprocess
    script = (ROOT / "scripts/mirror_release.sh").read_text(encoding="utf-8")
    start = script.index("<<'PY'\n", script.index("releases.jsonl")) + len("<<'PY'\n")
    code = script[start:script.index("\nPY\n", start)]
    releases = tmp_path / "releases.jsonl"
    rows = [["v1.0.43", "v1.0.43\n\nОбязательное обновление"],  # новее зеркалируемого — не учитываем
            ["v1.0.42", "v1.0.42\n\n- обычное"], ["v1.0.41", "v1.0.41\n\nОбязательное обновление."],
            ["v1.0.9", "v1.0.9\n\nобязательное обновление"]]
    releases.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    out = tmp_path / "version.json"

    def mirror(tag):
        subprocess.run([sys.executable, "-", str(out), tag, f"{tag}\n\n- правки", '{"windows": "MagicSQD_Setup.exe"}',
                        '{"MagicSQD_Setup.exe": "abc"}', str(releases)], input=code, text=True, check=True)
        return json.loads(out.read_text(encoding="utf-8"))

    data = mirror("v1.0.42")
    assert data["min_version"] == "v1.0.41" and data["version"] == "v1.0.42" and data["assets"] == {"windows": "MagicSQD_Setup.exe"}
    releases.write_text(json.dumps(["v1.0.42", "- обычное"], ensure_ascii=False) + "\n", encoding="utf-8")
    assert "min_version" not in mirror("v1.0.42"), "помеченных нет — поля нет"
