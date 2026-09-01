"""Динамическая загрузка stages.py модели (режим "Мастер установки по этапам")."""
from __future__ import annotations
import importlib.util
from pathlib import Path

STAGE_TYPES = ("usb", "adb", "manual", "apps", "exe", "check", "instruction", "uart", "telnet", "actions", "qr_adb")


class StageDefinitionError(RuntimeError):
    pass


def load_stages(model) -> list[dict]:
    """Загружает stages.py модели и возвращает провалидированный список этапов."""
    module = _load_module(model.stages_script)
    if not hasattr(module, "STAGES"):
        raise StageDefinitionError("stages.py должен содержать список STAGES")

    stages = module.STAGES
    if not stages:
        raise StageDefinitionError("STAGES пуст")

    for i, stage in enumerate(stages, start=1):
        stage_type = stage.get("type")
        if stage_type not in STAGE_TYPES:
            raise StageDefinitionError(
                f"Этап {i}: неизвестный type={stage_type!r}, ожидается один из {STAGE_TYPES}"
            )
        if not stage.get("title"):
            raise StageDefinitionError(f"Этап {i}: не задан title")
        if stage_type in ("usb", "adb", "uart", "telnet") and not callable(stage.get("run")):
            raise StageDefinitionError(f"Этап {i} ({stage_type}): не задан run(ctx)")
        if stage_type == "exe" and not stage.get("exe_path"):
            raise StageDefinitionError(f"Этап {i} (exe): не задан exe_path")
        if stage_type == "check" and not stage.get("check_options"):
            raise StageDefinitionError(f"Этап {i} (check): не заданы check_options")
        if stage_type == "actions":
            actions = stage.get("actions")
            if not actions:
                raise StageDefinitionError(f"Этап {i} (actions): не задано ни одного действия")
            for j, action in enumerate(actions, start=1):
                if not callable(action.get("run")):
                    raise StageDefinitionError(f"Этап {i} (actions), действие {j}: не задан run(ctx)")

    return list(stages)


def load_wifi_port(model) -> int:
    """WIFI_PORT — модуль-константа в сгенерированном stages.py (см.
    car_generator.py: _render_stages_py, только для моделей с spec.wifi=True)
    — порт, который apps-этап с apps_connection="wifi"/"ask" использует для
    Wi-Fi ADB (см. app/web/frontend/js/screens/stage_wizard.js:
    buildTransportBar), тот же, что уже используют adb-этапы через
    _with_connect. 5555, если не задан (обычные проводные модели)."""
    module = _load_module(model.stages_script)
    return getattr(module, "WIFI_PORT", 5555)


def load_model_wifi(model) -> bool:
    """Есть ли у модели вообще проводной ADB — WIFI_PORT (см. load_wifi_port)
    пишется в stages.py ТОЛЬКО если spec.wifi=True (car_generator.py:
    _render_stages_py), поэтому его наличие как атрибута модуля — тот же
    самый признак, без отдельной генерируемой константы. Нужен, чтобы верно
    достраивать apps_connection по умолчанию (см. install_api.py:
    _stage_to_dict) для моделей, чей apps-этап был сохранён ДО появления
    этого поля — иначе wifi-only модель молча по умолчанию "wired"."""
    module = _load_module(model.stages_script)
    return hasattr(module, "WIFI_PORT")


def stage_instruction_html_path(model, stage: dict) -> Path | None:
    """Путь к html-инструкции этапа, если она указана и существует."""
    rel = stage.get("instruction")
    if not rel:
        return None
    path = model.dir / rel
    return path if path.exists() else None


def _load_module(script_path: Path):
    spec = importlib.util.spec_from_file_location(
        f"car_stages_script_{abs(hash(str(script_path)))}", script_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
