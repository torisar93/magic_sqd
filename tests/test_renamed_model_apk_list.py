"""Переименование в админке (владелец, 2026-10-02: «сделай безопасное переименование»): у техника, скачавшего файл
модели раньше, локально остаётся старое имя — ПК показывал бы в списке и его (локальные файлы + серверные). Для
опубликованной модели в списке только то, что есть на сервере; своя неотправленная модель — как была."""
from __future__ import annotations
import shutil
import types
from pathlib import Path

from app import car_generator as cg
from app import content_sync
from app.web.api.install_api import InstallApi

PREFIX = "cars/Haval/Jolion/2026"
REPO = Path(__file__).resolve().parents[1]


def _model(tmp_path):
    cars = tmp_path / "cars"
    (cars / "_shared").mkdir(parents=True)
    shutil.copy(REPO / "cars/_shared/load_sibling.py", cars / "_shared/load_sibling.py")
    src = tmp_path / "src"
    src.mkdir()
    (src / "MagicSQD_WheelKeys_1.1.apk").write_bytes(b"old")
    spec = cg.NewCarSpec(brand="Haval", model="Jolion", modification="2026", steps=[
        cg.StepSpec(type="apps", title="Приложения",
                    standard_apks_optional=[cg.StandardApkSpec(path=src / "MagicSQD_WheelKeys_1.1.apk")])])
    model_dir = cg.create_car(cars, spec)
    assert (model_dir / "files/pack/optional/MagicSQD_WheelKeys_1.1.apk").is_file()  # скачан раньше
    return model_dir


def _api(tmp_path, model_dir, manifest):
    model = types.SimpleNamespace(dir=model_dir, brand="Haval", name="Jolion", modification="2026", key="k",
                                  stages_script=model_dir / "stages.py")
    api = InstallApi("adb", tmp_path, types.SimpleNamespace(get_model=lambda key: model))
    api._get_manifest = lambda fresh=False: manifest
    return api


def _names(api):
    return [apk["path"].replace("\\", "/").rsplit("/", 1)[-1] for apk in api.standard_apks("k", 0, None)["optional"]]


SERVER = {f"{PREFIX}/_wizard_spec.json": {"size": 1, "mtime": 1.0},
          f"{PREFIX}/files/pack/optional/MagicSQD_WheelKeys_1.3.apk": {"size": 3, "mtime": 1.0}}


def test_renamed_on_server_shows_only_the_new_name(tmp_path):
    model_dir = _model(tmp_path)
    assert _names(_api(tmp_path, model_dir, SERVER)) == ["MagicSQD_WheelKeys_1.3.apk"]


def test_own_unsent_model_and_offline_keep_local_files(tmp_path):
    model_dir = _model(tmp_path)
    # нет связи с сервером — показываем, что есть
    assert _names(_api(tmp_path, model_dir, None)) == ["MagicSQD_WheelKeys_1.1.apk"]
    # модели нет на сервере (своя, ещё не отправленная) — локальные файлы её и есть
    local_only = {k: v for k, v in SERVER.items() if not k.endswith("_wizard_spec.json")}
    assert "MagicSQD_WheelKeys_1.1.apk" in _names(_api(tmp_path, model_dir, local_only))
    # неотправленная правка опубликованной модели — тоже как была
    content_sync.mark_local_edit(model_dir)
    assert "MagicSQD_WheelKeys_1.1.apk" in _names(_api(tmp_path, model_dir, SERVER))
