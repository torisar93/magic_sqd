"""scripts/publish_ui.py — выкладка интерфейса без выпуска программы (app/ui_bundle.py): бандл только если после
выпуска менялся лишь интерфейс этой платформы; архив воспроизводимый; запись манифеста принимает сама программа."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

from app import ed25519, ui_bundle

ROOT = Path(__file__).resolve().parents[1]
TEST_SECRET = bytes(range(32))
TEST_PUBLIC = ed25519.public_key(TEST_SECRET)


@pytest.fixture(scope="module")
def publish_ui():
    spec = importlib.util.spec_from_file_location("publish_ui", ROOT / "scripts/publish_ui.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_classify_changes(publish_ui):
    out = publish_ui.classify([
        "app/web/frontend/js/app.js", "android/app/src/main/assets/css/app.css",
        "tests/test_x.py", "research/Haval/notes.md", "README.md", "android/README.md", "cars/Haval/H3/stages.py",
        ".github/workflows/tests.yml", "scripts/publish_ui.py", "android/adbdiag/src/main/AndroidManifest.xml",
        "app/web/bridge.py", "main_web.py", "magic_sqd.spec", "requirements.txt",
        "android/app/src/main/java/ru/magicsqd/mobile/WebBridge.kt", "android/app/src/main/python/mobile_bridge.py",
        "android/app/build.gradle.kts",
    ])
    assert out["desktop_ui"] == ["app/web/frontend/js/app.js"]
    assert out["android_ui"] == ["android/app/src/main/assets/css/app.css"]
    assert out["desktop_native"] == ["app/web/bridge.py", "main_web.py", "magic_sqd.spec", "requirements.txt"]
    assert out["android_native"] == ["android/app/src/main/java/ru/magicsqd/mobile/WebBridge.kt",
                                     "android/app/src/main/python/mobile_bridge.py", "android/app/build.gradle.kts"]


def test_select_files(publish_ui):
    files = {"index.html": b"<html>", "js/app.js": b"js", "img/big.webp": b"img", "img/new.png": b"png",
             "notes.md": b"doc", "fonts/a.woff2": b"font"}
    chosen = publish_ui.select_files(files, {"img/new.png"})
    assert sorted(chosen) == ["img/new.png", "index.html", "js/app.js"]
    with pytest.raises(publish_ui.PublishError):
        publish_ui.select_files(files, {"notes.md"})  # такой файл программа из бандла не примет


def test_zip_is_reproducible(publish_ui):
    a = publish_ui.build_zip({"js/app.js": b"x" * 1000, "index.html": b"<html>"})
    b = publish_ui.build_zip({"index.html": b"<html>", "js/app.js": b"x" * 1000})
    assert a == b
    with zipfile.ZipFile(io.BytesIO(a)) as archive:
        assert archive.namelist() == ["index.html", "js/app.js"]


def test_tree_files_reads_git(publish_ui):
    files = publish_ui.tree_files("HEAD", "app/web/frontend/")
    assert "index.html" in files and "js/app.js" in files
    assert not any(name.startswith("/") or name.startswith("app/") for name in files)


def test_entry_is_accepted_by_program(publish_ui, tmp_path, content_server, monkeypatch):
    data = publish_ui.build_zip({"index.html": b"<html>server</html>", "js/app.js": b"// server"})
    entry = publish_ui.make_entry("desktop", "1.0.62", 3, data, TEST_SECRET)
    publish_ui.self_check("desktop", "1.0.62", entry, data, TEST_PUBLIC)
    with pytest.raises(publish_ui.PublishError):
        publish_ui.self_check("desktop", "1.0.62", entry, data, ed25519.public_key(bytes(32)))
    assert ui_bundle.PUBLIC_KEY != TEST_PUBLIC  # самопроверка не подменяет ключ программы насовсем
    content_server.add(f"ui/{entry['file']}", data)
    content_server.add("ui/manifest.json", json.dumps({"schema": 1, "desktop": {"1.0.62": entry}}).encode())
    monkeypatch.setattr(ui_bundle, "PUBLIC_KEY", TEST_PUBLIC)
    assert ui_bundle.refresh(content_server.url, tmp_path / "ui", "desktop", "1.0.62") == "updated"
    active = ui_bundle.active_dir(tmp_path / "ui", "desktop", "1.0.62")
    assert (active / "js/app.js").read_bytes() == b"// server"


def test_last_rev(publish_ui):
    assert publish_ui.last_rev(None) == 0
    assert publish_ui.last_rev({"rev": 4}) == 4
    assert publish_ui.last_rev({"rev": 0, "last_rev": 6}) == 6
    assert publish_ui.last_rev({"rev": True}) == 0


@pytest.fixture
def fake_release(publish_ui, monkeypatch):
    """Выпуск v1.0.62 с интерфейсом с сервера; сервер и git — подставные."""
    state = {"changed": [], "manifest": {"schema": 1}, "uploads": [], "written": None}
    constants = {"app/version.py": "1.0.62", "android/app/build.gradle.kts": "1.0.62",
                 "app/ui_bundle.py": TEST_PUBLIC.hex()}
    monkeypatch.setattr(publish_ui, "latest_tag", lambda ref: "v1.0.62")
    monkeypatch.setattr(publish_ui, "tag_constant", lambda tag, path, pattern: constants[path])
    monkeypatch.setattr(publish_ui, "changed_paths", lambda tag, ref: state["changed"])
    monkeypatch.setattr(publish_ui, "git", lambda *args, **kw: "")
    monkeypatch.setattr(publish_ui, "tree_files", lambda ref, prefix: {"index.html": prefix.encode(),
                                                                        "js/app.js": b"// js"})
    monkeypatch.setattr(publish_ui, "load_secret", lambda: TEST_SECRET)
    monkeypatch.setattr(publish_ui, "fetch_manifest", lambda: (json.loads(json.dumps(state["manifest"])), "sha0"))
    monkeypatch.setattr(publish_ui, "upload_file", lambda name, data: state["uploads"].append(name))
    monkeypatch.setattr(publish_ui, "write_manifest", lambda manifest, sha: state.update(written=(manifest, sha)))
    monkeypatch.setattr(publish_ui, "remove_old_zips", lambda manifest: None)
    monkeypatch.setattr(publish_ui, "verify_public", lambda platform, version, public: "ok")
    return state


def test_publish_both_platforms(publish_ui, fake_release):
    fake_release["changed"] = ["app/web/frontend/js/app.js", "android/app/src/main/assets/js/app.js", "tests/x.py"]
    assert publish_ui.main([]) == 0  # без --yes ничего не выкладывается
    assert fake_release["uploads"] == [] and fake_release["written"] is None
    assert publish_ui.main(["--yes"]) == 0
    assert fake_release["uploads"] == ["desktop-1.0.62-r1.zip", "android-1.0.62-r1.zip"]
    manifest, sha = fake_release["written"]
    assert sha == "sha0" and manifest["desktop"]["1.0.62"]["rev"] == 1 and manifest["android"]["1.0.62"]["rev"] == 1


def test_refuses_platform_with_native_changes(publish_ui, fake_release):
    fake_release["changed"] = ["app/web/frontend/js/app.js", "app/web/bridge.py",
                               "android/app/src/main/assets/js/app.js"]
    assert publish_ui.main(["--yes"]) == 0
    assert fake_release["uploads"] == ["android-1.0.62-r1.zip"]  # ПК — нет: мост поменялся после выпуска
    fake_release["uploads"].clear()
    fake_release["changed"] = ["app/web/frontend/js/app.js", "app/web/bridge.py"]
    assert publish_ui.main(["--yes", "--platform", "desktop"]) == 1
    assert fake_release["uploads"] == []
    assert publish_ui.main(["--yes", "--platform", "desktop", "--force"]) == 0
    assert fake_release["uploads"] == ["desktop-1.0.62-r1.zip"]


def test_same_bundle_is_not_republished_and_rev_grows(publish_ui, fake_release):
    fake_release["changed"] = ["app/web/frontend/js/app.js"]
    assert publish_ui.main(["--yes", "--platform", "desktop"]) == 0
    entry = fake_release["written"][0]["desktop"]["1.0.62"]
    fake_release["manifest"] = {"schema": 1, "desktop": {"1.0.62": entry}}
    fake_release["uploads"].clear()
    fake_release["written"] = None
    assert publish_ui.main(["--yes", "--platform", "desktop"]) == 0
    assert fake_release["uploads"] == [] and fake_release["written"] is None  # тот же архив — уже выложен
    # Выключить, потом выложить снова — номер выпуска не повторяется (программы помнят плохие выпуски).
    assert publish_ui.main(["--yes", "--platform", "desktop", "--disable"]) == 0
    assert fake_release["written"][0]["desktop"]["1.0.62"] == {"rev": 0, "last_rev": 1}
    fake_release["manifest"] = fake_release["written"][0]
    assert publish_ui.main(["--yes", "--platform", "desktop"]) == 0
    assert fake_release["uploads"] == ["desktop-1.0.62-r2.zip"]
    entry = fake_release["written"][0]["desktop"]["1.0.62"]
    assert entry["sha256"] == hashlib.sha256(publish_ui.build_zip(
        {"index.html": b"app/web/frontend/", "js/app.js": b"// js"})).hexdigest()


def test_refuses_release_without_ui_bundle(publish_ui, fake_release, monkeypatch):
    monkeypatch.setattr(publish_ui, "tag_constant",
                        lambda tag, path, pattern: None if path == "app/ui_bundle.py" else "1.0.62")
    with pytest.raises(publish_ui.PublishError, match="нет интерфейса с сервера"):
        publish_ui.main(["--yes"])


def test_refuses_foreign_signing_key(publish_ui, fake_release, monkeypatch):
    fake_release["changed"] = ["app/web/frontend/js/app.js"]
    monkeypatch.setattr(publish_ui, "load_secret", lambda: bytes(32))
    with pytest.raises(publish_ui.PublishError, match="не подходит"):
        publish_ui.main(["--yes"])
