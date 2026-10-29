"""macOS, первый запуск (29.10.2026): папки данных ~/Library/Application Support/MagicSQD ещё нет — .catalog_key молча
не записывался, каталог первого запуска шифровался ключом, который жил только в памяти, и после перезапуска не читался
(«не прошла проверка подлинности», пустой каталог). Теперь ключ сохраняется с первого запуска, а файлы модели под чужим
ключом удаляются при старте — синхронизация скачает их заново."""
from __future__ import annotations

import sys

import pytest

from app import catalog_crypto, catalog_key, catalog_setup

SECRET = b"build-secret-for-tests-0123456789"


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(catalog_setup, "get_app_build_secret", lambda base_dir: SECRET)
    monkeypatch.setattr(catalog_setup, "get_base_url", lambda base_dir: "")
    monkeypatch.setattr(catalog_setup, "_register_decrypt_hook", lambda base_dir: None)
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield
    catalog_key.configure(None, None)


def _encrypted(device_random: bytes, text: bytes) -> bytes:
    return catalog_crypto.encrypt(catalog_crypto.derive_key(SECRET, device_random), text)


def test_first_run_saves_key_before_folder_exists(tmp_path, setup):
    base = tmp_path / "Application Support" / "MagicSQD"
    catalog_setup.configure(base)
    key_file = base / ".catalog_key"
    assert key_file.is_file() and len(key_file.read_bytes()) == 32
    first = key_file.read_bytes()
    catalog_setup.configure(base)  # перезапуск — тот же ключ
    assert key_file.read_bytes() == first


def test_files_under_other_key_are_dropped_others_kept(tmp_path, setup):
    base = tmp_path / "MagicSQD"
    model = base / "cars" / "Haval" / "Jolion" / "2026"
    model.mkdir(parents=True)
    old, new = b"o" * 32, b"n" * 32
    (base / ".catalog_key").write_bytes(new)
    (model / "version.json").write_bytes(_encrypted(old, b'{"revision": 1}'))
    (model / "stages.py").write_bytes(_encrypted(old, b"STAGES = []"))
    (model / "hero.webp").write_bytes(b"RIFF....WEBP")  # не шифруется — не трогаем
    other = base / "cars" / "Geely" / "Atlas"
    other.mkdir(parents=True)
    (other / "version.json").write_bytes(_encrypted(new, b'{"revision": 2}'))  # уже под нынешним ключом
    shared = base / "cars" / "_shared"
    shared.mkdir()
    (shared / "load_sibling.py").write_bytes(b"def load_install(f): pass\n")

    catalog_setup.configure(base)
    assert not (model / "version.json").exists() and not (model / "stages.py").exists()
    assert (model / "hero.webp").exists() and (shared / "load_sibling.py").exists()
    assert catalog_key.read_text(other / "version.json") == '{"revision": 2}'


def test_readable_catalog_is_not_scanned_further(tmp_path, setup, monkeypatch):
    base = tmp_path / "MagicSQD"
    model = base / "cars" / "Haval" / "Jolion" / "2026"
    model.mkdir(parents=True)
    key = b"k" * 32
    (base / ".catalog_key").write_bytes(key)
    (model / "version.json").write_bytes(_encrypted(key, b"{}"))
    calls = []
    real = catalog_key.decrypt_if_needed
    monkeypatch.setattr(catalog_key, "decrypt_if_needed", lambda data: calls.append(1) or real(data))
    catalog_setup.configure(base)
    assert len(calls) == 1 and (model / "version.json").exists()
