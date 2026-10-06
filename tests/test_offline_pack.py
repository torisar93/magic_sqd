"""«Скачать заранее» (подписчики Boosty, владелец 2026-10-05): все файлы модели на устройство, чтобы у машины работать
без интернета. Пакет — files/ и usb_files/ модели и общие наборы флешки (cars/_shared/<имя>) из её этапов; отметка
_offline.json — по ней значок «офлайн»/«обновить» в списке без сети; удаление — по нажатию на «Доступна офлайн».
Обе платформы: ПК (app/offline_pack.py + app/web/api/offline_api.py) и Android (offline_pack.py + mobile_bridge.py)."""
from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app import content_sync, offline_pack
from app.web.api import offline_api as offline_api_module
from app.web.api.offline_api import OfflineApi, OfflineCancelled
from app.web.api.scanner_api import ScannerApi

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"

MODEL = "Haval/F7"
SPEC = {"steps": [
    {"type": "apps", "standard_apks": [{"filename": "app.apk"}]},
    {"type": "usb", "usb_files": ["fw.bin"], "usb_shared_folder": "freetuga"},
    {"type": "qr_adb", "flash_blocks": [{"id": "1a2b3c4d", "kind": "write", "shared_folder": "flash"}]},
]}


def _publish(server, *, apk=b"A" * 3000, extra_model=True):
    server.add(f"cars/{MODEL}/_wizard_spec.json", json.dumps(SPEC).encode())
    server.add(f"cars/{MODEL}/stages.py", b"x")
    server.add(f"cars/{MODEL}/version.json", b'{"revision": 1}')
    server.add(f"cars/{MODEL}/files/pack/optional/app.apk", apk)
    server.add(f"cars/{MODEL}/files/instruction_1/instruction.html", b"<p>hi</p>")
    server.add(f"cars/{MODEL}/files/resign_cert/cert.pem", b"cert")
    server.add(f"cars/{MODEL}/usb_files/fw.bin", b"F" * 2000)
    server.add("cars/_shared/freetuga/maps/map.dat", b"M" * 5000)
    server.add("cars/_shared/flash/svlog.flag", b"1")
    server.add("cars/_shared/motion_cert/key.pem", b"k")      # не из этапов модели — не в пакете
    server.add("apk/Навигация/navi.apk", b"N" * 9000)          # общая библиотека — техник выбирает сам
    if extra_model:
        server.add("cars/Haval/Jolion/_wizard_spec.json", json.dumps({"steps": [
            {"type": "usb", "usb_shared_folder": "freetuga"}]}).encode())
        server.add("cars/Haval/Jolion/stages.py", b"x")
        server.add("cars/Haval/Jolion/files/pack/optional/j.apk", b"J" * 100)
    return server.write_manifest()


