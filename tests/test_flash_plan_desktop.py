"""ПК: блок этапа «Флешка» пишется по плану общим кодом каталога (flash_plan.py, 12.10.2026) — и раскладка на флешке
та же, что у прежней записи (usb_context.write_flash_files): файлы и папки комплекта под своими именами, выбранные
приложения — в свою папку, общий набор — в корень со структурой; триггер прошлого шага убирается. Модель собирается
настоящим генератором, флешка — папка, сеть — подмена (всё уже на диске)."""
from __future__ import annotations

import shutil
import types
from pathlib import Path

import pytest

from app import car_generator as cg
from app.stage_runner import load_stages
from app.web.api import usb_api

REPO = Path(__file__).resolve().parents[1]
TRIGGER = "b832bc61472727635baffcf25dd28e9f239273e2"


@pytest.fixture
def env(tmp_path, monkeypatch):
    base = tmp_path / "app"
    cars = base / "cars"
    shared = cars / "_shared" / "freetuga"
    (shared / "magic_sqd" / "plugin").mkdir(parents=True)
    (shared / "magic_sqd.sh").write_text("sh", encoding="utf-8")
    (shared / "magic_sqd" / "plugin" / "magic_sqd.apk").write_bytes(b"plugin")
    shutil.copy(REPO / "cars/_shared/load_sibling.py", cars / "_shared/load_sibling.py")
    src = tmp_path / "src"
    (src / TRIGGER).mkdir(parents=True)
    (src / TRIGGER / "d4ad0e79.sh").write_text("#!/system/bin/sh\n", encoding="utf-8")
    (src / "svlog.flag").write_bytes(b"1")
    steps = [cg.StepSpec(type="usb", title="Флешка", flash_blocks=[
        cg.FlashBlockSpec(kind="write", files=[src / "svlog.flag", src / TRIGGER], copy_selected_apks=True,
                          apks_dest="apps", shared_folder="freetuga")])]
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Belgee", model="X70", steps=steps))
    model = types.SimpleNamespace(dir=model_dir, stages_script=model_dir / "stages.py", key="Belgee/X70",
                                  brand="Belgee", name="X70", modification="")
    apk = base / "apk" / "Навигация" / "yandex.apk"
    apk.parent.mkdir(parents=True)
    apk.write_bytes(b"apk")
    events = []
    monkeypatch.setattr(usb_api.event_bridge, "push", events.append)
    monkeypatch.setattr(usb_api, "sync_model_files", lambda *a, **k: [])
    monkeypatch.setattr(usb_api, "ensure_apks_downloaded", lambda *a, **k: 0)
    monkeypatch.setattr(usb_api, "sync_shared_folder", lambda *a, **k: None)
    monkeypatch.setattr(usb_api, "get_base_url", lambda base_dir: None)  # без сети: только то, что на диске
    api = usb_api.UsbApi(base, types.SimpleNamespace(get_model=lambda key: model))
    stage = load_stages(model)[0]
    return types.SimpleNamespace(api=api, model=model, stage=stage, apk=apk, events=events, tmp=tmp_path)


def write(env, drive: Path):
    drive.mkdir()
    (drive / "svengmode.flag").write_bytes(b"old")  # триггер прошлого шага
    env.api._worker(env.model, env.stage, 0, None, [str(env.apk)], str(drive), False, "FAT32",
                    env.stage["flash_blocks"][0])
    return sorted(p.relative_to(drive).as_posix() for p in drive.rglob("*") if p.is_file())


def test_plan_writes_the_same_tree_as_before(env, monkeypatch):
    by_plan = write(env, env.tmp / "plan_drive")
    assert env.events[-1]["kind"] == "usb_finished" and env.events[-1]["success"] is True
    monkeypatch.setattr(usb_api.flash_writer, "make_plan", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    legacy = write(env, env.tmp / "legacy_drive")
    assert by_plan == legacy == sorted([
        "svlog.flag", f"{TRIGGER}/d4ad0e79.sh", "apps/yandex.apk", "magic_sqd.sh", "magic_sqd/plugin/magic_sqd.apk"])


def test_window_queue_comes_from_the_plan(env):
    result = env.api.list_items(env.model.key, 0, None, [str(env.apk)], block=0)
    names = [item["name"] for item in result["items"]]
    assert result["ok"] and names == ["svlog.flag", "d4ad0e79.sh", "yandex.apk", "magic_sqd.sh", "magic_sqd.apk"]
