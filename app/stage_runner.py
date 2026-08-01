"""Динамическая загрузка stages.py модели (режим "Мастер установки по этапам")."""
import importlib.util
from pathlib import Path

STAGE_TYPES = ("usb", "adb", "manual", "apps", "exe", "check")


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
        if stage_type in ("usb", "adb") and not callable(stage.get("run")):
            raise StageDefinitionError(f"Этап {i} ({stage_type}): не задан run(ctx)")
        if stage_type == "exe" and not stage.get("exe_path"):
            raise StageDefinitionError(f"Этап {i} (exe): не задан exe_path")
        if stage_type == "check" and not stage.get("check_options"):
            raise StageDefinitionError(f"Этап {i} (check): не заданы check_options")

    return list(stages)


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
