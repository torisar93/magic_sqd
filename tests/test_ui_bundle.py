"""Интерфейс с сервера (app/ui_bundle.py + app/ed25519.py; владелец, 2026-10-06: «чтобы обновлений приложения стало
поменьше»): бандл под точную версию программы ставится только с верной подписью Ed25519, размером и sha256; путь
наружу, чужой тип файла, подпись от другой версии/платформы — отказ, остаётся встроенный интерфейс; rev 0 — выключить;
выпуск, с которым интерфейс не запустился, больше не включается; старые папки убираются."""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
import types
import zipfile
from pathlib import Path

import pytest

from app import ed25519, ui_bundle
from conftest import wait_until

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"
TEST_SECRET = bytes(range(32))
VERSION = "1.0.62"


@pytest.mark.parametrize("name", ["ed25519.py", "ui_bundle.py"])
def test_android_copy_is_identical(name):
    assert (ROOT / "app" / name).read_bytes() == (ANDROID_PY / name).read_bytes(), (
        f"app/{name} и android/.../python/{name} разошлись — правьте обе копии")


# RFC 8032, раздел 7.1, TEST 1–3: секретный ключ, открытый ключ, сообщение, подпись.
RFC_VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


@pytest.mark.parametrize("secret,public,message,signature", RFC_VECTORS)
def test_ed25519_rfc8032_vectors(secret, public, message, signature):
    secret, public, message, signature = (bytes.fromhex(x) for x in (secret, public, message, signature))
    assert ed25519.public_key(secret) == public
    assert ed25519.sign(secret, message) == signature
    assert ed25519.verify(public, message, signature)
    assert not ed25519.verify(public, message + b"x", signature)
    broken = bytearray(signature)
    broken[5] ^= 1
    assert not ed25519.verify(public, message, bytes(broken))


@pytest.mark.parametrize("public,signature", [(b"", b"\0" * 64), (b"\0" * 31, b"\0" * 64), (b"\0" * 32, b"\0" * 63),
                                              ("строка", b"\0" * 64), (b"\xff" * 32, b"\0" * 64)])
def test_ed25519_rejects_malformed_input(public, signature):
    assert ed25519.verify(public, b"msg", signature) is False


def test_production_public_key_is_a_curve_point():
    assert ed25519._decompress(ui_bundle.PUBLIC_KEY) is not None


@pytest.fixture
def signer(monkeypatch):
    monkeypatch.setattr(ui_bundle, "PUBLIC_KEY", ed25519.public_key(TEST_SECRET))
    return TEST_SECRET


def make_zip(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buf.getvalue()


def publish(server, platform: str, rev: int, files: dict, *, version: str = VERSION, secret: bytes = TEST_SECRET,
            signed_for: tuple | None = None, data: bytes | None = None, extra_entries: dict | None = None) -> dict:
    """Положить бандл на тестовый «сервер» так же, как scripts/publish_ui.py."""
    data = make_zip(files) if data is None else data
    sha = hashlib.sha256(data).hexdigest()
    sign_platform, sign_version, sign_rev = signed_for or (platform, version, rev)
    sig = ed25519.sign(secret, ui_bundle.signed_message(sign_platform, sign_version, sign_rev, sha)).hex()
    name = f"{platform}-{version}-r{rev}.zip"
    server.add(f"ui/{name}", data)
    entry = {"rev": rev, "file": name, "size": len(data), "sha256": sha, "sig": sig}
    manifest = {"schema": 1, platform: {version: entry}}
    for key, value in (extra_entries or {}).items():
        manifest.setdefault(key, {}).update(value)
    server.add("ui/manifest.json", json.dumps(manifest).encode("utf-8"))
    return entry


@pytest.fixture
def builtin(tmp_path):
    folder = tmp_path / "builtin"
    (folder / "js").mkdir(parents=True)
    (folder / "img").mkdir()
    (folder / "index.html").write_text("<html>встроенный</html>", encoding="utf-8")
    (folder / "js/app.js").write_text("// встроенный", encoding="utf-8")
    (folder / "img/big.png").write_bytes(b"\x89PNG" + b"0" * 1000)
    return folder


def test_no_manifest_on_server_means_builtin(tmp_path, content_server):
    root = tmp_path / "ui"
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION) == "none"
    assert ui_bundle.active_dir(root, "desktop", VERSION) is None
    assert not root.exists()


def test_network_error_keeps_everything(tmp_path):
    assert ui_bundle.refresh("http://127.0.0.1:9/content", tmp_path / "ui", "desktop", VERSION, timeout=1) == "error"


