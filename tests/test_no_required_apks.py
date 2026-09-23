"""«Обязательных» приложений больше нет (решение владельца, 2026-09-23): есть
общий каталог и каталог модели, галочки техник ставит сам. Старые модели со
standard_apks (files/pack/required) не должны ни терять APK, ни навязывать
их: генератор при любом пересохранении переносит их в необязательные, а
десктоп показывает остатки в required/ обычными галочками. Android — см.
tests/test_android_wizard_spec_usb_apks.py."""
from __future__ import annotations
import json
import shutil
import types
from pathlib import Path

from app import car_generator as cg

REPO = Path(__file__).resolve().parents[1]


def make_old_model_with_required(tmp_path):
    """Модель «как до 2026-09-23»: create_car пишет спеку как есть, в т.ч.
    standard_apks → files/pack/required (как Geely Preface Обычная)."""
    cars = tmp_path / "cars"
    (cars / "_shared").mkdir(parents=True)
    shutil.copy(REPO / "cars/_shared/load_sibling.py", cars / "_shared/load_sibling.py")
    src = tmp_path / "src"
    src.mkdir()
    (src / "ginputbridge.apk").write_bytes(b"required-apk")
    (src / "climator.apk").write_bytes(b"optional-apk")
    spec = cg.NewCarSpec(brand="Geely", model="Preface", modification="Обычная", steps=[
        cg.StepSpec(type="apps", title="Установка приложений",
                    standard_apks=[cg.StandardApkSpec(path=src / "ginputbridge.apk", name="GInputBridge")],
                    standard_apks_optional=[cg.StandardApkSpec(path=src / "climator.apk")]),
    ])
    model_dir = cg.create_car(cars, spec)
    assert (model_dir / "files/pack/required/ginputbridge.apk").is_file()  # исходное состояние
    return cars, model_dir


def test_loading_an_old_spec_folds_required_into_optional(tmp_path):
    _cars, model_dir = make_old_model_with_required(tmp_path)

    spec = cg.load_car_spec(model_dir, "Geely", "Preface", "Обычная")

    step = spec.steps[0]
    assert step.standard_apks == []
    assert [apk.path.name for apk in step.standard_apks_optional] == ["ginputbridge.apk", "climator.apk"]
    assert step.standard_apks_optional[0].name == "GInputBridge"  # подпись не теряется


def test_resaving_moves_files_from_required_to_optional(tmp_path):
    cars, model_dir = make_old_model_with_required(tmp_path)
    spec = cg.load_car_spec(model_dir, "Geely", "Preface", "Обычная")

    cg.update_car(cars, model_dir, spec)

    pack = model_dir / "files/pack"
    assert not list((pack / "required").glob("*.apk"))  # glob несуществующей папки — просто пусто
    assert (pack / "optional/ginputbridge.apk").read_bytes() == b"required-apk"
    assert (pack / "optional/climator.apk").read_bytes() == b"optional-apk"
    saved = json.loads((model_dir / "_wizard_spec.json").read_text(encoding="utf-8"))["steps"][0]
    assert saved["standard_apks"] == []
    assert [e["filename"] if isinstance(e, dict) else e for e in saved["standard_apks_optional"]] == [
        "ginputbridge.apk", "climator.apk"]


def test_desktop_list_shows_leftover_required_files_as_regular_checkboxes(tmp_path):
    # Не перенесённая модель (или старая локальная копия у техника):
    # install_api.standard_apks больше не отдаёт "required" вовсе.
    from app.web.api.install_api import InstallApi
    _cars, model_dir = make_old_model_with_required(tmp_path)
    (model_dir / "files/pack/optional/ginputbridge.apk").write_bytes(b"same name in optional")
    model = types.SimpleNamespace(dir=model_dir, brand="Geely", name="Preface", modification="Обычная", key="k",
                                  stages_script=model_dir / "stages.py")
    scanner = types.SimpleNamespace(get_model=lambda key: model)
    api = InstallApi("adb", tmp_path, scanner)

    result = api.standard_apks("k", 0, None)

    assert result["required"] == []
    paths = [Path(apk["path"]) for apk in result["optional"]]
    assert sorted(p.name for p in paths) == ["climator.apk", "ginputbridge.apk"]  # без дубля
    assert model_dir / "files/pack/optional/ginputbridge.apk" in paths  # optional/ побеждает
