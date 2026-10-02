"""Сверка опубликованной модели с сервером при открытии (владелец, 2026-10-02): «если модель не была заранее сохранена
клиентом и не является его личной модификацией, то при открытии программа должна всегда сверяться с сервером, удалять
локально всякие старые апк, если они были скачаны в прошлой версии инструкции, и быть готовой загрузить новые; файлы не
должны дублироваться». Обе копии: ПК (app/content_sync.py) и Android (android/.../python/content_sync.py)."""
from __future__ import annotations
import importlib.util
import os
import shutil
import sys
import types
from pathlib import Path

import pytest

from app import car_generator as cg
from app import content_sync as desktop

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"
MODEL = "Haval/Jolion/2026"
SERVER_MTIME = 1_700_000_000.0


def _load_android(monkeypatch, *names):
    for name in names:  # apk_library делает "from content_sync import ..." — андроидный из той же папки
        spec = importlib.util.spec_from_file_location(name, ANDROID_PY / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return [sys.modules[name] for name in names]


def _local(model_dir, rel, data, mtime=SERVER_MTIME):
    path = model_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


def _manifest(entries, model=MODEL):
    manifest = {f"cars/{model}/_wizard_spec.json": {"size": 2, "mtime": SERVER_MTIME}}
    manifest.update({f"cars/{model}/{rel}": {"size": size, "mtime": mtime} for rel, (size, mtime) in entries.items()})
    return manifest


@pytest.fixture(params=["ПК", "Android"])
def prune(request, tmp_path, monkeypatch):
    base = tmp_path / "app"
    model_dir = base / "cars" / MODEL
    model_dir.mkdir(parents=True)
    (model_dir / "_wizard_spec.json").write_text("{}", encoding="utf-8")
    if request.param == "ПК":
        def run(manifest):
            return desktop.prune_model_stale_files(base, model_dir, manifest)
    else:
        (android,) = _load_android(monkeypatch, "content_sync")

        def run(manifest):
            return android.prune_model_to_server(base / "cars", model_dir, manifest)
    return types.SimpleNamespace(model=model_dir, run=run)


def test_renamed_removed_and_old_versions_go_current_files_stay(prune):
    m = prune.model
    renamed = _local(m, "files/pack/optional/MagicSQD_WheelKeys_1.1.apk", b"old")     # на сервере теперь _1.3
    outdated = _local(m, "files/pack/optional/FreeZona.apk", b"v1")                    # на сервере новая версия
    current = _local(m, "files/pack/optional/Current.apk", b"same!")
    legacy = _local(m, "files/pack/required/Legacy.apk", b"x")                         # «обязательных» больше нет
    flag = _local(m, "usb_files/step_1/svlog.flag", b"1")
    old_image = _local(m, "files/instruction_1/images/old.png", b"png")                # прошлая версия инструкции
    signed = _local(m, "files/pack/optional/Current_resigned.apk", b"signed")          # копия Android-переподписи
    downloading = _local(m, "files/pack/optional/Big.apk.part", b"half")                # идущая докачка
    (m / "_known_files.json").write_text("[]", encoding="utf-8")                        # снимок прежней схемы
    manifest = _manifest({"files/pack/optional/Current.apk": (5, SERVER_MTIME),
                          "files/pack/optional/FreeZona.apk": (7, SERVER_MTIME),
                          "files/pack/optional/MagicSQD_WheelKeys_1.3.apk": (3, SERVER_MTIME),
                          "usb_files/step_1/svlog.flag": (1, SERVER_MTIME),
                          "files/instruction_1/instruction.html": (9, SERVER_MTIME)})

    removed = prune.run(manifest)

    assert sorted(removed) == sorted(["files/pack/optional/MagicSQD_WheelKeys_1.1.apk", "files/pack/optional/FreeZona.apk",
                                      "files/pack/required/Legacy.apk", "files/instruction_1/images/old.png"])
    for path in (renamed, outdated, legacy, old_image):
        assert not path.exists()
    for path in (current, flag, signed, downloading):
        assert path.exists()
    assert not (m / "files/pack/required").exists() and not (m / "files/instruction_1").exists()  # пустые папки — тоже
    assert prune.run(manifest) == []  # повторное открытие ничего не трогает


def test_copy_downloaded_by_old_program_without_server_time(prune):
    # До 1.0.56 докачанный APK получал время скачивания, а не сервера.
    kept = _local(prune.model, "files/pack/optional/Same.apk", b"12345", mtime=SERVER_MTIME + 3600)       # после правки
    replaced = _local(prune.model, "files/pack/optional/Replaced.apk", b"12345", mtime=SERVER_MTIME - 3600)  # до замены
    manifest = _manifest({"files/pack/optional/Same.apk": (5, SERVER_MTIME),
                          "files/pack/optional/Replaced.apk": (5, SERVER_MTIME)})

    assert prune.run(manifest) == ["files/pack/optional/Replaced.apk"]
    assert kept.exists() and abs(kept.stat().st_mtime - SERVER_MTIME) < 1  # время сервера поставлено — дальше точно
    assert not replaced.exists()


def test_own_model_personal_edit_and_offline_are_untouched(prune):
    stale = _local(prune.model, "files/pack/optional/Mine.apk", b"mine")
    assert prune.run(None) == []                                                     # нет связи с сервером
    other = {k.replace(MODEL, "Haval/Other"): v for k, v in _manifest({}).items()}
    assert prune.run(other) == []                                                    # своя модель — её нет на сервере
    (prune.model / "_local_edit.json").write_text('{"saved_at": 9999999999}', encoding="utf-8")
    assert prune.run(_manifest({})) == []                                            # личная правка техника
    assert stale.exists()


def test_desktop_open_model_checks_with_server(tmp_path):
    from app.web.api.install_api import InstallApi
    cars = tmp_path / "cars"
    (cars / "_shared").mkdir(parents=True)
    shutil.copy(ROOT / "cars/_shared/load_sibling.py", cars / "_shared/load_sibling.py")
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="Jolion", modification="2026", steps=[
        cg.StepSpec(type="apps", title="Приложения")]))
    stale = _local(model_dir, "files/pack/optional/MagicSQD_WheelKeys_1.1.apk", b"old")
    model = types.SimpleNamespace(dir=model_dir, brand="Haval", name="Jolion", modification="2026", key="k",
                                  stages_script=model_dir / "stages.py")
    api = InstallApi("adb", tmp_path, types.SimpleNamespace(get_model=lambda key: model))
    api._get_manifest = lambda: _manifest({"files/pack/optional/MagicSQD_WheelKeys_1.3.apk": (3, SERVER_MTIME)})

    assert "stages" in api.load_stages("k")
    assert not stale.exists()


