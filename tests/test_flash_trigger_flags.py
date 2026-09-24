"""На флешке — только файл-триггер текущего шага (владелец, 2026-09-24, Haval Jolion 2026): svengmode.flag
открывает инженерное меню, svlog.flag пишет лог, пока на экране QR-код. Если на флешке оба, магнитола во
время записи лога возвращается на главный экран инженерного меню, QR-код сбрасывается, и пароль из лога не
подходит. Поэтому запись одного триггера убирает с флешки другой (app/usb_context.py: TRIGGER_FLAGS; на
Android — UsbFlashWrite.kt: removeOtherTriggerFlags)."""
from __future__ import annotations
import pathlib
import shutil
import types
from pathlib import Path

import pytest

from app import car_generator as cg
from app.stage_runner import load_stages
from app.usb_context import remove_other_trigger_flags
from app.web.api import usb_api
from app.web.api.qr_adb_api import QrAdbApi

REPO = Path(__file__).resolve().parents[1]


def _files(drive: Path) -> list[str]:
    return sorted(p.name for p in drive.iterdir() if p.is_file())


@pytest.mark.parametrize("written, before, after, removed", [
    (["svlog.flag"], ["svengmode.flag", "update.bin"], ["update.bin"], ["svengmode.flag"]),
    (["svengmode.flag"], ["svlog.flag"], [], ["svlog.flag"]),
    (["svengmode.flag", "svlog.flag"], ["svlog.flag", "svengmode.flag"], ["svengmode.flag", "svlog.flag"], []),
    (["update.bin"], ["svengmode.flag", "svlog.flag"], ["svengmode.flag", "svlog.flag"], []),  # без триггера — не трогаем
    (["SVLOG.FLAG"], ["svengmode.flag"], [], ["svengmode.flag"]),
    (["svlog.flag"], [], [], []),
])
def test_only_the_current_step_trigger_stays(tmp_path, written, before, after, removed):
    for name in before:
        (tmp_path / name).write_bytes(b"x")
    logs = []

    assert remove_other_trigger_flags(tmp_path, written, logs.append) == removed
    assert _files(tmp_path) == after
    assert len(logs) == len(removed) and all("от прошлого шага" in line for line in logs)


def test_failure_to_remove_stops_the_write_with_a_clear_message(tmp_path, monkeypatch):
    (tmp_path / "svengmode.flag").write_bytes(b"x")

    def refuse(self, missing_ok=False):
        raise PermissionError("только для чтения")
    monkeypatch.setattr(pathlib.Path, "unlink", refuse)

    with pytest.raises(OSError) as info:
        remove_other_trigger_flags(tmp_path, ["svlog.flag"])
    assert "svengmode.flag" in str(info.value) and "вручную" in str(info.value)


def _jolion_like(tmp_path):
    """Этап как у боевой Haval Jolion 2026: запись svengmode.flag → инструкция → запись svlog.flag →
    инструкция → пароль. Модель собирается настоящим генератором (как tests/test_flash_blocks_desktop.py)."""
    base = tmp_path / "app"
    cars = base / "cars"
    (cars / "_shared").mkdir(parents=True)
    shutil.copy(REPO / "cars/_shared/load_sibling.py", cars / "_shared/load_sibling.py")
    src = tmp_path / "src"
    src.mkdir()
    (src / "svengmode.flag").write_bytes(b"eng")
    (src / "svlog.flag").write_bytes(b"log")
    steps = [cg.StepSpec(type="usb", title="Пароль ADB по QR-коду", flash_blocks=[
        cg.FlashBlockSpec(kind="write", files=[src / "svengmode.flag"]),
        cg.FlashBlockSpec(kind="instruction", title="Включите ADB в инженерном меню"),
        cg.FlashBlockSpec(kind="write", title="Запишите второй файл на флешку", files=[src / "svlog.flag"]),
        cg.FlashBlockSpec(kind="instruction", title="Вставьте флешку и дождитесь надписи"),
        cg.FlashBlockSpec(kind="password"),
    ])]
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="Jolion", steps=steps))
    model = types.SimpleNamespace(dir=model_dir, stages_script=model_dir / "stages.py", key="Haval/Jolion",
                                  brand="Haval", name="Jolion", modification="")
    scanner = types.SimpleNamespace(get_model=lambda key: model if key == model.key else None)
    return base, model, scanner


def test_second_block_takes_the_first_flag_off_the_same_drive(tmp_path, monkeypatch):
    base, model, scanner = _jolion_like(tmp_path)
    api = usb_api.UsbApi(base, scanner)
    events = []
    monkeypatch.setattr(usb_api.event_bridge, "push", events.append)
    stage = load_stages(model)[0]
    drive = tmp_path / "drive"
    drive.mkdir()
    (drive / "photo.jpg").write_bytes(b"jpeg")  # чужие файлы техника на флешке не трогаем

    def write_block(k):
        events.clear()
        api._worker(model, stage, 0, None, [], str(drive), False, "FAT32", stage["flash_blocks"][k])
        assert events[-1]["kind"] == "usb_finished" and events[-1]["success"] is True, events[-1]
        return [e["text"] for e in events if e["kind"] == "usb_log"]

    write_block(0)
    assert _files(drive) == ["photo.jpg", "svengmode.flag"]

    logs = write_block(2)
    assert _files(drive) == ["photo.jpg", "svlog.flag"]
    assert any("Убран с флешки svengmode.flag" in line for line in logs), logs

    write_block(0)  # техник начал заново — старый svlog.flag не должен сработать на первом шаге
    assert _files(drive) == ["photo.jpg", "svengmode.flag"]


def test_legacy_qr_stage_writes_leave_only_one_flag(tmp_path):
    cars = tmp_path / "cars"
    (cars / "_shared").mkdir(parents=True)
    (cars / "_shared" / "svengmode.flag").write_bytes(b"eng")
    (cars / "_shared" / "svlog.flag").write_bytes(b"log")
    drive = tmp_path / "drive"
    drive.mkdir()
    api = QrAdbApi(tmp_path, cars)

    assert api.write_prep_flag(str(drive)) == {"ok": True, "removed": []}
    assert api.write_flag(str(drive)) == {"ok": True, "removed": ["svengmode.flag"]}
    assert _files(drive) == ["svlog.flag"]
    assert api.write_prep_flag(str(drive)) == {"ok": True, "removed": ["svlog.flag"]}
    assert _files(drive) == ["svengmode.flag"]