def test_desktop_bundle_overlays_copy_of_builtin(tmp_path, content_server, signer, builtin):
    root = tmp_path / "ui"
    publish(content_server, "desktop", 3, {"js/app.js": "// с сервера", "css/new.css": "body{}"})
    messages = []
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=builtin,
                             log=messages.append) == "updated"
    active = ui_bundle.active_dir(root, "desktop", VERSION)
    assert active == root / "desktop-1.0.62-r3"
    assert (active / "js/app.js").read_text(encoding="utf-8") == "// с сервера"
    assert (active / "css/new.css").is_file()
    assert (active / "index.html").read_text(encoding="utf-8") == "<html>встроенный</html>"  # из встроенного
    assert (active / "img/big.png").read_bytes() == (builtin / "img/big.png").read_bytes()
    assert ui_bundle.active_rev(root, "desktop", VERSION) == 3
    assert any("следующем запуске" in m for m in messages)
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=builtin) == "same"
    # Бандл — только для своей версии программы и платформы.
    assert ui_bundle.active_dir(root, "desktop", "1.0.63") is None
    assert ui_bundle.active_dir(root, "android", VERSION) is None


def test_android_bundle_holds_only_its_files(tmp_path, content_server, signer):
    root = tmp_path / "ui"
    publish(content_server, "android", 1, {"js/app.js": "// с сервера"})
    assert ui_bundle.refresh(content_server.url, root, "android", VERSION) == "updated"
    active = ui_bundle.active_dir(root, "android", VERSION)
    assert active == root / "android-1.0.62-r1"
    assert sorted(p.relative_to(active).as_posix() for p in active.rglob("*") if p.is_file()) == ["js/app.js"]
    state = json.loads((root / "state.json").read_text(encoding="utf-8"))
    # Эти поля читает и Kotlin (UiBundleAssets.active) — имена и типы не менять без него.
    assert state["platform"] == "android" and state["app"] == VERSION and state["rev"] == 1
    assert state["dir"] == "android-1.0.62-r1" and state["bad"] == []


def test_kotlin_reads_the_same_state_contract():
    source = (ROOT / "android/app/src/main/java/ru/magicsqd/mobile/UiBundleAssets.kt").read_text(encoding="utf-8")
    assert ui_bundle.bundle_dir_name("android", "1.0.62", 3) == "android-1.0.62-r3"
    assert '"$PLATFORM-$appVersion-r$rev"' in source
    for key in ('"state.json"', 'opt("rev")', 'optJSONArray("bad")', 'opt("platform")', 'opt("app")', 'opt("dir")'):
        assert key in source, key
    assert ui_bundle.STATE_NAME == "state.json"


@pytest.mark.parametrize("case", ["other_key", "other_version", "other_platform", "other_rev", "sha", "size"])
def test_unverified_bundle_is_not_installed(tmp_path, content_server, signer, builtin, case):
    root = tmp_path / "ui"
    files = {"js/app.js": "// с сервера"}
    if case == "other_key":
        publish(content_server, "desktop", 2, files, secret=bytes(32))
    elif case == "other_version":
        publish(content_server, "desktop", 2, files, signed_for=("desktop", "1.0.61", 2))
    elif case == "other_platform":
        publish(content_server, "desktop", 2, files, signed_for=("android", VERSION, 2))
    elif case == "other_rev":
        publish(content_server, "desktop", 2, files, signed_for=("desktop", VERSION, 1))
    else:
        entry = publish(content_server, "desktop", 2, files)
        if case == "sha":
            entry["sha256"] = "0" * 64
        else:
            entry["size"] += 1
        content_server.add("ui/manifest.json", json.dumps({"schema": 1, "desktop": {VERSION: entry}}).encode())
    messages = []
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=builtin,
                             log=messages.append) == "error"
    assert ui_bundle.active_dir(root, "desktop", VERSION) is None
    assert any("не установлен" in m for m in messages)


@pytest.mark.parametrize("name", ["../evil.js", "js/../../evil.js", "/abs.js", "C:/x.js", "js/run.exe",
                                  "js/script.py"])
def test_dangerous_archive_members_are_rejected(tmp_path, content_server, signer, builtin, name):
    root = tmp_path / "ui"
    publish(content_server, "desktop", 1, {}, data=make_zip({"js/app.js": "ok", name: "x"}))
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=builtin) == "error"
    assert ui_bundle.active_dir(root, "desktop", VERSION) is None
    assert not (tmp_path / "evil.js").exists()
    assert not root.exists() or list(root.iterdir()) == []  # и временная папка убрана


