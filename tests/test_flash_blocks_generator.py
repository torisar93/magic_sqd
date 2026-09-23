"""Этап «Флешка» из блоков (владелец, 2026-09-23): «USB-флешка» и «Пароль ADB
по QR-коду» в редакторе — один этап, блоки «Инструкция» / «Запись на флешку»
(просто файлы и папки — svengmode.flag/svlog.flag тоже обычные файлы — и по
галочке приложения) / «Получить пароль» в любом порядке и количестве. Здесь —
как генератор (app/car_generator.py) это сохраняет: папки инструкций блоков не
переезжают при перестановке (фото не теряются), тип этапа и прежние поля
пересчитываются для старых версий программы, прежние этапы раскладываются в
блоки при открытии в редакторе."""
from __future__ import annotations
import json
import shutil
import types
from pathlib import Path

import pytest

from app import car_generator as cg
from app.stage_runner import load_stages

REPO = Path(__file__).resolve().parents[1]


def _cars(tmp_path) -> Path:
    cars = tmp_path / "cars"
    (cars / "_shared").mkdir(parents=True)
    shutil.copy(REPO / "cars/_shared/load_sibling.py", cars / "_shared/load_sibling.py")
    (cars / "_shared" / "svengmode.flag").write_bytes(b"eng")
    (cars / "_shared" / "svlog.flag").write_bytes(b"log")
    return cars


def _src(tmp_path) -> Path:
    src = tmp_path / "src"
    (src / "freetuga").mkdir(parents=True)
    (src / "freetuga" / "run.sh").write_text("echo hi", encoding="utf-8")
    (src / "update.bin").write_bytes(b"firmware")
    (src / "adb.png").write_bytes(b"png-adb")
    (src / "open.png").write_bytes(b"png-open")
    return src


def _model(model_dir: Path):
    return types.SimpleNamespace(dir=model_dir, stages_script=model_dir / "stages.py")


def _haval_steps(src: Path) -> list[cg.StepSpec]:
    shared = src.parent / "cars" / "_shared"
    return [
        cg.StepSpec(type="usb", title="Пароль ADB по QR-коду", flash_blocks=[
            cg.FlashBlockSpec(kind="write", files=[shared / "svengmode.flag"]),
            cg.FlashBlockSpec(kind="instruction", title="Включите ADB", instruction_blocks=[
                {"type": "steps", "text": "Перейдите в подраздел «ADB»\nНажмите «ADB Open»"},
                {"type": "photo", "path": str(src / "adb.png"), "caption": ""},
            ]),
            cg.FlashBlockSpec(kind="write", files=[shared / "svlog.flag"]),
            cg.FlashBlockSpec(kind="instruction", title="Вставьте флешку и дождитесь «QNX OK»"),
            cg.FlashBlockSpec(kind="password"),
        ]),
        cg.StepSpec(type="usb", title="Файлы", flash_blocks=[
            cg.FlashBlockSpec(kind="write", files=[src / "update.bin", src / "freetuga"],
                              copy_selected_apks=True, apks_dest="apps", shared_folder="freetuga"),
            cg.FlashBlockSpec(kind="instruction", instruction_blocks=[{"type": "p", "text": "Вставьте флешку."}]),
        ]),
    ]