def _serve(content_server, rel, data, mtime):
    path = content_server.add(rel, data)
    os.utime(path, (mtime, mtime))
    content_server.write_manifest()


def test_desktop_download_replaces_same_size_file_changed_on_server(content_server, app_base):
    rel = f"cars/{MODEL}/files/pack/optional/App.apk"
    _serve(content_server, rel, b"NEW-1", SERVER_MTIME)
    local = _local(app_base / "cars" / MODEL, "files/pack/optional/App.apk", b"OLD-1", mtime=SERVER_MTIME - 3600)

    assert desktop.ensure_apks_downloaded(app_base, app_base / "apk", [local]) == 1
    assert local.read_bytes() == b"NEW-1" and abs(local.stat().st_mtime - SERVER_MTIME) < 1  # время сервера
    assert desktop.ensure_apks_downloaded(app_base, app_base / "apk", [local]) == 0  # свежий — не качаем снова


def test_android_download_replaces_same_size_file_changed_on_server(content_server, tmp_path, monkeypatch):
    _android_cs, apk_library = _load_android(monkeypatch, "content_sync", "apk_library")
    rel = f"cars/{MODEL}/files/pack/optional/App.apk"
    _serve(content_server, rel, b"NEW-1", SERVER_MTIME)
    cars = tmp_path / "phone" / "cars"
    local = _local(cars / MODEL, "files/pack/optional/App.apk", b"OLD-1", mtime=SERVER_MTIME - 3600)

    assert apk_library.ensure_apks_downloaded(tmp_path / "phone" / "apk", cars, content_server.url, [str(local)]) == 1
    assert local.read_bytes() == b"NEW-1" and abs(local.stat().st_mtime - SERVER_MTIME) < 1
    assert apk_library.ensure_apks_downloaded(tmp_path / "phone" / "apk", cars, content_server.url, [str(local)]) == 0