def _load_android(monkeypatch, *names):
    monkeypatch.syspath_prepend(str(ANDROID_PY))  # catalog_crypto/catalog_key — плоские импорты как в Chaquopy
    for name in names:  # андроидные модули импортируют друг друга по короткому имени
        spec = importlib.util.spec_from_file_location(name, ANDROID_PY / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return [sys.modules[name] for name in names]


class _Auth:
    def __init__(self, subscriber=True, ok=True):
        self.subscriber, self.ok = subscriber, ok

    def refresh_subscriber(self):
        return {"ok": self.ok, "subscriber": self.subscriber} if self.ok else {"ok": False}


def _sync_catalog(app_base, server_url):
    manifest = content_sync.fetch_manifest(server_url)
    content_sync.sync_scripts(app_base, app_base / "cars", manifest=manifest)
    return manifest


@pytest.fixture(params=["desktop", "android"])
def platform(request, monkeypatch, app_base, content_server):
    """Скачать / состояние / удалить — через настоящий бэкенд платформы."""
    cars = app_base / "cars"

    class Desktop:
        name = "desktop"

        def __init__(self):
            self.events = []
            monkeypatch.setattr(offline_api_module.event_bridge, "push", self.events.append)
            self.scanner = ScannerApi(cars, app_base / "apk")
            self.api = OfflineApi(app_base, self.scanner, _Auth())

        def sync(self):
            manifest = _sync_catalog(app_base, content_server.url)
            offline_pack.refresh_all(manifest, cars)
            self.scanner.list_cars()

        def download(self, model_dir):
            self.api._manifest = None
            return self.api.download_pack(model_dir)

        def status(self, model_dir):
            self.api._manifest = None
            return self.api.status(str(model_dir))

        def delete(self, model_dir):
            return self.api.delete(str(model_dir))

        def offline_states(self):
            return {m["key"]: m["offline_state"] for b in self.scanner.list_cars()["brands"]
                    for g in b["groups"] for m in [g["leaf"], *g["modifications"]] if m}

    class Android:
        name = "android"

        def __init__(self):
            monkeypatch.syspath_prepend(str(ANDROID_PY))  # mobile_bridge: wizard_spec, apk_library …
            _, self.sync_mod, _, self.bridge = _load_android(monkeypatch, "offline_pack", "content_sync", "scanner",
                                                             "mobile_bridge")

        def sync(self):
            json.loads(self.bridge.sync_cars(str(cars), content_server.url))

        def download(self, model_dir):
            return json.loads(self.bridge.offline_download(str(cars), content_server.url, str(model_dir)))

        def status(self, model_dir):
            self.bridge._offline_manifest["data"] = None
            return json.loads(self.bridge.offline_status(str(cars), content_server.url, str(model_dir)))

        def delete(self, model_dir):
            return json.loads(self.bridge.offline_delete(str(cars), str(model_dir)))

        def offline_states(self):
            data = json.loads(self.bridge.list_cars(str(cars)))
            return {m["key"]: m["offline_state"] for b in data["brands"]
                    for g in b["groups"] for m in [g["leaf"], *g["modifications"]] if m}

    return Desktop() if request.param == "desktop" else Android()


def test_android_copy_is_identical():
    assert (ROOT / "app/offline_pack.py").read_bytes() == (ANDROID_PY / "offline_pack.py").read_bytes(), (
        "app/offline_pack.py (ПК) и android/.../python/offline_pack.py разошлись — правьте одну и копируйте в другую")


def test_pack_is_model_files_and_its_shared_flash_sets_not_the_library(app_base, content_server):
    manifest = _publish(content_server)
    _sync_catalog(app_base, content_server.url)
    paths = {item["path"] for item in offline_pack.pack_items(manifest, app_base / "cars", app_base / "cars" / MODEL)}
    assert paths == {
        f"cars/{MODEL}/files/pack/optional/app.apk", f"cars/{MODEL}/files/instruction_1/instruction.html",
        f"cars/{MODEL}/files/resign_cert/cert.pem", f"cars/{MODEL}/usb_files/fw.bin",
        "cars/_shared/freetuga/maps/map.dat", "cars/_shared/flash/svlog.flag",
    }


def test_download_puts_everything_on_disk_and_marks_offline(app_base, content_server, platform):
    _publish(content_server)
    platform.sync()
    model_dir = app_base / "cars" / MODEL
    before = platform.status(model_dir)
    assert before["state"] == "none" and before["available"] is True
    assert before["missing_bytes"] == 3000 + 9 + 4 + 2000 + 5000 + 1

    result = platform.download(model_dir)

    assert result["ok"] is True and result["state"] == "done" and result["failed"] == 0
    assert (model_dir / "files/pack/optional/app.apk").read_bytes() == b"A" * 3000
    assert (model_dir / "usb_files/fw.bin").is_file()
    assert (app_base / "cars/_shared/freetuga/maps/map.dat").is_file()
    assert not (app_base / "apk/Навигация/navi.apk").exists()
    assert platform.offline_states()[str(model_dir)] == "done"
    assert platform.status(model_dir)["state"] == "done"


def test_new_file_on_server_turns_badge_to_update_and_update_downloads_only_it(app_base, content_server, platform):
    _publish(content_server)
    platform.sync()
    model_dir = app_base / "cars" / MODEL
    platform.download(model_dir)
    untouched = model_dir / "usb_files/fw.bin"
    inode = untouched.stat().st_ino  # перекачка подменила бы файл (новый .part → новый inode)

    _publish(content_server, apk=b"B" * 3500)  # админ заменил APK
    platform.sync()

    assert platform.offline_states()[str(model_dir)] == "update"
    status = platform.status(model_dir)
    assert status["state"] == "update" and status["missing_bytes"] == 3500
    assert platform.download(model_dir)["state"] == "done"
    assert (model_dir / "files/pack/optional/app.apk").read_bytes() == b"B" * 3500
    assert untouched.stat().st_ino == inode


def test_badge_survives_without_internet(app_base, content_server, platform):
    _publish(content_server)
    platform.sync()
    model_dir = app_base / "cars" / MODEL
    platform.download(model_dir)
    content_server.close()  # у машины сети нет

    status = platform.status(model_dir)
    assert status["state"] == "done" and status["online"] is False and status["available"] is True
    assert platform.offline_states()[str(model_dir)] == "done"


def test_delete_removes_pack_but_keeps_shared_set_needed_by_another_offline_model(app_base, content_server, platform):
    _publish(content_server)
    platform.sync()
    model_dir = app_base / "cars" / MODEL
    jolion = app_base / "cars/Haval/Jolion"
    platform.download(model_dir)
    platform.download(jolion)

    result = platform.delete(model_dir)

    assert result["ok"] is True and result["state"] == "none" and result["freed"] >= 3000 + 2000 + 1
    assert not (model_dir / "files/pack/optional/app.apk").exists()
    assert not (model_dir / "usb_files/fw.bin").exists()
    assert not (model_dir / offline_pack.MARKER).exists()
    assert not (app_base / "cars/_shared/flash/svlog.flag").exists()   # нужен был только F7
    assert (app_base / "cars/_shared/freetuga/maps/map.dat").is_file()  # нужен скачанной Jolion
    assert (model_dir / "stages.py").is_file()                          # модель осталась в списке
    assert platform.offline_states()[str(model_dir)] is None
    assert platform.offline_states()[str(jolion)] == "done"


def test_pruned_stale_file_on_model_open_marks_update(app_base, content_server):
    _publish(content_server)
    manifest = _sync_catalog(app_base, content_server.url)
    model_dir = app_base / "cars" / MODEL
    OfflineApi(app_base, None, _Auth()).download_pack(model_dir)
    assert offline_pack.marker_state(model_dir) == "done"

    manifest = _publish(content_server, apk=b"C" * 10)
    content_sync.prune_model_stale_files(app_base, model_dir, manifest)  # открытие модели (load_stages)

    assert offline_pack.marker_state(model_dir) == "update"


def test_own_or_locally_edited_model_has_no_button(app_base, content_server):
    _publish(content_server)
    _sync_catalog(app_base, content_server.url)
    scanner = ScannerApi(app_base / "cars", app_base / "apk")
    own = app_base / "cars/Мой/Авто"
    own.mkdir(parents=True)
    (own / "_wizard_spec.json").write_text("{}", encoding="utf-8")
    (own / "stages.py").write_text("x", encoding="utf-8")
    scanner.list_cars()
    api = OfflineApi(app_base, scanner, _Auth())
    assert api.status(str(own))["available"] is False
    content_sync.mark_local_edit(app_base / "cars" / MODEL)
    assert api.status(str(app_base / "cars" / MODEL))["available"] is False


def test_desktop_download_needs_subscription_checked_on_server(app_base, content_server):
    _publish(content_server)
    _sync_catalog(app_base, content_server.url)
    scanner = ScannerApi(app_base / "cars", app_base / "apk")
    scanner.list_cars()
    key = str(app_base / "cars" / MODEL)
    assert OfflineApi(app_base, scanner, _Auth(subscriber=False)).download(key) == {"ok": False, "locked": True}
    assert OfflineApi(app_base, scanner, _Auth(ok=False)).download(key)["locked"] is True
    assert not (app_base / "cars" / MODEL / offline_pack.MARKER).exists()


def test_desktop_cancel_leaves_no_offline_mark(app_base, content_server):
    _publish(content_server)
    _sync_catalog(app_base, content_server.url)
    api = OfflineApi(app_base, None, _Auth())
    api._cancel.set()
    with pytest.raises(OfflineCancelled):
        api.download_pack(app_base / "cars" / MODEL)
    assert offline_pack.marker_state(app_base / "cars" / MODEL) is None


def test_android_cancel_leaves_no_offline_mark(app_base, content_server, monkeypatch):
    _publish(content_server)
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    *_, bridge = _load_android(monkeypatch, "offline_pack", "content_sync", "scanner", "mobile_bridge")
    cars = app_base / "cars"
    json.loads(bridge.sync_cars(str(cars), content_server.url))

    class Sink:
        def line(self, _text):
            pass

        def progress(self, done, total):
            bridge._offline_cancel.set()  # «нажал на кнопку» на первом же куске

    result = json.loads(bridge.offline_download(str(cars), content_server.url, str(cars / MODEL), Sink()))
    assert result["cancelled"] is True and result["state"] == "none"
    assert offline_pack.marker_state(cars / MODEL) is None


@pytest.mark.parametrize("index", [ROOT / "app/web/frontend/index.html", ROOT / "android/app/src/main/assets/index.html"])
def test_offline_ui_is_included_after_catalog_ui(index):
    html = index.read_text(encoding="utf-8")
    assert 'href="css/offline.css"' in html
    assert html.index('src="js/catalog-ui.js"') < html.index('src="js/offline.js"')
