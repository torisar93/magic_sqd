"""Этап «Флешка» из блоков на Android: android/.../python/wizard_spec.py разбирает
flash_blocks, сохранённые настоящим генератором ПК (app/car_generator.py), — для
app.js: renderFlashBlocksStage. Инструкции блоков уже прочитаны (картинки на
appassets, как у этапа «Инструкция»), у инструкции одной строкой HTML нет (на
карточке не будет кнопки), файлы блока записи — абсолютными путями в
usb_files/step_N (их пишет тот же usb_run_stage). Этап с паролем сохраняется как
"qr_adb", но его приложения модели (usb_pack) по-прежнему видны."""
from __future__ import annotations
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

from app import car_generator as cg

ROOT = Path(__file__).resolve().parent.parent
WIZARD_SPEC_PATH = ROOT / "android/app/src/main/python/wizard_spec.py"


@pytest.fixture(scope="module")
def wizard_spec():
    spec = importlib.util.spec_from_file_location("android_wizard_spec_flash", WIZARD_SPEC_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_flash_blocks_reach_android_ready_to_render(tmp_path, wizard_spec):
    files_root = tmp_path / "files_root"
    cars = files_root / "cars"
    (cars / "_shared").mkdir(parents=True)
    shutil.copy(ROOT / "cars/_shared/load_sibling.py", cars / "_shared/load_sibling.py")
    (cars / "_shared" / "svengmode.flag").write_bytes(b"")
    src = tmp_path / "src"
    src.mkdir()
    (src / "adb.png").write_bytes(b"png")
    (src / "extra.apk").write_bytes(b"apk")
    step = cg.StepSpec(type="usb", title="Пароль ADB по QR-коду", flash_blocks=[
        cg.FlashBlockSpec(kind="write", files=[cars / "_shared" / "svengmode.flag"], copy_selected_apks=True,
                          apks_dest="apps", shared_folder="freetuga"),
        cg.FlashBlockSpec(kind="instruction", title="Включите ADB", instruction_blocks=[
            {"type": "photo", "path": str(src / "adb.png")}]),
        cg.FlashBlockSpec(kind="instruction", title="Дождитесь «QNX OK»"),
        cg.FlashBlockSpec(kind="password"),
    ], standard_apks_optional=[cg.StandardApkSpec(path=src / "extra.apk")])
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="Jolion", steps=[step]))

    stage = wizard_spec.load_wizard_spec(model_dir, files_root)["steps"][0]

    assert stage["type"] == "qr_adb" and stage["supported"] is True
    write, instruction, line, password = stage["flash_blocks"]
    assert write == {"kind": "write", "title": "", "files": [str(model_dir / "usb_files/step_1/svengmode.flag")],
                     "copy_selected_apks": True, "apks_dest": "apps", "shared_folder": "freetuga"}
    assert instruction["title"] == "Включите ADB"
    assert 'src="https://appassets.androidplatform.net/data/cars/Haval/Jolion/files/flash_' in \
        instruction["instruction_html"]
    assert line == {"kind": "instruction", "title": "Дождитесь «QNX OK»", "instruction_html": ""}
    assert password == {"kind": "password", "title": ""}
    # приложения модели у этапа, сохранённого как qr_adb, не теряются
    assert [Path(a["path"]).name for a in stage["standard_apks_optional"]] == ["extra.apk"]


def test_old_stage_without_blocks_has_empty_list(tmp_path, wizard_spec):
    cars = tmp_path / "cars"
    (cars / "_shared").mkdir(parents=True)
    shutil.copy(ROOT / "cars/_shared/load_sibling.py", cars / "_shared/load_sibling.py")
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Geely", model="Monjaro", steps=[
        cg.StepSpec(type="qr_adb", title="Пароль ADB по QR-коду")]))

    stage = wizard_spec.load_wizard_spec(model_dir)["steps"][0]

    assert stage["flash_blocks"] == []  # прежний этап — прежний вид (renderQrAdbStage)
