"""Общая библиотека APK: скачанным считается только то, что скачала сама программа на этом устройстве (владелец,
14.10.2026: «если я сам руками в папку запихну каталог — чтобы он не работал»; офлайн-каталог — только подписчикам).
Реестр apk_registry.json — подпись ключом установки; файлы не шифруются. Обе копии: ПК и Android."""
from __future__ import annotations

import filecmp
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from app import apk_registry, catalog_key, scanner
from app import content_sync as desktop

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"
SECRET = bytes(range(32))
MTIME = 1_700_000_000.0


@pytest.fixture
def keyed(monkeypatch):
    """Официальная сборка: ключ установки настроен (без него реестр не действует — запуск из исходников)."""
    monkeypatch.setattr(catalog_key, "_key", None)
    catalog_key.configure(SECRET, b"device-one")
    yield
    catalog_key.configure(None, None)


def _serve(content_server, rel, data):
    path = content_server.add(rel, data)
    os.utime(path, (MTIME, MTIME))
    content_server.write_manifest()


def test_copies_are_identical():
    assert filecmp.cmp(ROOT / "app/apk_registry.py", ANDROID_PY / "apk_registry.py", shallow=False)
    assert filecmp.cmp(ROOT / "app/catalog_key.py", ANDROID_PY / "catalog_key.py", shallow=False)


def test_only_files_downloaded_here_are_trusted(tmp_path, keyed, monkeypatch):
    apk_dir = tmp_path / "app" / "apk"
    own = apk_dir / "Навигация" / "Nav.apk"
    own.parent.mkdir(parents=True)
    own.write_bytes(b"nav-v1")
    apk_registry.record(apk_dir, "Навигация/Nav.apk", own)
    assert apk_registry.is_trusted(apk_dir, "Навигация/Nav.apk", own)

    copied = apk_dir / "Навигация" / "Other.apk"   # положили руками — записи нет
    shutil.copy2(own, copied)
    assert not apk_registry.is_trusted(apk_dir, "Навигация/Other.apk", copied)

    own.unlink()
    shutil.copy2(copied, own)  # подменили файл тем же содержимым — это уже другой файл на диске
    assert not apk_registry.is_trusted(apk_dir, "Навигация/Nav.apk", own)

    # Папка вместе с реестром перенесена в другую установку (другой случайный секрет) — подпись не сходится.
    apk_registry.record(apk_dir, "Навигация/Nav.apk", own)
    assert apk_registry.is_trusted(apk_dir, "Навигация/Nav.apk", own)
    catalog_key.configure(SECRET, b"device-two")
    assert not apk_registry.is_trusted(apk_dir, "Навигация/Nav.apk", own)

    catalog_key.configure(None, None)  # запуск из исходников — как раньше
    assert apk_registry.is_trusted(apk_dir, "Навигация/Other.apk", copied)


def test_hand_placed_catalog_is_not_listed_as_downloaded(tmp_path, keyed):
    apk_dir = tmp_path / "apk"
    (apk_dir / "Видео").mkdir(parents=True)
    own = apk_dir / "Видео" / "Own.apk"
    own.write_bytes(b"own")
    apk_registry.record(apk_dir, "Видео/Own.apk", own)
    (apk_dir / "Видео" / "Stuffed.apk").write_bytes(b"stuffed")   # есть и на сервере
    (apk_dir / "Видео" / "Random.apk").write_bytes(b"random")     # на сервере нет вовсе
    remote = [{"rel_path": "Видео/Own.apk", "size": 3}, {"rel_path": "Видео/Stuffed.apk", "size": 7}]
    found = {a.path.name: a.remote_only for a in scanner.scan_apks(apk_dir, remote)}
    assert found == {"Own.apk": False, "Stuffed.apk": True}  # подложенный — «на сервере», чужой — не виден


def test_desktop_redownloads_or_sets_aside_hand_placed_files(content_server, app_base, keyed):
    _serve(content_server, "apk/Видео/App.apk", b"server-copy")
    apk_dir = app_base / "apk"
    stuffed = apk_dir / "Видео" / "App.apk"
    stuffed.parent.mkdir(parents=True)
    stuffed.write_bytes(b"server-copy")          # тот же размер — раньше считался скачанным
    os.utime(stuffed, (MTIME, MTIME))
    logs = []
    assert desktop.ensure_apks_downloaded(app_base, apk_dir, [stuffed], log=logs.append) == 1
    assert apk_registry.is_trusted(apk_dir, "Видео/App.apk", stuffed)
    assert any("положен не программой" in line for line in logs)
    assert desktop.ensure_apks_downloaded(app_base, apk_dir, [stuffed]) == 0  # свой — не качаем снова

    # Без сервера (нет интернета) подложенный файл не ставится: уходит в apk/_unverified/.
    gone = apk_dir / "Видео" / "Gone.apk"
    gone.write_bytes(b"not-on-server")
    assert desktop.ensure_apks_downloaded(app_base, apk_dir, [gone]) == 0
    assert not gone.exists() and (apk_dir / "_unverified" / "Видео" / "Gone.apk").exists()


def _load_android(monkeypatch, *names):
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    for name in names:
        spec = importlib.util.spec_from_file_location(name, ANDROID_PY / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return [sys.modules[name] for name in names]


def test_android_same_rules(content_server, tmp_path, monkeypatch):
    key_mod, registry, _cs, library = _load_android(monkeypatch, "catalog_key", "apk_registry", "content_sync",
                                                     "apk_library")
    key_mod.configure(SECRET, b"phone")
    _serve(content_server, "apk/Видео/App.apk", b"server-copy")
    apk_dir = tmp_path / "phone" / "apk"
    stuffed = apk_dir / "Видео" / "App.apk"
    stuffed.parent.mkdir(parents=True)
    stuffed.write_bytes(b"server-copy")
    os.utime(stuffed, (MTIME, MTIME))
    listed = json.loads(library.list_apks(apk_dir, content_server.url))
    assert [(a["name"], a.get("remote_only", False)) for a in listed] == [("App", True)]
    assert library.ensure_apks_downloaded(apk_dir, tmp_path / "phone" / "cars", content_server.url, [str(stuffed)]) == 1
    assert registry.is_trusted(apk_dir, "Видео/App.apk", stuffed)
    listed = json.loads(library.list_apks(apk_dir, content_server.url))
    assert [(a["name"], a.get("remote_only", False)) for a in listed] == [("App", False)]