def test_blocks_are_saved_for_new_and_old_app_versions(tmp_path):
    cars, src = _cars(tmp_path), _src(tmp_path)
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="Jolion", steps=_haval_steps(src)))

    saved = json.loads((model_dir / "_wizard_spec.json").read_text(encoding="utf-8"))["steps"]
    qr, files = saved
    # Тип — из блоков: с паролем "qr_adb" (старая версия покажет прежние шаги QR, с
    # инженерным меню — раз среди файлов svengmode.flag), без него "usb" (запишет всё разом).
    assert qr["type"] == "qr_adb" and qr["qr_adb_engineering_menu"] is True
    assert [b["kind"] for b in qr["flash_blocks"]] == ["write", "instruction", "write", "instruction", "password"]
    assert [b.get("files") for b in qr["flash_blocks"]] == [["svengmode.flag"], None, ["svlog.flag"], None, None]
    assert qr["usb_files"] == ["svengmode.flag", "svlog.flag"]
    assert (model_dir / "usb_files/step_1/svlog.flag").read_bytes() == b"log"
    assert files["type"] == "usb" and files["qr_adb_engineering_menu"] is False
    assert files["usb_files"] == ["update.bin", "freetuga"]
    assert files["usb_copy_selected_apks"] is True and files["usb_apks_dest"] == "apps"
    assert files["usb_shared_folder"] == "freetuga"
    assert files["flash_blocks"][0]["files"] == ["update.bin", "freetuga"]

    instr_id = qr["flash_blocks"][1]["id"]
    instr_dir = model_dir / "files" / f"flash_{instr_id}"
    assert (instr_dir / "images" / "adb.png").read_bytes() == b"png-adb"
    assert "images/adb.png" in (instr_dir / "instruction.html").read_text(encoding="utf-8")
    assert not (model_dir / "files" / "instruction_1").exists()  # у этапа из блоков общей инструкции нет
    assert (model_dir / "usb_files/step_2/update.bin").read_bytes() == b"firmware"
    assert (model_dir / "usb_files/step_2/freetuga/run.sh").is_file()

    # инструкция одной строкой (без HTML) — папки у неё нет, ссылки тоже
    assert "instruction" not in load_stages(_model(model_dir))[0]["flash_blocks"][3]
    assert not (model_dir / "files" / f"flash_{qr['flash_blocks'][3]['id']}").exists()

    stages = load_stages(_model(model_dir))  # stages.py проходит проверку старых и новых версий
    assert stages[0]["type"] == "qr_adb" and stages[0]["qr_adb_engineering_menu"] is True
    assert stages[0]["flash_blocks"][1]["instruction"] == f"files/flash_{instr_id}/instruction.html"
    assert stages[0]["flash_blocks"][2]["files"] == [model_dir / "usb_files/step_1/svlog.flag"]
    assert callable(stages[1]["run"])  # прежняя запись всего этапа — для старых версий
    write = stages[1]["flash_blocks"][0]
    assert write["files"] == [model_dir / "usb_files/step_2/update.bin", model_dir / "usb_files/step_2/freetuga"]
    assert write["copy_selected_apks"] is True and write["apks_dest"] == "apps"
    assert write["shared_folder"] == "freetuga"


def test_blocks_round_trip_through_the_editor_loader(tmp_path):
    cars, src = _cars(tmp_path), _src(tmp_path)
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="Jolion", steps=_haval_steps(src)))

    spec = cg.load_car_spec(model_dir, "Haval", "Jolion")

    qr, files = spec.steps
    assert [b.kind for b in qr.flash_blocks] == ["write", "instruction", "write", "instruction", "password"]
    assert qr.flash_blocks[0].files == [model_dir / "usb_files/step_1/svengmode.flag"]
    assert qr.flash_blocks[3].title == "Вставьте флешку и дождитесь «QNX OK»"
    assert qr.flash_blocks[3].instruction_blocks == []
    photo = qr.flash_blocks[1].instruction_blocks[1]
    assert Path(photo["path"]) == (model_dir / "files" / f"flash_{qr.flash_blocks[1].id}" / "images/adb.png").resolve()
    assert files.flash_blocks[0].files == [model_dir / "usb_files/step_2/update.bin",
                                           model_dir / "usb_files/step_2/freetuga"]
    assert files.flash_blocks[1].instruction_blocks == [{"type": "p", "text": "Вставьте флешку."}]

    before = {p.relative_to(model_dir) for p in model_dir.rglob("*") if p.is_file()} - {Path("version.json")}
    cg.update_car(cars, model_dir, spec)  # пересохранение без правок ничего не теряет
    after = {p.relative_to(model_dir) for p in model_dir.rglob("*") if p.is_file()} - {Path("version.json")}
    assert before == after


