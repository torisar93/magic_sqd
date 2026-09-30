"""Служебные подпапки cars/_shared для кнопок «Доп. действий» качаются при запуске вместе со скриптами (лог №1985,
Haval Dargo, Android 1.0.53: «нет ключа подписи» — ключ «Работы в движении» лежит в cars/_shared/motion_cert, а
синхронизация при запуске пропускала любые подпапки _shared). Payload-папки _shared (наборы для usb-этапов) и
files/ моделей при запуске по-прежнему не качаются. Проверяются обе копии: ПК и Android (Chaquopy)."""
from __future__ import annotations
from importlib import util
from pathlib import Path

from app import content_sync

ANDROID_CONTENT_SYNC = Path(__file__).resolve().parents[1] / "android/app/src/main/python/content_sync.py"


def _fill(server):
    server.add("cars/_shared/adb_permissions.py", b"# helper")
    server.add("cars/_shared/motion_cert/private.pk8", b"K" * 1217)
    server.add("cars/_shared/motion_cert/certificate.crt", b"C" * 1100)
    server.add("cars/_shared/freetuga/big.bin", b"F" * 5000)  # payload usb-этапа — только перед этапом
    server.add("cars/Haval/Dargo/install.py", b"# model")
    server.add("cars/Haval/Dargo/files/pack/app.apk", b"A" * 3000)
    server.write_manifest()


def _check(cars: Path):
    assert (cars / "_shared/adb_permissions.py").is_file()
    assert (cars / "_shared/motion_cert/private.pk8").read_bytes() == b"K" * 1217
    assert (cars / "_shared/motion_cert/certificate.crt").read_bytes() == b"C" * 1100
    assert (cars / "Haval/Dargo/install.py").is_file()
    assert not (cars / "_shared/freetuga").exists()
    assert not (cars / "Haval/Dargo/files").exists()


def test_desktop_startup_sync_brings_motion_cert(content_server, app_base):
    _fill(content_server)
    manifest = content_sync.fetch_manifest(content_server.url)
    content_sync.sync_scripts(app_base, app_base / "cars", manifest=manifest)
    _check(app_base / "cars")


def test_android_startup_sync_brings_motion_cert(content_server, tmp_path):
    _fill(content_server)
    spec = util.spec_from_file_location("android_content_sync", ANDROID_CONTENT_SYNC)
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cars = tmp_path / "phone" / "cars"
    cars.mkdir(parents=True)
    mod.sync_scripts(content_server.url, cars)
    _check(cars)


def test_both_copies_list_the_same_startup_folders():
    spec = util.spec_from_file_location("android_content_sync_list", ANDROID_CONTENT_SYNC)
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.STARTUP_SHARED_FOLDERS == content_sync.STARTUP_SHARED_FOLDERS
    assert "motion_cert" in content_sync.STARTUP_SHARED_FOLDERS
