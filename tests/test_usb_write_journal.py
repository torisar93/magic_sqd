"""ПК: запись на флешку расписана в журнале сессии (раньше там была одна строка «записано / остановлена», и по логу
было не понять, что происходило — ПК 1.0.46, 28.09, техники останавливали запись) и докачка перед записью видна в
кольце окна записи (фаза download). usb_api._worker: строки события install_log уходят в журнал сессии
(stage_wizard.js), ход докачки — событие apk_progress. Модель собирается настоящим генератором, флешка — папка."""
from __future__ import annotations
import shutil
import types
from pathlib import Path

import pytest

from app import car_generator as cg
from app.install_context import InstallCancelled
from app.stage_runner import load_stages
from app.web.api import usb_api

MB = 1048576
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path, monkeypatch):
    base = tmp_path / "app"
    cars = base / "cars"
    (cars / "_shared" / "freetuga").mkdir(parents=True)
    (cars / "_shared" / "freetuga" / "tool.sh").write_text("tool", encoding="utf-8")
    shutil.copy(REPO / "cars/_shared/load_sibling.py", cars / "_shared/load_sibling.py")  # его грузит stages.py модели
    src = tmp_path / "src"
    src.mkdir()
    (src / "update.bin").write_bytes(b"firmware")
    steps = [cg.StepSpec(type="usb", title="Флешка", flash_blocks=[
        cg.FlashBlockSpec(kind="write", files=[src / "update.bin"], copy_selected_apks=True, apks_dest="apps",
                          shared_folder="freetuga")])]
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Belgee", model="S50", steps=steps))
    model = types.SimpleNamespace(dir=model_dir, stages_script=model_dir / "stages.py", key="Belgee/S50",
                                  brand="Belgee", name="S50", modification="")
    apk = base / "apk" / "Data_Belgee_2.3.apk"
    apk.parent.mkdir()
    apk.write_bytes(b"apk")
    events = []
    monkeypatch.setattr(usb_api.event_bridge, "push", events.append)
    # Докачка перед записью: файлы модели — байтами, выбранные APK — ничего, общая папка — только числом файлов.
    monkeypatch.setattr(usb_api, "sync_model_files", lambda *a, on_progress, **k: [
        on_progress(done, 5 * MB, files, 2) for done, files in ((0, 0), (3 * MB, 1), (5 * MB, 2))])
    monkeypatch.setattr(usb_api, "ensure_apks_downloaded", lambda *a, **k: 0)
    monkeypatch.setattr(usb_api, "sync_shared_folder", lambda *a, on_progress, **k: on_progress(1, 1, 1, 1))
    drive = tmp_path / "drive"
    drive.mkdir()
    api = usb_api.UsbApi(base, types.SimpleNamespace(get_model=lambda key: model))
    stage = load_stages(model)[0]
    return types.SimpleNamespace(api=api, model=model, stage=stage, apk=apk, drive=drive, events=events)


def journal(env):
    return [e["text"] for e in env.events if e["kind"] == "install_log"]


def run(env):
    env.api._worker(env.model, env.stage, 0, None, [str(env.apk)], str(env.drive), False, "FAT32",
                    env.stage["flash_blocks"][0])


def test_write_is_described_in_the_session_journal(env):
    run(env)
    lines = journal(env)
    assert lines[0].startswith("Скачано перед записью: 5,0 МБ за ")
    assert lines[1] == f"Пишу на флешку {env.drive}: 3 файла, 0,0 МБ."
    assert lines[2].startswith("Запись на флешку закончена: 3 файла, 0,0 МБ за ")
    assert env.events[-1]["kind"] == "usb_finished" and env.events[-1]["success"] is True


def test_downloads_before_writing_move_the_ring(env):
    run(env)
    ring = [e for e in env.events if e["kind"] == "apk_progress" and e.get("phase") == "download"]
    assert [(e["bytes_done"], e["bytes_total"]) for e in ring if "bytes_done" in e] == [(0, 5 * MB), (3 * MB, 5 * MB),
                                                                                        (5 * MB, 5 * MB)]
    counted = [e for e in ring if "percent" in e]
    assert counted and counted[-1]["percent"] == 100 and "bytes_total" not in counted[-1]  # не выдаём число файлов за МБ
    assert all(e["stage_index"] == 0 and e["path"] == "" for e in ring)


def test_stop_says_how_far_the_write_got(env, monkeypatch):
    def write_then_stop(ctx, block):
        ctx._on_progress("update.bin", 8, 8, 1, 3, "done")
        env.api._thread = types.SimpleNamespace(is_alive=lambda: True)  # как во время настоящей записи
        env.api.cancel()
        raise InstallCancelled("Копирование остановлено пользователем.")

    monkeypatch.setattr(usb_api, "write_flash_files", write_then_stop)
    run(env)
    lines = journal(env)
    assert "Техник нажал «Остановить»: записано 1 из 3 файлов." in lines
    assert lines[-1].startswith("Запись на флешку остановлена техником: записано 1 из 3 файлов за ")
    assert env.events[-1]["kind"] == "usb_finished" and env.events[-1]["success"] is False


def test_failure_says_where_it_stopped(env, monkeypatch):
    def write_then_fail(ctx, block):
        ctx._on_progress("update.bin", 8, 8, 2, 3, "done")
        raise OSError("[Errno 28] No space left on device")

    monkeypatch.setattr(usb_api, "write_flash_files", write_then_fail)
    run(env)
    assert journal(env)[-1].startswith(
        "Запись на флешку прервалась ([Errno 28] No space left on device): записано 2 из 3 файлов за ")


def test_stop_during_downloads_is_named_as_such(env, monkeypatch):
    def downloading(*a, on_progress, **k):
        env.api._thread = types.SimpleNamespace(is_alive=lambda: True)
        env.api.cancel()
        raise InstallCancelled("Копирование остановлено пользователем.")

    monkeypatch.setattr(usb_api, "sync_model_files", downloading)
    run(env)
    assert journal(env) == ["Техник нажал «Остановить» во время подготовки файлов (скачивание)."]


@pytest.mark.parametrize("n, text", [(1, "1 файл"), (3, "3 файла"), (5, "5 файлов"), (11, "11 файлов"),
                                     (21, "21 файл"), (104, "104 файла"), (112, "112 файлов")])
def test_file_counts_are_proper_russian(n, text):
    assert usb_api._files(n) == text


@pytest.mark.parametrize("n, text", [(1, "1 файла"), (3, "3 файлов"), (11, "11 файлов"), (21, "21 файла")])
def test_counts_after_iz_are_genitive(n, text):
    assert usb_api._of_files(n) == text  # «записано 2 из 3 файлов»