def test_desktop_bundle_without_index_is_rejected(tmp_path, content_server, signer):
    root = tmp_path / "ui"
    publish(content_server, "desktop", 1, {"js/app.js": "x"})
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=None) == "error"
    assert ui_bundle.active_dir(root, "desktop", VERSION) is None


@pytest.mark.parametrize("entry", [{"rev": 0}, None])
def test_server_can_switch_bundle_off(tmp_path, content_server, signer, builtin, entry):
    root = tmp_path / "ui"
    publish(content_server, "desktop", 4, {"js/app.js": "// с сервера"})
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=builtin) == "updated"
    manifest = {"schema": 1, "desktop": {VERSION: entry} if entry else {}}
    content_server.add("ui/manifest.json", json.dumps(manifest).encode())
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=builtin) == "disabled"
    assert ui_bundle.active_dir(root, "desktop", VERSION) is None
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=builtin) == "none"
    # Следующий выпуск включает снова.
    publish(content_server, "desktop", 5, {"js/app.js": "// исправлено"})
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=builtin) == "updated"
    assert ui_bundle.active_rev(root, "desktop", VERSION) == 5


def test_bundle_that_failed_to_start_is_not_used_again(tmp_path, content_server, signer, builtin):
    root = tmp_path / "ui"
    publish(content_server, "desktop", 2, {"js/app.js": "// сломано"})
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=builtin) == "updated"
    ui_bundle.mark_bad(root, 2)
    assert ui_bundle.active_dir(root, "desktop", VERSION) is None
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=builtin) == "same"
    assert ui_bundle.active_dir(root, "desktop", VERSION) is None
    publish(content_server, "desktop", 3, {"js/app.js": "// исправлено"})
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=builtin) == "updated"
    assert ui_bundle.active_rev(root, "desktop", VERSION) == 3
    assert json.loads((root / "state.json").read_text(encoding="utf-8"))["bad"] == [2]


def test_same_rev_with_other_content_does_not_replace_open_folder(tmp_path, content_server, signer, builtin):
    root = tmp_path / "ui"
    publish(content_server, "desktop", 2, {"js/app.js": "// первый"})
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=builtin) == "updated"
    publish(content_server, "desktop", 2, {"js/app.js": "// подменён"})
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=builtin) == "error"
    assert (root / "desktop-1.0.62-r2/js/app.js").read_text(encoding="utf-8") == "// первый"


def test_cleanup_removes_old_bundles_and_other_versions(tmp_path, content_server, signer, builtin):
    root = tmp_path / "ui"
    publish(content_server, "desktop", 1, {"js/app.js": "// r1"})
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=builtin) == "updated"
    publish(content_server, "desktop", 2, {"js/app.js": "// r2"})
    assert ui_bundle.refresh(content_server.url, root, "desktop", VERSION, builtin_dir=builtin) == "updated"
    (root / ".desktop-1.0.62-r9.tmp").mkdir()
    ui_bundle.cleanup(root, "desktop", VERSION)
    assert sorted(p.name for p in root.iterdir()) == ["desktop-1.0.62-r2", "state.json"]
    # Новая версия программы: её встроенный интерфейс уже с правками — бандл прошлой версии убирается.
    messages = []
    ui_bundle.cleanup(root, "desktop", "1.0.63", log=messages.append)
    assert sorted(p.name for p in root.iterdir()) == ["state.json"]
    assert json.loads((root / "state.json").read_text(encoding="utf-8")) == {}
    assert messages


def test_broken_state_file_means_builtin(tmp_path):
    root = tmp_path / "ui"
    (root / "desktop-1.0.62-r1").mkdir(parents=True)
    (root / "desktop-1.0.62-r1/index.html").write_text("x", encoding="utf-8")
    for state in ["{битый", "[]", json.dumps({"platform": "desktop", "app": VERSION, "rev": True,
                                              "dir": "desktop-1.0.62-r1"}),
                  json.dumps({"platform": "desktop", "app": VERSION, "rev": 1, "dir": "../outside"})]:
        (root / "state.json").write_text(state, encoding="utf-8")
        assert ui_bundle.active_dir(root, "desktop", VERSION) is None


