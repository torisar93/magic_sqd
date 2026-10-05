"""Закрытые этапы модели (владелец, 2026-10-05): «у бесплатной версии инструкция будет заканчиваться на
установке приложений, а для платных подписчиков я допишу этапы для настройки уже на самом ГУ после
установки». Добавляет их только администратор в редакторе (галочка «Закрытый этап» у этапа); видят
подписчики Boosty и/или группы из админки (server/user_groups.py: closed_stage_access).

Как это лежит в папке модели (так же на сервере):
- открытая часть — как обычно (stages.py, install.py, _wizard_spec.json, files/, usb_files/), но вместо
  каждого закрытого этапа — заглушка: этап "manual" с тем же названием и id, "closed": True и текстом
  для старых версий программы (они закрытых этапов не знают и показывают заглушку как обычный этап);
- _closed/ — полные stages.py, install.py, _wizard_spec.json модели и файлы закрытых этапов (files/…,
  usb_files/… с теми же путями, что были бы у открытой модели). Сервер закрывает _closed/ всегда: в каталоге
  её нет, по прямой ссылке — 403 (кроме тех, кому открыто).

Программа подставляет закрытый этап вместо заглушки по id, если _closed/ скачана (merge_stages — ПК,
android/.../wizard_spec.py — Android). Нет доступа — заглушка остаётся, мастер рисует её под замком.
"""
from __future__ import annotations

import copy
import shutil
from pathlib import Path

CLOSED_DIR = "_closed"
SPEC_FILENAME = "_wizard_spec.json"
# Заглушка для версий программы, которые закрытых этапов не знают (показывают её как обычный этап)
PLACEHOLDER_TEXT_SUBSCRIBERS = ("Этот этап — для подписчиков Boosty. Обновите Magic SQD до последней версии, "
                                "чтобы увидеть его.")
PLACEHOLDER_TEXT_GROUPS = "Этот этап открыт не всем. Обновите Magic SQD до последней версии."
# Этапы, которые нельзя закрыть: у «Проверки» свой граф переходов (у заглушки — один «Далее»)
NOT_CLOSABLE_TYPES = ("check",)


class ClosedStagesError(ValueError):
    pass


def closed_dir(model_dir: Path) -> Path:
    return Path(model_dir) / CLOSED_DIR


# ----------------------------------------------------------------------
# Редактор: сохранение (car_generator._write_model_files → split_model)
# ----------------------------------------------------------------------
def _step_paths(spec, index: int, step) -> list[str]:
    """Папки файлов этапа index (с 1) от папки модели — те же имена, что пишет car_generator."""
    paths = [f"files/instruction_{index}", f"files/exe_{index}", f"files/adb_{index}", f"files/video_{index}",
             f"files/usb_pack_{index}", f"usb_files/step_{index}"]
    paths += [f"files/actions_{index}_{j}" for j in range(1, len(step.actions) + 1)]
    paths += [f"files/flash_{block.id}" for block in step.flash_blocks if block.id]
    if step.type == "apps":
        apps_index = sum(1 for s in spec.steps[:index] if s.type == "apps")
        paths.append("files/pack" if apps_index == 1 else f"files/pack_{apps_index}")
    return paths


def validate(spec) -> None:
    """Закрыть можно не всякий этап — понятная ошибка в редакторе вместо сломанной модели."""
    seen_open_apps_after = False
    for step in reversed(spec.steps):
        if step.type == "apps" and not step.closed:
            seen_open_apps_after = True
        if not step.closed:
            continue
        if step.type in NOT_CLOSABLE_TYPES:
            raise ClosedStagesError(f"Этап «{step.title or step.type}»: этап «Проверка» нельзя сделать закрытым — "
                                    "у него свои переходы по вариантам.")
        if step.type == "apps" and seen_open_apps_after:
            # Папки pack/pack_N нумеруются по порядку этапов «Приложения» — у заглушки её нет, и номера
            # открытых этапов после неё разъехались бы.
            raise ClosedStagesError(f"Этап «{step.title or 'Приложения'}»: закрытый этап «Приложения» должен идти "
                                    "после всех открытых этапов «Приложения».")


def placeholder_step(step, subscribers: bool = True):
    """Заглушка закрытого этапа в открытой части: "manual" с тем же id, названием и переходом."""
    from .car_generator import StepSpec  # car_generator импортирует этот модуль — здесь лениво
    return StepSpec(type="manual", title=step.title,
                    description=PLACEHOLDER_TEXT_SUBSCRIBERS if subscribers else PLACEHOLDER_TEXT_GROUPS,
                    id=step.id, next=step.next, pos_x=step.pos_x, pos_y=step.pos_y, closed=True)


