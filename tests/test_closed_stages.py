"""Закрытые этапы (владелец, 2026-10-05): «у бесплатной версии инструкция будет заканчиваться на установке
приложений, а для платных подписчиков я допишу этапы для настройки уже на самом ГУ». Редактор кладёт
закрытые этапы в <модель>/_closed/ (сервер её закрывает), в открытой части — заглушки; программа
подставляет закрытый этап, если _closed/ скачана, иначе показывает заглушку под замком (app/closed_stages.py)."""
from __future__ import annotations
import json
import shutil
import types
from pathlib import Path

import pytest

from app import car_generator as cg, closed_stages, content_sync
from app.admin_client import compute_stale_files
from app.stage_runner import load_stages, stage_instruction_html_path

REPO = Path(__file__).resolve().parents[1]


def _cars(tmp_path) -> Path:
    cars = tmp_path / "cars"
    (cars / "_shared").mkdir(parents=True)
    shutil.copy(REPO / "cars/_shared/load_sibling.py", cars / "_shared/load_sibling.py")
    return cars


def _model(model_dir: Path):
    return types.SimpleNamespace(dir=model_dir, stages_script=model_dir / "stages.py")


def _photo(tmp_path) -> Path:
    path = tmp_path / "src" / "eq.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"png-eq")
    return path


def _steps(tmp_path, closed=True) -> list[cg.StepSpec]:
    steps = [
        cg.StepSpec(type="instruction", title="Доступ к ADB", instruction_blocks=[{"type": "text", "text": "Откройте меню"}]),
        cg.StepSpec(type="manual", title="Установка приложений"),
        cg.StepSpec(type="instruction", title="Настройка звука", closed=closed, instruction_blocks=[
            {"type": "steps", "text": "Откройте «Звук»\nВыставьте эквалайзер"},
            {"type": "photo", "path": str(_photo(tmp_path)), "caption": ""}]),
        cg.StepSpec(type="actions", title="Кнопки руля", closed=closed, actions=[
            cg.ActionSpec(label="Обучить кнопки", kind="command", commands=["am start -n x/.Learn"])]),
    ]
    for i, step in enumerate(steps):  # граф, как его строит редактор: этапы по порядку
        step.id, step.next = i, (i + 1 if i + 1 < len(steps) else None)
    return steps


def _create(tmp_path, closed=True):
    cars = _cars(tmp_path)
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="H3", steps=_steps(tmp_path, closed)))
    return cars, model_dir


def _spec(model_dir) -> dict:
    return json.loads((model_dir / "_wizard_spec.json").read_text(encoding="utf-8"))


def test_save_puts_closed_stages_into_closed_folder_and_placeholders_in_open_part(tmp_path):
    _, model_dir = _create(tmp_path)
    steps = _spec(model_dir)["steps"]
    assert [s["type"] for s in steps] == ["instruction", "manual", "manual", "manual"]
    placeholder = steps[2]
    assert placeholder["closed"] is True and placeholder["title"] == "Настройка звука"
    assert placeholder["description"] == closed_stages.PLACEHOLDER_TEXT_SUBSCRIBERS
    assert "Выставьте эквалайзер" not in (model_dir / "stages.py").read_text(encoding="utf-8")
    assert "am start" not in (model_dir / "install.py").read_text(encoding="utf-8")
    assert not (model_dir / "files" / "instruction_3").exists()          # файлы закрытого этапа — не в открытой части
    closed = model_dir / "_closed"
    assert (closed / "files" / "instruction_3" / "instruction.html").is_file()
    assert list((closed / "files" / "instruction_3" / "images").iterdir())
    assert "am start" in (closed / "install.py").read_text(encoding="utf-8")
    assert [s.get("closed", False) for s in _spec(closed)["steps"]] == [False, False, True, True]
    assert (model_dir / "files" / "instruction_1" / "instruction.html").is_file()  # открытые — на месте


def test_without_access_the_wizard_gets_locked_placeholders(tmp_path):
    _, model_dir = _create(tmp_path)
    shutil.rmtree(model_dir / "_closed")  # у этого пользователя закрытой части нет — сервер её не отдал
    stages = load_stages(_model(model_dir))
    assert [s["type"] for s in stages] == ["instruction", "manual", "manual", "manual"]
    assert stages[2]["closed_locked"] is True and stages[2]["closed_subscribers"] is True
    assert stages[2]["title"] == "Настройка звука" and stages[2]["next"] == stages[3]["id"]


