"""Этап «Флешка» из блоков на ПК: что уходит в интерфейс (install_api.
_stage_to_dict — инструкции блоков с встроенными фото, описание записи) и как
пишется ОДИН блок записи (usb_api с block — только его файлы, выбранные
приложения и общий набор; обычная папка вместо флешки). Модель собирается
настоящим генератором, stages.py загружается настоящим stage_runner."""
from __future__ import annotations
import shutil
import types
from pathlib import Path

from app import car_generator as cg
from app.stage_runner import load_stages
from app.web.api import usb_api
from app.web.api.install_api import InstallApi

REPO = Path(__file__).resolve().parents[1]


def _make(tmp_path):
    base = tmp_path / "app"
    cars = base / "cars"
    (cars / "_shared" / "freetuga").mkdir(parents=True)
    (cars / "_shared" / "freetuga" / "tool.sh").write_text("tool", encoding="utf-8")
    shutil.copy(REPO / "cars/_shared/load_sibling.py", cars / "_shared/load_sibling.py")
    src = tmp_path / "src"
    (src / "maps").mkdir(parents=True)
    (src / "maps" / "a.map").write_bytes(b"map")
    (src / "update.bin").write_bytes(b"firmware")
    (src / "second.bin").write_bytes(b"second")
    (src / "adb.png").write_bytes(b"\x89PNG-adb")
    steps = [cg.StepSpec(type="usb", title="Флешки", flash_blocks=[
        cg.FlashBlockSpec(kind="write", files=[src / "update.bin", src / "maps"], copy_selected_apks=True,
                          apks_dest="apps", shared_folder="freetuga"),
        cg.FlashBlockSpec(kind="instruction", title="Шаги", instruction_blocks=[
            {"type": "steps", "text": "Раз\nДва"}, {"type": "photo", "path": str(src / "adb.png")}]),
        cg.FlashBlockSpec(kind="write", title="Вторая флешка", files=[src / "second.bin"]),
        cg.FlashBlockSpec(kind="instruction", title="Дождитесь «QNX OK»"),
        cg.FlashBlockSpec(kind="password"),
    ])]
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="Jolion", steps=steps))
    model = types.SimpleNamespace(dir=model_dir, stages_script=model_dir / "stages.py", key="Haval/Jolion",
                                  brand="Haval", name="Jolion", modification="")
    scanner = types.SimpleNamespace(get_model=lambda key: model if key == model.key else None)
    apk = base / "apk" / "navi.apk"
    apk.parent.mkdir()
    apk.write_bytes(b"apk")
    return base, model, scanner, apk


def test_stage_dict_describes_blocks_with_inlined_instruction(tmp_path):
    base, model, scanner, _apk = _make(tmp_path)
    stage = load_stages(model)[0]

    data = InstallApi("adb", base, scanner)._stage_to_dict(model, 0, stage)

    assert data["type"] == "qr_adb"  # есть блок пароля
    blocks = data["flash_blocks"]
    assert [b["kind"] for b in blocks] == ["write", "instruction", "write", "instruction", "password"]
    assert blocks[0] == {"kind": "write", "title": "", "file_names": ["update.bin", "maps"],
                         "copy_selected_apks": True, "apks_dest": "apps", "shared_folder": "freetuga"}
    assert blocks[1]["title"] == "Шаги"
    assert "<li>Раз</li>" in blocks[1]["instruction_html"]
    assert 'src="data:image/png;base64,' in blocks[1]["instruction_html"]  # фото встроено для iframe srcdoc
    assert blocks[2]["file_names"] == ["second.bin"] and blocks[2]["copy_selected_apks"] is False
    # инструкция одной строкой: без HTML — у техника без кнопки
    assert blocks[3] == {"kind": "instruction", "title": "Дождитесь «QNX OK»", "instruction_html": None}
    assert blocks[4] == {"kind": "password", "title": ""}


def test_list_items_and_write_of_one_block(tmp_path, monkeypatch):
    base, model, scanner, apk = _make(tmp_path)
    api = usb_api.UsbApi(base, scanner)
    events = []
    monkeypatch.setattr(usb_api.event_bridge, "push", events.append)

    items = api.list_items(model.key, 0, None, [str(apk)], 0)

    assert [i["name"] for i in items["items"]] == ["update.bin", "a.map", "navi.apk", "tool.sh"]

    drive = tmp_path / "drive"
    drive.mkdir()
    stage = load_stages(model)[0]
    api._worker(model, stage, 0, None, [str(apk)], str(drive), False, "FAT32", stage["flash_blocks"][0])

    assert events[-1]["kind"] == "usb_finished" and events[-1]["success"] is True, events[-1]
    written = sorted(p.relative_to(drive).as_posix() for p in drive.rglob("*") if p.is_file())
    assert written == ["apps/navi.apk", "maps/a.map", "tool.sh", "update.bin"]
    assert "second.bin" not in written  # это другая флешка — другой блок

    drive2 = tmp_path / "drive2"
    drive2.mkdir()
    api._worker(model, stage, 0, None, [str(apk)], str(drive2), False, "FAT32", stage["flash_blocks"][2])
    assert sorted(p.name for p in drive2.rglob("*") if p.is_file()) == ["second.bin"]  # без приложений


def test_block_that_does_not_write_files_is_refused(tmp_path):
    base, model, scanner, _apk = _make(tmp_path)
    api = usb_api.UsbApi(base, scanner)

    for block in (1, 3, 4, 99):
        result = api.list_items(model.key, 0, None, [], block)
        assert result["ok"] is False and "не записывает файлы" in result["error"]
        assert api.start(model.key, 0, None, [], "/nowhere", False, "FAT32", block)["ok"] is False
