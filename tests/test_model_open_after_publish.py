"""После публикации модели из редактора программа держала прежнюю инструкцию до перезапуска (владелец, 2026-10-03:
«перезаходил в модель, нажимал обновить каталог — всё равно старая инструкция, помогло только перезайти в программу»).
По журналу сервера две причины: (1) по событию car_saved интерфейс сразу переоткрывает модель, пока публикация ещё
льётся, и открытие скачивало с сервера ещё старую инструкцию поверх только что сохранённой — маркер локальной правки
синхронизация папки инструкции не учитывала (и ставился уже после car_saved); (2) манифест, полученный при этом
открытии, жил в кэше 60 с, повторные входы видели его же, а «Обновить каталог» этот кэш не сбрасывал."""
from __future__ import annotations
import os
import shutil
import sys
import types
from pathlib import Path

import pytest

from app import car_generator as cg
from app import content_sync
from app.web.api.install_api import InstallApi

ROOT = Path(__file__).resolve().parents[1]
MODEL = "Haval/H3"


def _blocks(text: str) -> list[dict]:
    return [{"type": "h1", "text": "Haval H3 — доступ к ADB"}, {"type": "p", "text": text}]


@pytest.fixture
def published(app_base, content_server):
    """Модель, опубликованная на сервере и такая же локально (время файлов совпадает — копия свежая)."""
    cars = app_base / "cars"
    (cars / "_shared").mkdir(exist_ok=True)
    shutil.copy(ROOT / "cars/_shared/load_sibling.py", cars / "_shared/load_sibling.py")
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="H3", steps=[
        cg.StepSpec(type="instruction", title="Доступ к ADB", instruction_blocks=_blocks("Шаги как раньше."))]))
    server_dir = content_server.content / "cars" / MODEL
    for path in model_dir.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            target = server_dir / path.relative_to(model_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    content_server.write_manifest()
    model = types.SimpleNamespace(dir=model_dir, brand="Haval", name="H3", modification="", key="k",
                                  stages_script=model_dir / "stages.py")
    api = InstallApi("adb", app_base, types.SimpleNamespace(get_model=lambda key: model))
    return types.SimpleNamespace(dir=model_dir, api=api)


def _shown_instruction(api) -> str:
    result = api.load_stages("k")
    assert "stages" in result, result
    return result["stages"][0]["instruction_html"]


def _publish_on_server(content_server, text: str):
    html = content_server.content / "cars" / MODEL / "files/instruction_1/instruction.html"
    from app import instruction_html
    html.write_text(instruction_html.render_document(_blocks(text), lambda b: b["path"]), encoding="utf-8")
    os.utime(html, (html.stat().st_mtime + 60, html.stat().st_mtime + 60))
    content_server.write_manifest()


def test_reopening_right_after_publish_shows_the_new_instruction(published, content_server):
    assert "Шаги как раньше." in _shown_instruction(published.api)
    _publish_on_server(content_server, "На прошивке 3B11 установка так же работает")
    # Повторный вход через секунды (раньше — тот же манифест из кэша на 60 с и прежняя инструкция)
    assert "3B11" in _shown_instruction(published.api)


def test_opening_during_publish_keeps_the_just_saved_instruction(published, content_server):
    # Редактор сохранил правку локально (маркер), публикация ещё не дошла — на сервере прежняя версия
    from app import instruction_html
    local_html = published.dir / "files/instruction_1/instruction.html"
    local_html.write_text(instruction_html.render_document(_blocks("Новая правка"), lambda b: b["path"]),
                          encoding="utf-8")
    content_sync.mark_local_edit(published.dir)
    photo = content_server.add(f"cars/{MODEL}/files/instruction_1/images/photo.jpg", b"jpg")  # нет локально
    content_server.write_manifest()

    assert "Новая правка" in _shown_instruction(published.api)  # не затёрто версией с сервера
    assert (published.dir / "files/instruction_1/images/photo.jpg").read_bytes() == photo.read_bytes()  # докачано


def test_locally_edited_model_keeps_its_own_apk(app_base, content_server):
    rel = f"cars/{MODEL}/files/pack/optional/App.apk"
    content_server.add(rel, b"SERVER")
    content_server.add(f"cars/{MODEL}/_wizard_spec.json", b"{}")
    content_server.write_manifest()
    model_dir = app_base / "cars" / MODEL
    local = model_dir / "files/pack/optional/App.apk"
    local.parent.mkdir(parents=True)
    local.write_bytes(b"MINE, version 2")  # своя версия под тем же именем (размер другой)
    content_sync.mark_local_edit(model_dir)

    assert content_sync.ensure_apks_downloaded(app_base, app_base / "apk", [local]) == 0
    assert local.read_bytes() == b"MINE, version 2"


def test_editor_marks_local_edit_before_car_saved(tmp_path, monkeypatch):
    stubbed = "webview" not in sys.modules
    if stubbed:
        sys.modules["webview"] = types.ModuleType("webview")
    from app.web.api import car_editor_api
    if stubbed:
        del sys.modules["webview"]
    cars = tmp_path / "cars"
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="H3", steps=[
        cg.StepSpec(type="instruction", title="Доступ к ADB", instruction_blocks=_blocks("Шаги"))]))
    seen = {}
    monkeypatch.setattr(car_editor_api.event_bridge, "push", lambda event: seen.setdefault(
        event["kind"], (model_dir / content_sync.LOCAL_EDIT_MARKER_FILENAME).exists()))
    api = car_editor_api.CarEditorApi.__new__(car_editor_api.CarEditorApi)
    api.base_dir, api.cars_dir = tmp_path, cars
    spec = cg.load_car_spec(model_dir, "Haval", "H3", "")

    api._worker(spec, model_dir, is_pending=False, admin_mode=False)

    assert seen["car_saved"] is True  # интерфейс переоткроет модель уже с маркером — правку не затрёт
    assert "car_save_finished" in seen