def test_reordering_instruction_blocks_keeps_their_photos(tmp_path):
    cars, src = _cars(tmp_path), _src(tmp_path)
    steps = [cg.StepSpec(type="usb", title="QR", flash_blocks=[
        cg.FlashBlockSpec(kind="instruction", instruction_blocks=[{"type": "photo", "path": str(src / "adb.png")}]),
        cg.FlashBlockSpec(kind="password"),
        cg.FlashBlockSpec(kind="instruction", instruction_blocks=[{"type": "photo", "path": str(src / "open.png")}]),
    ])]
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="Jolion", steps=steps))
    (src / "adb.png").unlink()
    (src / "open.png").unlink()  # остались только копии внутри модели

    spec = cg.load_car_spec(model_dir, "Haval", "Jolion")
    blocks = spec.steps[0].flash_blocks
    spec.steps[0].flash_blocks = [blocks[2], blocks[1], blocks[0]]
    cg.update_car(cars, model_dir, spec)

    reloaded = cg.load_car_spec(model_dir, "Haval", "Jolion").steps[0].flash_blocks
    assert [b.kind for b in reloaded] == ["instruction", "password", "instruction"]
    assert Path(reloaded[0].instruction_blocks[0]["path"]).read_bytes() == b"png-open"
    assert Path(reloaded[2].instruction_blocks[0]["path"]).read_bytes() == b"png-adb"


def test_removed_instruction_block_takes_its_folder_with_it(tmp_path):
    cars, src = _cars(tmp_path), _src(tmp_path)
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="Jolion", steps=_haval_steps(src)))
    spec = cg.load_car_spec(model_dir, "Haval", "Jolion")
    gone = spec.steps[0].flash_blocks.pop(1)

    cg.update_car(cars, model_dir, spec)

    assert not (model_dir / "files" / f"flash_{gone.id}").exists()


def test_removed_instruction_text_leaves_a_plain_line(tmp_path):
    # «Убрать инструкцию» в редакторе: у техника остаётся строка без кнопки — HTML с
    # диска не должен «на всякий случай» пережить сохранение.
    cars, src = _cars(tmp_path), _src(tmp_path)
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="Jolion", steps=_haval_steps(src)))
    spec = cg.load_car_spec(model_dir, "Haval", "Jolion")
    block = spec.steps[0].flash_blocks[1]
    block.instruction_blocks = []

    cg.update_car(cars, model_dir, spec)

    assert not (model_dir / "files" / f"flash_{block.id}").exists()
    assert "instruction" not in load_stages(_model(model_dir))[0]["flash_blocks"][1]


def test_same_file_name_from_different_places_in_two_write_blocks_is_rejected(tmp_path):
    cars, src = _cars(tmp_path), _src(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / "update.bin").write_bytes(b"another firmware")
    steps = [cg.StepSpec(type="usb", title="Две флешки", flash_blocks=[
        cg.FlashBlockSpec(kind="write", files=[src / "update.bin"]),
        cg.FlashBlockSpec(kind="write", files=[other / "update.bin"]),
    ])]

    with pytest.raises(cg.CarGenerationError, match="update.bin"):
        cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="Jolion", steps=steps))


def test_the_same_file_in_two_write_blocks_is_stored_once(tmp_path):
    cars, src = _cars(tmp_path), _src(tmp_path)
    steps = [cg.StepSpec(type="usb", title="Две флешки", flash_blocks=[
        cg.FlashBlockSpec(kind="write", files=[src / "update.bin"]),
        cg.FlashBlockSpec(kind="write", files=[src / "update.bin", src / "freetuga"]),
    ])]

    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="Jolion", steps=steps))

    saved = json.loads((model_dir / "_wizard_spec.json").read_text(encoding="utf-8"))["steps"][0]
    assert saved["usb_files"] == ["update.bin", "freetuga"]
    assert [b["files"] for b in saved["flash_blocks"]] == [["update.bin"], ["update.bin", "freetuga"]]