def split_model(model_dir: Path, spec) -> None:
    """После того как car_generator записал модель целиком (все этапы открыто): закрытые этапы — в
    _closed/ (полные скрипты, спека и файлы этих этапов), в открытой части — заглушки. Нет закрытых
    этапов — _closed/ убирается. Этап-заглушка без содержимого (модель правили там, где закрытой части
    нет, — например заявка техника) остаётся заглушкой, а _closed/ — как была."""
    from . import car_generator as cg
    model_dir = Path(model_dir)
    target = closed_dir(model_dir)
    closed = [(i, step) for i, step in enumerate(spec.steps, start=1) if step.closed]
    with_content = [(i, step) for i, step in closed if not getattr(step, "closed_placeholder", False)]
    if not closed:
        shutil.rmtree(target, ignore_errors=True)
        return
    if not with_content:
        return  # закрытая часть не здесь — не трогаем ни её, ни заглушки
    validate(spec)
    staging = model_dir / (CLOSED_DIR + ".new")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    for name in ("stages.py", "install.py", SPEC_FILENAME):
        shutil.copy2(model_dir / name, staging / name)
    for i, step in with_content:
        for rel in _step_paths(spec, i, step):
            src = model_dir / rel
            if src.exists():
                dst = staging / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
    shutil.rmtree(target, ignore_errors=True)
    staging.rename(target)

    public = copy.copy(spec)
    subscribers = getattr(spec, "closed_subscribers", True)
    public.steps = [placeholder_step(step, subscribers) if step.closed else step for step in spec.steps]
    (model_dir / "install.py").write_text(cg._render_install_py(public), encoding="utf-8")
    (model_dir / "stages.py").write_text(cg._render_stages_py(public, model_dir), encoding="utf-8")
    (model_dir / SPEC_FILENAME).write_text(cg._render_spec_json(public), encoding="utf-8")


# ----------------------------------------------------------------------
# Редактор: открытие модели (car_generator.load_car_spec → merge_for_edit)
# ----------------------------------------------------------------------
def merge_for_edit(model_dir: Path, steps: list, load_spec) -> list:
    """Шаги открытой части с заглушками → полные шаги для редактора: содержимое закрытых — из _closed/
    (load_spec(папка) — load_car_spec той же модели), переходы и место на холсте — из открытой части
    (её могли переставить). Закрытой части нет (нет доступа, не скачана) — заглушка помечается
    closed_placeholder: её можно переставлять, но не править, и при сохранении она остаётся заглушкой."""
    if not any(step.closed for step in steps):
        return steps
    folder = closed_dir(model_dir)
    full = load_spec(folder) if (folder / SPEC_FILENAME).is_file() else None
    by_id = {step.id: step for step in (full.steps if full else []) if step.closed}
    merged = []
    for step in steps:
        source = by_id.get(step.id) if step.closed else None
        if source is None:
            if step.closed:
                step.closed_placeholder = True
            merged.append(step)
            continue
        source.next, source.next_options = step.next, step.next_options
        source.pos_x, source.pos_y = step.pos_x, step.pos_y
        merged.append(source)
    return merged


def stage_location(model_dir: Path, stage: dict, stage_index: int) -> tuple[Path, int]:
    """Где лежат файлы этапа и под каким номером (files/actions_<номер>_<j>, usb_files/step_<номер>): у
    открытого — папка модели и его позиция (stage_index с 0), у подставленного закрытого — _closed/ и его
    позиция в закрытой части (модель могли переставить после того, как этап закрыли)."""
    if stage.get("closed") and not stage.get("closed_locked") and stage.get("closed_index"):
        return closed_dir(model_dir), int(stage["closed_index"])
    return Path(model_dir), stage_index + 1


# ----------------------------------------------------------------------
# Установка: stage_runner.load_stages → merge_stages
# ----------------------------------------------------------------------
def merge_stages(model_dir: Path, stages: list[dict], load_module) -> list[dict]:
    """STAGES открытой части → с закрытыми этапами из _closed/stages.py (load_module — загрузчик stages.py),
    если она скачана (есть доступ). Переходы — из открытой части. Относительные пути (инструкции) — от папки
    модели, с префиксом _closed/. Нет закрытой части — заглушки остаются (мастер рисует их под замком)."""
    if not any(stage.get("closed") for stage in stages):
        return stages
    script = closed_dir(model_dir) / "stages.py"
    by_id = {}
    if script.is_file():
        try:
            module = load_module(script)
            by_id = {stage.get("id"): {**stage, "closed_index": number}
                     for number, stage in enumerate(getattr(module, "STAGES", []), start=1) if stage.get("closed")}
        except Exception:  # noqa: BLE001 - сломанная закрытая часть не должна ломать открытую
            by_id = {}
    result = []
    for stage in stages:
        source = by_id.get(stage.get("id")) if stage.get("closed") else None
        if source is None:
            if stage.get("closed"):
                stage = {**stage, "closed_locked": True,
                         "closed_subscribers": stage.get("description") != PLACEHOLDER_TEXT_GROUPS}
            result.append(stage)
            continue
        merged = {**source, "closed": True}
        for key in ("next", "next_options"):
            if key in stage:
                merged[key] = stage[key]
        if merged.get("instruction"):
            merged["instruction"] = f"{CLOSED_DIR}/{merged['instruction']}"
        if merged.get("flash_blocks"):
            merged["flash_blocks"] = [
                {**block, "instruction": f"{CLOSED_DIR}/{block['instruction']}"} if block.get("instruction") else block
                for block in merged["flash_blocks"]]
        result.append(merged)
    return result
