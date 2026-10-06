"""Ключ и политика шифрования каталога (app/catalog_key.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

from app import catalog_key, catalog_crypto

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _reset():
    catalog_key.configure(None, None)
    yield
    catalog_key.configure(None, None)


def test_desktop_and_android_copies_are_byte_identical():
    assert (ROOT / "app/catalog_key.py").read_bytes() == \
        (ROOT / "android/app/src/main/python/catalog_key.py").read_bytes()


@pytest.mark.parametrize("rel", [
    "cars/Haval/H3/_wizard_spec.json",
    "cars/Haval/H3/stages.py",
    "cars/Haval/H3/install.py",
    "cars/Haval/H3/version.json",
    "cars/Chery/Tiggo 8 Pro Max/Рест/stages.py",          # модификация
    "cars/Haval/H3/files/instruction_1/instruction.html",
    "cars/Haval/H3/files/instruction_1/images/IMG_1.jpg",
    "cars/Geely/Cityray/files/flash_main/instruction.html",
    "cars/Geely/Cityray/files/flash_main/images/a.png",
])
def test_encrypted_paths(rel):
    assert catalog_key.is_encrypted_path(rel)


@pytest.mark.parametrize("rel", [
    "cars/Haval/H3/hero.webp",                             # миниатюра — не шифруем
    "cars/Haval/H3/logo.png",
    "cars/Haval/logo.png",                                 # лого марки
    "cars/Haval/H3/files/pack/optional/RuStore.apk",       # payload
    "cars/Haval/H3/files/instruction_1/video.mp4",         # видео не под images/
    "cars/Haval/H3/files/video_1/clip.mp4",
    "cars/Haval/H3/usb_files/step_1/firmware.bin",
    "cars/_shared/adb_permissions.py",                     # публичный движок
    "cars/_shared/motion_cert/private.pk8",
    "apk/RuStore.apk",                                     # общая библиотека
    "apk/RuStore.apk.json",
    "manifest.json",
    "device_models.json",
])
def test_plaintext_paths(rel):
    assert not catalog_key.is_encrypted_path(rel)


def test_without_key_everything_is_plaintext():
    assert not catalog_key.is_configured()
    assert catalog_key.encrypt_bytes(b"hello") == b"hello"              # пишем как есть
    assert catalog_key.decrypt_if_needed(b"<html>") == b"<html>"       # читаем как есть


def test_with_key_round_trip():
    catalog_key.configure(b"build-secret", bytes(range(32)))
    assert catalog_key.is_configured()
    blob = catalog_key.encrypt_bytes(b"<html>instruction</html>")
    assert catalog_crypto.is_encrypted(blob)
    assert catalog_key.decrypt_if_needed(blob) == b"<html>instruction</html>"
    assert catalog_key.decrypt_if_needed(b"plain") == b"plain"         # плейнтекст — как есть


def test_encrypted_file_without_key_raises():
    catalog_key.configure(b"s", bytes(range(32)))
    blob = catalog_key.encrypt_bytes(b"x")
    catalog_key.configure(None, None)  # ключ потерян (другая установка)
    with pytest.raises(RuntimeError, match="ключ не настроен"):
        catalog_key.decrypt_if_needed(blob)