def test_with_access_the_wizard_gets_the_real_closed_stages(tmp_path):
    _, model_dir = _create(tmp_path)
    stages = load_stages(_model(model_dir))
    assert [s["type"] for s in stages] == ["instruction", "manual", "instruction", "actions"]
    sound, wheel = stages[2], stages[3]
    assert sound["closed"] is True and not sound.get("closed_locked")
    assert sound["instruction"] == "_closed/files/instruction_3/instruction.html"
    assert "эквалайзер" in stage_instruction_html_path(_model(model_dir), sound).read_text(encoding="utf-8")
    assert sound["next"] == wheel["id"] and callable(wheel["actions"][0]["run"])
    root, number = closed_stages.stage_location(model_dir, wheel, 3)
    assert root == model_dir / "_closed" and number == 4


def test_editor_reopens_closed_stages_with_content_and_resave_keeps_layout(tmp_path):
    cars, model_dir = _create(tmp_path)
    spec = cg.load_car_spec(model_dir, "Haval", "H3")
    sound = spec.steps[2]
    assert sound.closed and sound.type == "instruction"
    assert any("эквалайзер" in (b.get("text") or "") for b in sound.instruction_blocks)
    assert spec.steps[3].actions[0].commands == ["am start -n x/.Learn"]
    cg.update_car(cars, model_dir, spec)
    assert (model_dir / "_closed" / "files" / "instruction_3" / "instruction.html").is_file()
    assert list((model_dir / "_closed" / "files" / "instruction_3" / "images").iterdir())  # фото не потерялось
    assert _spec(model_dir)["steps"][2]["type"] == "manual"


def test_opening_closed_stage_to_everyone_moves_it_back(tmp_path):
    cars, model_dir = _create(tmp_path)
    spec = cg.load_car_spec(model_dir, "Haval", "H3")
    for step in spec.steps:
        step.closed = False
    cg.update_car(cars, model_dir, spec)
    assert not (model_dir / "_closed").exists()
    assert (model_dir / "files" / "instruction_3" / "instruction.html").is_file()
    assert [s["type"] for s in _spec(model_dir)["steps"]] == ["instruction", "manual", "instruction", "actions"]


def test_editing_without_closed_part_keeps_placeholders_and_touches_nothing_closed(tmp_path):
    cars, model_dir = _create(tmp_path)
    shutil.rmtree(model_dir / "_closed")  # техник без доступа правит модель (заявка)
    spec = cg.load_car_spec(model_dir, "Haval", "H3")
    assert spec.steps[2].closed and getattr(spec.steps[2], "closed_placeholder", False)
    spec.steps[0].title = "Доступ к ADB (новое)"
    cg.update_car(cars, model_dir, spec)
    steps = _spec(model_dir)["steps"]
    assert steps[2]["closed"] is True and steps[2]["type"] == "manual" and not (model_dir / "_closed").exists()


def test_groups_only_placeholder_text(tmp_path):
    cars = _cars(tmp_path)
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="H3", steps=_steps(tmp_path),
                                                  closed_subscribers=False))
    assert _spec(model_dir)["steps"][2]["description"] == closed_stages.PLACEHOLDER_TEXT_GROUPS
    shutil.rmtree(model_dir / "_closed")
    assert load_stages(_model(model_dir))[2]["closed_subscribers"] is False


@pytest.mark.parametrize("steps, message", [
    ([cg.StepSpec(type="check", title="Версия", closed=True, check_options=["A", "B"])], "Проверка"),
    ([cg.StepSpec(type="apps", title="Свои", closed=True), cg.StepSpec(type="apps", title="Общие")], "после всех"),
])
def test_some_stages_cannot_be_closed(tmp_path, steps, message):
    with pytest.raises(closed_stages.ClosedStagesError, match=message):
        cg.create_car(_cars(tmp_path), cg.NewCarSpec(brand="Haval", model="H3", steps=steps))


def test_publish_cleanup_keeps_server_closed_part_when_it_is_not_here():
    server = ["stages.py", "_closed/stages.py", "_closed/files/instruction_3/instruction.html", "Рест/_closed/x"]
    assert compute_stale_files(["stages.py"], server) == []
    assert compute_stale_files(["stages.py", "_closed/stages.py"], server) == ["_closed/files/instruction_3/instruction.html"]