def test_manifest_entry_must_be_well_formed(tmp_path, content_server, signer, builtin):
    entry = publish(content_server, "desktop", 1, {"js/app.js": "x"})
    for key, value in [("file", "../x.zip"), ("file", "x.tar"), ("rev", "1"), ("sig", "zz"), ("size", 0)]:
        bad = dict(entry, **{key: value})
        content_server.add("ui/manifest.json", json.dumps({"schema": 1, "desktop": {VERSION: bad}}).encode())
        assert ui_bundle.refresh(content_server.url, tmp_path / "ui", "desktop", VERSION,
                                 builtin_dir=builtin) == "error", (key, value)
    content_server.add("ui/manifest.json", json.dumps({"schema": 2, "desktop": {VERSION: entry}}).encode())
    assert ui_bundle.refresh(content_server.url, tmp_path / "ui", "desktop", VERSION, builtin_dir=builtin) == "error"


# --- ПК: выбор папки интерфейса при запуске и откат, если интерфейс с сервера не запустился ---------------------


def test_choose_frontend_dir(tmp_path, content_server, signer, builtin, monkeypatch):
    import main_web
    monkeypatch.setattr(main_web, "APP_VERSION", VERSION)
    base = tmp_path / "app"
    base.mkdir()
    assert main_web.choose_frontend_dir(base, builtin) == builtin
    publish(content_server, "desktop", 7, {"js/app.js": "// с сервера"})
    assert ui_bundle.refresh(content_server.url, base / "ui", "desktop", VERSION, builtin_dir=builtin) == "updated"
    assert main_web.choose_frontend_dir(base, builtin) == base / "ui/desktop-1.0.62-r7"
    monkeypatch.setattr(main_web, "APP_VERSION", "1.0.63")
    assert main_web.choose_frontend_dir(base, builtin) == builtin
    assert not (base / "ui/desktop-1.0.62-r7").exists()


def test_watchdog_falls_back_to_builtin(tmp_path, content_server, signer, builtin, monkeypatch):
    import main_web
    monkeypatch.setattr(main_web, "APP_VERSION", VERSION)
    base = tmp_path / "app"
    publish(content_server, "desktop", 2, {"js/app.js": "// сломано"})
    assert ui_bundle.refresh(content_server.url, base / "ui", "desktop", VERSION, builtin_dir=builtin) == "updated"
    bundle = main_web.choose_frontend_dir(base, builtin)
    assert bundle != builtin

    server = types.SimpleNamespace(root_path=str(bundle), address="http://127.0.0.1:5555/")
    fake_http = types.ModuleType("webview.http")
    fake_http.global_server = server
    fake_webview = types.ModuleType("webview")
    fake_webview.http = fake_http
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setitem(sys.modules, "webview.http", fake_http)
    calls = []

    class Api:
        def _wait_ui_ready(self, timeout):
            calls.append(("wait", timeout))
            return False  # app.js так и не сообщил ui_ready

        def _set_frontend(self, builtin_dir, bundle_dir):
            calls.append(("set", builtin_dir, bundle_dir))

    class Window:
        # Отсчёт минуты — с загрузки страницы (pywebview: window.events.loaded), а не с запуска окна.
        events = types.SimpleNamespace(loaded=types.SimpleNamespace(
            wait=lambda timeout: calls.append(("loaded", timeout)) or True))

        def load_url(self, url):
            calls.append(("load", url))

    main_web.start_ui_bundle_watchdog(Api(), Window(), base, builtin, bundle)
    assert wait_until(lambda: ("load", "http://127.0.0.1:5555/index.html") in calls)
    assert [c[0] for c in calls[:2]] == ["loaded", "wait"]
    assert ("wait", main_web.UI_READY_TIMEOUT) in calls
    assert ("set", builtin, None) in calls
    assert server.root_path == os.path.abspath(builtin)
    assert ui_bundle.active_dir(base / "ui", "desktop", VERSION) is None  # выпуск помечен плохим
    assert main_web.choose_frontend_dir(base, builtin) == builtin


def test_watchdog_quiet_when_ui_started(tmp_path, builtin):
    import main_web
    calls = []

    class Api:
        def _wait_ui_ready(self, timeout):
            calls.append("wait")
            return True

        def _set_frontend(self, *args):
            calls.append("set")

    main_web.start_ui_bundle_watchdog(Api(), None, tmp_path, builtin, tmp_path / "ui/desktop-1.0.62-r1")
    assert wait_until(lambda: calls == ["wait"])
    assert calls == ["wait"]


def test_watchdog_not_started_for_builtin(tmp_path, monkeypatch, builtin):
    import main_web
    import threading
    started = []
    monkeypatch.setattr(threading, "Thread", lambda *a, **k: started.append(1))
    main_web.start_ui_bundle_watchdog(object(), object(), tmp_path, builtin, builtin)
    assert started == []
