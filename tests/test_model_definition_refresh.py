"""Описание опубликованной модели (stages.py, _wizard_spec.json, version.json) обновляется при ОТКРЫТИИ модели, а не
только при запуске программы. Логи №8650–8660 (Android 1.1.12, Atlas New Обычная, 22.10.2026): программа жила в фоне с
правки модели 18.10 (приложения из «обязательных» перенесли в необязательные), сверка при открытии убрала старые
files/pack/required/*, а старая спека просила их снова — HTTP 404 шесть раз и окно «Нет интернета». Обе копии: ПК
(app/content_sync.py) и Android (android/.../python/content_sync.py)."""
from __future__ import annotations
import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

from app import content_sync as desktop

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"
MODEL = "Geely/Atlas New/Обычная"
OLD, NEW = 1_700_000_000.0, 1_760_000_000.0
NEW_SPEC = b'{"steps": [{"type": "apps", "standard_apks": [], "standard_apks_optional": ["ginputbridge.apk"]}]}'


def _load_android(monkeypatch):
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    for name in ("offline_pack", "content_sync"):
        spec = importlib.util.spec_from_file_location(name, ANDROID_PY / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return sys.modules["content_sync"]


def _local(path, data, mtime=OLD):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))


@pytest.fixture(params=["ПК", "Android"])
def setup(request, tmp_path, monkeypatch):
    base = tmp_path / "app"
    model = base / "cars" / MODEL
    _local(model / "_wizard_spec.json", b'{"steps": [{"type": "apps", "standard_apks": ["ginputbridge.apk"]}]}')
    _local(model / "stages.py", b"old")
    _local(model / "files/pack/optional/button.apk", b"apk!")
    server = {f"cars/{MODEL}/_wizard_spec.json": NEW_SPEC, f"cars/{MODEL}/stages.py": b"new stages",
              f"cars/{MODEL}/files/pack/optional/button.apk": b"APK v2 bigger",
              f"cars/{MODEL}/files/pack/optional/ginputbridge.apk": b"gib"}
    manifest = {path: {"size": len(data), "mtime": NEW} for path, data in server.items()}
    fetched = []

    def download_file(base_url, remote_path, dest, *args, mtime=None, **kwargs):
        fetched.append(remote_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(server[remote_path])
        if mtime:
            os.utime(dest, (mtime, mtime))

    if request.param == "ПК":
        monkeypatch.setattr(desktop, "get_base_url", lambda base_dir: "https://example.test")
        monkeypatch.setattr(desktop, "download_file", download_file)
        run = lambda: desktop.sync_model_definition(base, model, manifest)
    else:
        android = _load_android(monkeypatch)
        monkeypatch.setattr(android, "download_file", download_file)
        run = lambda: android.sync_model_definition("https://example.test", base / "cars", model, manifest)
    return types.SimpleNamespace(model=model, run=run, fetched=fetched, manifest=manifest)


def test_stale_spec_and_stages_refreshed_payload_untouched(setup):
    assert setup.run() == 2
    assert (setup.model / "_wizard_spec.json").read_bytes() == NEW_SPEC
    assert (setup.model / "stages.py").read_bytes() == b"new stages"
    # files/ — не здесь: APK качаются по клику на этапе, а старые убирает сверка при открытии
    assert sorted(setup.fetched) == [f"cars/{MODEL}/_wizard_spec.json", f"cars/{MODEL}/stages.py"]
    assert (setup.model / "files/pack/optional/button.apk").read_bytes() == b"apk!"
    assert setup.run() == 0  # уже свежее — второй раз ничего не качаем


def test_model_not_on_server_or_offline_untouched(setup):
    del setup.manifest[f"cars/{MODEL}/_wizard_spec.json"]  # своя модель техника — на сервере её нет
    assert setup.run() == 0 and setup.fetched == []


def test_unpublished_local_edit_is_kept(tmp_path, monkeypatch):
    """ПК: у модели с неопубликованной правкой (_local_edit.json) описание с сервера не затирает правку."""
    base = tmp_path / "app"
    model = base / "cars" / MODEL
    _local(model / "_wizard_spec.json", b"my edit")
    (model / "_local_edit.json").write_text("{}", encoding="utf-8")
    manifest = {f"cars/{MODEL}/_wizard_spec.json": {"size": len(NEW_SPEC), "mtime": NEW}}
    monkeypatch.setattr(desktop, "get_base_url", lambda base_dir: "https://example.test")
    monkeypatch.setattr(desktop, "download_file", lambda *a, **k: pytest.fail("правку затёрли"))
    assert desktop.sync_model_definition(base, model, manifest) == 0
    assert (model / "_wizard_spec.json").read_bytes() == b"my edit"


def test_model_open_refreshes_definition_before_reading_spec():
    """Порядок важен: сначала описание, потом чтение спеки/этапов."""
    bridge = (ANDROID_PY / "mobile_bridge.py").read_text(encoding="utf-8")
    body = bridge[bridge.index("def sync_payload("):]
    assert body.index("sync_model_definition(") < body.index('read_text(model_dir / "_wizard_spec.json")')
    api = (ROOT / "app/web/api/install_api.py").read_text(encoding="utf-8")
    body = api[api.index("    def load_stages(self, model_key: str)"):]
    assert body.index("sync_model_definition(") < body.index("stages = load_stages(model)")


@pytest.mark.parametrize("platform", ["ПК", "Android"])
def test_file_missing_from_server_catalog_is_reported(platform, tmp_path, monkeypatch):
    """Файла нет ни на диске, ни в манифесте — вызывающий скажет «нет на сервере», а не «нет интернета»."""
    base = tmp_path / "app"
    model = base / "cars" / MODEL
    gone = model / "files/pack/required/ginputbridge.apk"
    there = model / "files/pack/optional/button.apk"
    manifest = {f"cars/{MODEL}/_wizard_spec.json": {"size": 2, "mtime": NEW},
                f"cars/{MODEL}/files/pack/optional/button.apk": {"size": 4, "mtime": NEW}}

    def download_file(base_url, remote_path, dest, *args, mtime=None, **kwargs):
        if remote_path not in manifest:
            raise module.ContentSyncError("HTTP Error 404: Not Found")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"apk!")

    missing = []
    if platform == "ПК":
        module = desktop
        monkeypatch.setattr(desktop, "get_base_url", lambda base_dir: "https://example.test")
        monkeypatch.setattr(desktop, "fetch_manifest", lambda url: manifest)
        monkeypatch.setattr(desktop, "download_file", download_file)
        desktop.ensure_apks_downloaded(base, base / "apk", [str(gone), str(there)], not_on_server=missing)
    else:
        _load_android(monkeypatch)
        spec = importlib.util.spec_from_file_location("apk_library", ANDROID_PY / "apk_library.py")
        library = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, "apk_library", library)
        spec.loader.exec_module(library)
        module = library
        monkeypatch.setattr(library, "fetch_manifest", lambda url: manifest)
        monkeypatch.setattr(library, "download_file", download_file)
        library.ensure_apks_downloaded(base / "apk", base / "cars", "https://example.test", [str(gone), str(there)],
                                       not_on_server=missing)
    assert missing == ["ginputbridge.apk"] and there.is_file()
    assert desktop.not_on_server_message(missing).startswith("Этих файлов нет на сервере: ginputbridge.apk.")
