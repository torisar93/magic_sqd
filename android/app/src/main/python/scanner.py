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
SUBMISSION_STATUS_FILENAME = ".submission_status"
LOGO_FILENAMES = ("logo.png", "logo.svg", "logo.jpg", "logo.jpeg")
# Большая фотография модели для .model-hero (см. app/scanner.py на
# desktop-стороне — то же самое, синхронизируется вместе с остальными
# файлами модели, без пересборки APK).
HERO_FILENAMES = ("hero.webp", "hero.png", "hero.jpg", "hero.jpeg")
HERO_PLACEHOLDER_FILENAMES = ("hero-placeholder.webp", "hero-placeholder.png")
# Ранний доступ — отметку пишет content_sync.sync_early_access (то же, что desktop app/scanner.py).
EARLY_ACCESS_MARKER = "_early_access.json"


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
    hero_path: Path = None
    # Статус своей заявки на сервере ("pending"/"rejected"), см. auth_bridge.
    # sync_my_cars — маркер-файл SUBMISSION_STATUS_FILENAME в папке модели.
    submission_status: str = ""
    # Ранний доступ: когда откроется всем; early_locked — без подписки, только витрина.
    early_open_at: float = None
    early_locked: bool = False

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
    hero_path: Path = None
    early_open_at: float = None
    early_locked: bool = False


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
            model_early = read_early_access(model_dir)
            if _has_own_model_files(model_dir) or not sub_dirs:
                leaf = _build_model_info(brand_dir.name, model_dir.name, None, model_dir, model_early)
                groups.append(ModelGroup(
                    name=model_dir.name, leaf=leaf, modifications=[], logo_path=_find_logo(model_dir),
                    hero_path=_find_hero(model_dir) or _hero_placeholder(cars_dir),
                    early_open_at=leaf.early_open_at, early_locked=leaf.early_locked,
                ))
            else:
                modifications = [
                    _build_model_info(brand_dir.name, model_dir.name, sub.name, sub, model_early)
                    for sub in sub_dirs
                ]
                early = [m for m in modifications if m.early_open_at is not None]
                groups.append(ModelGroup(
                    name=model_dir.name, leaf=None, modifications=modifications, logo_path=_find_logo(model_dir),
                    hero_path=_find_hero(model_dir) or _hero_placeholder(cars_dir),
                    early_open_at=min((m.early_open_at for m in early), default=None),
                    early_locked=bool(early) and all(m.early_locked for m in modifications),
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


def read_early_access(directory: Path):
    """(когда откроется всем, под замком ли) из EARLY_ACCESS_MARKER или None."""
    try:
        data = json.loads((directory / EARLY_ACCESS_MARKER).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    open_at = data.get("open_at") if isinstance(data, dict) else None
    if isinstance(open_at, bool) or not isinstance(open_at, (int, float)):
        return None
    return float(open_at), bool(data.get("locked"))


def _build_model_info(brand: str, name: str, modification, leaf_dir: Path, inherited_early=None) -> ModelInfo:
    stages_script = leaf_dir / "stages.py"
    revision, changelog, status, updated_at = _read_version(leaf_dir)
    early = read_early_access(leaf_dir) or inherited_early
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
        hero_path=_find_hero(leaf_dir),
        submission_status=_read_submission_status(leaf_dir),
        early_open_at=early[0] if early else None,
        early_locked=bool(early and early[1]),
    )


def _read_submission_status(model_dir: Path) -> str:
    try:
        value = (model_dir / SUBMISSION_STATUS_FILENAME).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return value if value in ("pending", "rejected") else ""


def _find_logo(directory: Path):
    for filename in LOGO_FILENAMES:
        path = directory / filename
        if path.is_file():
            return path
    return None


def _find_hero(directory: Path):
    for filename in HERO_FILENAMES:
        path = directory / filename
        if path.is_file():
            return path
    return None


def _hero_placeholder(cars_dir: Path):
    shared_dir = cars_dir / "_shared"
    for filename in HERO_PLACEHOLDER_FILENAMES:
        path = shared_dir / filename
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
