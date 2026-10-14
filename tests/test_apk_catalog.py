"""«Каталог APK» — весь каталог приложений для работы без интернета, только подписчикам (владелец, 14.10.2026):
кнопка рядом со «Скачать заранее»; скачал — кнопка пропадает, каталог на сервере обновился — «Обновить каталог APK».
Скачанным считается только скачанное самой программой (apk_registry): положенный вручную каталог не засчитывается.
Обе платформы: ПК (app/apk_catalog.py + offline_api.py) и Android (apk_catalog.py + mobile_bridge.py)."""
from __future__ import annotations

import filecmp
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

from app import apk_catalog, catalog_key
from app.web.api import offline_api as offline_api_module
from app.web.api.offline_api import OfflineApi

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"
MTIME = 1_700_000_000.0


class _Auth:
    def __init__(self, subscriber=True, ok=True):
        self.subscriber, self.ok = subscriber, ok

    def refresh_subscriber(self):
        return {"ok": self.ok, "subscriber": self.subscriber} if self.ok else {"ok": False}


def _publish(server, extra=None):
    files = {"apk/Навигация/navi.apk": b"N" * 9000, "apk/Навигация/navi.json": b'{"name": "Navi"}',
             "apk/Видео/video.apk": b"V" * 4000, "apk/_служебное/tool.apk": b"T" * 100,  # «_» — не каталог
             "cars/Haval/F7/stages.py": b"x"}
    files.update(extra or {})
    for rel, data in files.items():
        path = server.add(rel, data)
        os.utime(path, (MTIME, MTIME))
    return server.write_manifest()


def _load_android(monkeypatch, *names):
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    for name in names:
        spec = importlib.util.spec_from_file_location(name, ANDROID_PY / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return [sys.modules[name] for name in names]


@pytest.fixture(params=["desktop", "android"])
def platform(request, monkeypatch, app_base, content_server):
    if request.param == "desktop":
        events = []
        monkeypatch.setattr(offline_api_module.event_bridge, "push", events.append)
        monkeypatch.setattr(catalog_key, "_key", None)
        catalog_key.configure(bytes(range(32)), b"pc")
        api = OfflineApi(app_base, None, _Auth())

        class Desktop:
            name = "desktop"

            def status(self):
                api._manifest = None
                return api.apk_catalog_status()

            def download(self):
                return api.download_apk_catalog()

        yield Desktop()
        catalog_key.configure(None, None)
    else:
        # Все андроидные модули реестра — свежие: иначе apk_registry из прошлого теста держит чужой catalog_key
        key, *_mods, bridge = _load_android(monkeypatch, "catalog_key", "apk_registry", "content_sync", "apk_catalog",
                                            "apk_library", "mobile_bridge")
        key.configure(bytes(range(32)), b"phone")

        class Android:
            name = "android"

            def status(self):
                bridge._offline_manifest["data"] = None
                return json.loads(bridge.apk_catalog_status(str(app_base / "apk"), content_server.url))

            def download(self):
                return json.loads(bridge.apk_catalog_download(str(app_base / "apk"), content_server.url))

        yield Android()


def test_android_copy_is_identical():
    assert filecmp.cmp(ROOT / "app/apk_catalog.py", ANDROID_PY / "apk_catalog.py", shallow=False)


def test_catalog_downloads_once_then_the_button_disappears(app_base, content_server, platform):
    _publish(content_server)
    before = platform.status()
    assert before["state"] == "none" and before["available"] and before["files"] == 3  # 2 APK + описание
    assert before["missing_bytes"] == 9000 + 4000 + len(b'{"name": "Navi"}')
    result = platform.download()
    assert result["ok"] and result["state"] == "done" and result["failed"] == 0
    assert (app_base / "apk" / "Видео" / "video.apk").read_bytes() == b"V" * 4000
    assert not (app_base / "apk" / "_служебное").exists()
    assert platform.status()["state"] == "done"  # всё на месте — кнопки нет


def test_new_app_on_server_asks_to_update_only_the_difference(app_base, content_server, platform):
    _publish(content_server)
    platform.download()
    _publish(content_server, {"apk/Музыка/music.apk": b"M" * 2500})
    st = platform.status()
    assert st["state"] == "update" and st["missing_bytes"] == 2500
    assert platform.download()["state"] == "done"


def test_hand_placed_catalog_does_not_count(app_base, content_server, platform):
    _publish(content_server)
    stuffed = app_base / "apk" / "Видео" / "video.apk"
    stuffed.parent.mkdir(parents=True)
    stuffed.write_bytes(b"V" * 4000)  # тот же файл, что на сервере, но положен руками
    os.utime(stuffed, (MTIME, MTIME))
    assert platform.status()["missing_bytes"] >= 4000
    assert platform.download()["state"] == "done"
    assert (app_base / "apk" / "_unverified" / "Видео" / "video.apk").exists()  # отложен, заменён копией с сервера


def test_desktop_needs_subscription_checked_on_server(app_base, content_server):
    _publish(content_server)
    assert OfflineApi(app_base, None, _Auth(subscriber=False)).apk_catalog_download() == {"ok": False, "locked": True}
    assert OfflineApi(app_base, None, _Auth(ok=False)).apk_catalog_download()["locked"] is True


def test_without_internet_no_button_unless_downloaded(app_base, platform, content_server):
    manifest = _publish(content_server)
    assert apk_catalog.status(None, app_base / "apk") == {"state": "none", "available": False, "files": 0,
                                                          "total_bytes": 0, "missing_bytes": 0}
    apk_catalog.write_marker(app_base / "apk")
    assert apk_catalog.status(None, app_base / "apk")["state"] == "done"
    assert apk_catalog.status(manifest, app_base / "apk")["state"] == "update"  # отметка есть, файлов нет


def test_button_lives_in_the_model_card_and_both_ui_copies_match():
    for rel in ("js/offline.js", "js/catalog-ui.js", "css/offline.css"):
        assert filecmp.cmp(ROOT / "app/web/frontend" / rel, ROOT / "android/app/src/main/assets" / rel, shallow=False)
    ui = (ROOT / "app/web/frontend/js/catalog-ui.js").read_text(encoding="utf-8")
    assert "model-apkcat" in ui and "OfflineUI.bindCatalog" in ui
    offline = (ROOT / "app/web/frontend/js/offline.js").read_text(encoding="utf-8")
    for event in ("apk_catalog_status", "apk_catalog_progress", "apk_catalog_finished"):
        assert event in offline
    assert "Обновить каталог APK" in offline