def test_lost_access_removes_downloaded_closed_part(tmp_path):
    base = tmp_path
    cars, model_dir = _create(tmp_path)
    rel = "cars/Haval/H3"
    with_access = {f"{rel}/stages.py": {"size": 1, "mtime": 0}, f"{rel}/_closed/stages.py": {"size": 1, "mtime": 0}}
    without = {f"{rel}/stages.py": {"size": 1, "mtime": 0}}
    assert content_sync.prune_closed_stages(base, cars, None) == []         # без сети — не трогаем
    assert content_sync.prune_closed_stages(base, cars, with_access) == []
    content_sync.mark_local_edit(model_dir)                                   # своя неотправленная правка
    assert content_sync.prune_closed_stages(base, cars, {**without, f"{rel}/_wizard_spec.json": {"size": 1, "mtime": 0}}) == []
    content_sync.clear_local_edit_marker(model_dir)
    assert content_sync.prune_closed_stages(base, cars, without) == ["Haval/H3"]
    assert not (model_dir / "_closed").exists()


# ----------------------------------------------------------------------
# Android: та же модель, свой загрузчик (android/.../python/wizard_spec.py)
# ----------------------------------------------------------------------
ANDROID_PY = REPO / "android/app/src/main/python"


def _android(monkeypatch, name):
    import importlib.util
    import sys
    monkeypatch.syspath_prepend(str(ANDROID_PY))  # catalog_crypto/catalog_key — плоские импорты как в Chaquopy
    spec = importlib.util.spec_from_file_location(name, ANDROID_PY / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_android_gets_real_closed_stages_with_access_and_locked_without(tmp_path, monkeypatch):
    wizard_spec = _android(monkeypatch, "wizard_spec")
    assert wizard_spec.CLOSED_GROUPS_TEXT == closed_stages.PLACEHOLDER_TEXT_GROUPS
    _, model_dir = _create(tmp_path)
    steps = wizard_spec.load_wizard_spec(model_dir, tmp_path)["steps"]
    assert [s["type"] for s in steps] == ["instruction", "manual", "instruction", "actions"]
    assert steps[2]["closed"] and "эквалайзер" in steps[2]["instruction_html"]
    assert steps[2]["next"] == steps[3]["id"] and steps[2]["index"] == 2
    assert steps[3]["actions"][0]["commands"] == [{"kind": "shell", "command": "am start -n x/.Learn"}]

    shutil.rmtree(model_dir / "_closed")
    steps = wizard_spec.load_wizard_spec(model_dir, tmp_path)["steps"]
    assert steps[2]["type"] == "manual" and steps[2]["closed_locked"] is True and steps[2]["closed_subscribers"] is True
    assert "эквалайзер" not in json.dumps(steps, ensure_ascii=False)


def test_android_lost_access_removes_closed_part(tmp_path, monkeypatch):
    _android(monkeypatch, "offline_pack")
    content_sync_android = _android(monkeypatch, "content_sync")
    cars, model_dir = _create(tmp_path)
    rel = "cars/Haval/H3"
    assert content_sync_android.prune_closed_stages(cars, {f"{rel}/_closed/stages.py": {}}) == []
    assert content_sync_android.prune_closed_stages(cars, {f"{rel}/stages.py": {}}) == ["Haval/H3"]
    assert not (model_dir / "_closed").exists()


@pytest.mark.parametrize("index", [REPO / "app/web/frontend/index.html", REPO / "android/app/src/main/assets/index.html"])
def test_lock_page_is_included_on_both_platforms(index):
    html = index.read_text(encoding="utf-8")
    assert 'href="css/closed_stage.css"' in html and 'src="js/closed_stage.js"' in html


def test_editor_round_trip_keeps_closed_flags_and_audience_choice():
    from app.web.api.car_editor_api import _clean_access, _step_from_dict, _step_to_dict
    step = cg.StepSpec(type="manual", title="Кнопки руля", closed=True)
    step.closed_placeholder = True
    data = _step_to_dict(step)
    assert data["closed"] is True and data["closed_placeholder"] is True
    back = _step_from_dict(data)
    assert back.closed and getattr(back, "closed_placeholder", False)
    assert not getattr(_step_from_dict({**data, "closed_placeholder": False}), "closed_placeholder", False)
    # поменяли только «Закрытые этапы видят» — доступ к модели не трогаем
    assert _clean_access({"closed": {"subscribers": False, "groups": [3]}}) == {"closed": {"subscribers": False, "groups": [3]}}
    assert _clean_access({"restricted": False, "groups": [], "closed": {"subscribers": True, "groups": []}}) == {
        "restricted": False, "groups": [], "closed": {"subscribers": True, "groups": []}}
    assert _clean_access({"closed": {"subscribers": "да"}}) is None