def _legacy_qr(tmp_path, *, engineering_menu: bool, with_instruction: bool = False):
    cars, src = _cars(tmp_path), _src(tmp_path)
    step = cg.StepSpec(type="qr_adb", title="Пароль ADB по QR-коду", qr_adb_engineering_menu=engineering_menu,
                       instruction_blocks=([{"type": "photo", "path": str(src / "adb.png")}]
                                           if with_instruction else []))
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Geely", model="Monjaro", steps=[step]))
    return cars, model_dir


def test_legacy_qr_stage_becomes_the_same_chain_of_blocks(tmp_path):
    cars, model_dir = _legacy_qr(tmp_path, engineering_menu=False)
    step = cg.load_car_spec(model_dir, "Geely", "Monjaro").steps[0]

    assert cg.convert_legacy_flash_step(step, cars / "_shared") is True

    assert [b.kind for b in step.flash_blocks] == ["write", "instruction", "password"]
    assert step.flash_blocks[0].files == [cars / "_shared/svlog.flag"]  # флаг — обычный файл блока
    steps_text = step.flash_blocks[1].instruction_blocks[0]
    assert steps_text["type"] == "steps"
    assert steps_text["text"].splitlines() == list(cg.LEGACY_QR_ADB_STEPS)


def test_legacy_engineering_menu_stage_gets_both_flashes_and_its_instruction(tmp_path):
    cars, model_dir = _legacy_qr(tmp_path, engineering_menu=True, with_instruction=True)
    spec = cg.load_car_spec(model_dir, "Geely", "Monjaro")
    step = spec.steps[0]
    assert (model_dir / "files/instruction_1/images/adb.png").is_file()

    cg.convert_legacy_flash_step(step, cars / "_shared")

    assert [b.kind for b in step.flash_blocks] == ["write", "instruction", "write", "instruction", "password"]
    assert [b.files for b in step.flash_blocks[::2][:2]] == [[cars / "_shared/svengmode.flag"],
                                                               [cars / "_shared/svlog.flag"]]
    assert step.flash_blocks[1].instruction_blocks[0]["text"].splitlines() == list(cg.LEGACY_QR_ADB_PREP_STEPS)
    assert step.flash_blocks[2].title == "Запишите второй файл на флешку"
    # прежняя инструкция этапа — в конце шагов на магнитоле, как её и показывало окно
    assert step.flash_blocks[3].instruction_blocks[-1]["type"] == "photo"

    cg.update_car(cars, model_dir, spec)

    saved = json.loads((model_dir / "_wizard_spec.json").read_text(encoding="utf-8"))["steps"][0]
    assert saved["type"] == "qr_adb" and saved["qr_adb_engineering_menu"] is True
    assert (model_dir / "usb_files/step_1/svengmode.flag").read_bytes() == b"eng"
    assert not (model_dir / "files/instruction_1").exists()  # фото переехало в блок
    photo_block_id = saved["flash_blocks"][3]["id"]
    assert (model_dir / "files" / f"flash_{photo_block_id}/images/adb.png").read_bytes() == b"png-adb"


def test_legacy_usb_stage_becomes_one_write_block(tmp_path):
    cars, src = _cars(tmp_path), _src(tmp_path)
    step = cg.StepSpec(type="usb", title="Флешка", usb_files=[src / "update.bin"], usb_copy_selected_apks=True,
                       usb_apks_dest="APK", usb_shared_folder="freetuga")
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Belgee", model="S50", steps=[step]))
    step = cg.load_car_spec(model_dir, "Belgee", "S50").steps[0]

    cg.convert_legacy_flash_step(step, cars / "_shared")

    [block] = step.flash_blocks
    assert (block.kind, block.copy_selected_apks, block.apks_dest, block.shared_folder) == (
        "write", True, "APK", "freetuga")
    assert block.files == [model_dir / "usb_files/step_1/update.bin"]


def test_stage_with_variants_stays_as_it_was(tmp_path):
    step = cg.StepSpec(type="usb", title="Флешка", variants=[cg.StepVariant(name="Full")])

    assert cg.convert_legacy_flash_step(step, Path("_shared")) is False
    assert step.flash_blocks == []
