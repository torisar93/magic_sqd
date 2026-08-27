"""Сканирование папок cars/ — порт desktop-версии (app/scanner.py), тоже
чистый stdlib (json/dataclasses/datetime/pathlib), переносится почти без
изменений. Урезано до scan_cars/статусов — apk/-библиотека (scan_apks) не
нужна для списка машин, отдельный следующий шаг."""
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

MODEL_STATUSES = ("ok", "needs_review", "broken")
_RECENTLY_UPDATED_HOURS = 24
NO_INSTRUCTION_MARKER = "no_instruction.txt"
VERSION_FILENAME = "version.json"
LOGO_FILENAMES = ("logo.png", "logo.svg", "logo.jpg", "logo.jpeg")


@dataclass
class ModelInfo:
    brand: str
    name: str
    dir: Path
    stages_script: Path
    modification: str = None
    no_instruction: bool = False
    revision: int = 0
    changelog: str = ""
    status: str = "ok"
    updated_at: str = ""
    logo_path: Path = None

    @property
    def display_label(self) -> str:
        model_part = f"{self.name} — {self.modification}" if self.modification else self.name
        return f"{self.brand} / {model_part}"


@dataclass
class ModelGroup:
    name: str
    leaf: ModelInfo
    modifications: list
    logo_path: Path = None


_MODEL_PAYLOAD_DIR_NAMES = {"files", "usb_files"}


def scan_cars(cars_dir: Path):
    """brand -> [ModelGroup, ...]."""
    brands = {}
    if not cars_dir.exists():
        return brands

    for brand_dir in sorted(cars_dir.iterdir(), key=lambda p: p.name.lower()):
        if not brand_dir.is_dir() or brand_dir.name.startswith("_"):
            continue
        groups = []
        for model_dir in sorted(brand_dir.iterdir(), key=lambda p: p.name.lower()):
            if not model_dir.is_dir():
                continue
            sub_dirs = _model_sub_dirs(model_dir)
            if _has_own_model_files(model_dir) or not sub_dirs:
                leaf = _build_model_info(brand_dir.name, model_dir.name, None, model_dir)
                groups.append(ModelGroup(
                    name=model_dir.name, leaf=leaf, modifications=[], logo_path=_find_logo(model_dir),
                ))
            else:
                modifications = [
                    _build_model_info(brand_dir.name, model_dir.name, sub.name, sub)
                    for sub in sub_dirs
                ]
                groups.append(ModelGroup(
                    name=model_dir.name, leaf=None, modifications=modifications, logo_path=_find_logo(model_dir),
                ))
        if groups:
            brands[brand_dir.name] = groups
    return brands


def _has_own_model_files(model_dir: Path) -> bool:
    return any((model_dir / name).exists() for name in ("stages.py", "install.py"))


def _model_sub_dirs(model_dir: Path):
    return sorted(
        (p for p in model_dir.iterdir()
         if p.is_dir() and not p.name.startswith("_") and p.name not in _MODEL_PAYLOAD_DIR_NAMES),
        key=lambda p: p.name.lower())


def _build_model_info(brand: str, name: str, modification, leaf_dir: Path) -> ModelInfo:
    stages_script = leaf_dir / "stages.py"
    revision, changelog, status, updated_at = _read_version(leaf_dir)
    return ModelInfo(
        brand=brand,
        name=name,
        dir=leaf_dir,
        stages_script=stages_script if stages_script.exists() else None,
        modification=modification,
        no_instruction=(leaf_dir / NO_INSTRUCTION_MARKER).exists(),
        revision=revision,
        changelog=changelog,
        status=status,
        updated_at=updated_at,
        logo_path=_find_logo(leaf_dir),
    )


def _find_logo(directory: Path):
    for filename in LOGO_FILENAMES:
        path = directory / filename
        if path.is_file():
            return path
    return None


def _read_version(model_dir: Path):
    try:
        data = json.loads((model_dir / VERSION_FILENAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, "", "ok", ""
    try:
        revision = int(data.get("revision", 0))
    except (TypeError, ValueError):
        revision = 0
    status = str(data.get("status") or "ok")
    if status not in MODEL_STATUSES:
        status = "ok"
    return revision, str(data.get("changelog") or ""), status, str(data.get("updated_at") or "")


def model_status_color(model: ModelInfo) -> str:
    if model.status == "broken":
        return "red"
    if model.updated_at:
        try:
            updated = datetime.fromisoformat(model.updated_at)
        except ValueError:
            updated = None
        if updated is not None and datetime.now() - updated < timedelta(hours=_RECENTLY_UPDATED_HOURS):
            return "blue"
    if model.status == "needs_review":
        return "yellow"
    return "green"


def rollup_status_color(colors) -> str:
    if not colors:
        return "green"
    if all(c == "green" for c in colors):
        return "green"
    if any(c == "blue" for c in colors):
        return "blue"
    red_count = colors.count("red")
    yellow_count = colors.count("yellow")
    if red_count >= yellow_count and red_count > 0:
        return "red"
    if yellow_count > 0:
        return "yellow"
    return "green"
