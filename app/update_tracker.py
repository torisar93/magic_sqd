"""Локальное отслеживание того, какие ревизии моделей техник уже видел —
для сводки "Что нового" при старте программы (см. app/web/api/sync_api.py).
Ревизия/чейнджлог самой модели — см. app/scanner.py:ModelInfo.revision/
changelog, app/car_generator.py: version.json.

Состояние живёт в base_dir/seen_versions.json — РЯДОМ с cars/, а не внутри
неё, чтобы автообновление cars/ с сервера (content_sync.sync_scripts) не
могло его перезаписать (сервер ничего не знает про этот файл и не пришлёт
его, sync_tree трогает только то, что есть в удалённом листинге)."""
import json
from pathlib import Path

from .scanner import ModelInfo

STATE_FILENAME = "seen_versions.json"


def _state_path(base_dir: Path) -> Path:
    return base_dir / STATE_FILENAME


def _model_key(model: ModelInfo) -> str:
    return str(model.dir)


def load_seen(base_dir: Path) -> dict[str, int]:
    try:
        data = json.loads(_state_path(base_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    try:
        return {str(k): int(v) for k, v in data.items()}
    except (TypeError, ValueError):
        return {}


def save_seen(base_dir: Path, seen: dict[str, int]) -> None:
    try:
        _state_path(base_dir).write_text(
            json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # сводка "Что нового" просто не запомнится до следующего раза


def compute_changes(base_dir: Path, models: list[ModelInfo]) -> tuple[list[dict], dict[str, int]]:
    """Сравнивает текущие revision (см. app/scanner.py:flatten_models) с тем,
    что было запомнено в прошлый раз. Возвращает (изменения для сводки,
    новое состояние — вызывающий сам решает, когда его сохранить через
    save_seen). Первый запуск (нет seen_versions.json вовсе) не даёт ни
    одного изменения — только заводит базовую линию, иначе на первой же
    сборке технику вывалило бы "добавлено" сразу на все модели в cars/."""
    previous = load_seen(base_dir)
    is_first_run = not previous
    changes: list[dict] = []
    new_state: dict[str, int] = {}
    for model in models:
        key = _model_key(model)
        new_state[key] = model.revision
        if is_first_run:
            continue
        prev_revision = previous.get(key)
        if prev_revision is None:
            changes.append({"kind": "added", "label": model.display_label,
                             "changelog": model.changelog})
        elif model.revision > prev_revision:
            changes.append({"kind": "updated", "label": model.display_label,
                             "changelog": model.changelog})
    return changes, new_state
