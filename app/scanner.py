"""Сканирование папок cars/ и apk/."""
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelInfo:
    brand: str
    name: str
    dir: Path
    instruction_html: Path | None
    stages_script: Path | None


@dataclass
class ApkInfo:
    path: Path
    name: str
    description: str = ""
    category: str = ""  # "" = лежит прямо в apk/ ("Без категории")
    remote_only: bool = False  # есть на сервере, но ещё не скачан локально
    size: int = -1  # известен, только когда remote_only=True (см. scan_apks)


def scan_cars(cars_dir: Path) -> dict[str, list[ModelInfo]]:
    """brand -> [ModelInfo, ...], отсортировано по имени."""
    brands: dict[str, list[ModelInfo]] = {}
    if not cars_dir.exists():
        return brands

    for brand_dir in sorted(cars_dir.iterdir(), key=lambda p: p.name.lower()):
        if not brand_dir.is_dir() or brand_dir.name.startswith("_"):
            continue
        models = []
        for model_dir in sorted(brand_dir.iterdir(), key=lambda p: p.name.lower()):
            if not model_dir.is_dir():
                continue
            instruction = model_dir / "instruction.html"
            stages_script = model_dir / "stages.py"
            models.append(ModelInfo(
                brand=brand_dir.name,
                name=model_dir.name,
                dir=model_dir,
                instruction_html=instruction if instruction.exists() else None,
                stages_script=stages_script if stages_script.exists() else None,
            ))
        if models:
            brands[brand_dir.name] = models
    return brands


def scan_apks(apk_dir: Path, remote_catalog: list[dict] | None = None) -> list[ApkInfo]:
    """Список общих APK из папки apk/, включая категории — подпапки верхнего
    уровня (без рекурсии глубже). APK прямо в apk/ получают category="".
    Имя/описание можно задать в <файл>.json.

    remote_catalog (см. content_sync.list_shared_apk_catalog) — что есть на
    СЕРВЕРЕ, но, может, ещё не скачано локально: такие записи добавляются
    в результат с remote_only=True (path указывает, куда файл ляжет после
    скачивания — см. content_sync.ensure_apks_downloaded, вызывается прямо
    перед установкой выбранных приложений, а не здесь). Уже скачанный
    локально файл всегда в приоритете перед записью из каталога — местная
    копия может иметь своё <файл>.json с именем/описанием."""
    apks: dict[tuple[str, str], ApkInfo] = {}
    if apk_dir.exists():
        for apk in scan_apk_dir(apk_dir, category=""):
            apks[("", apk.path.name)] = apk
        for sub_dir in sorted(apk_dir.iterdir(), key=lambda p: p.name.lower()):
            if sub_dir.is_dir() and not sub_dir.name.startswith("_"):
                for apk in scan_apk_dir(sub_dir, category=sub_dir.name):
                    apks[(sub_dir.name, apk.path.name)] = apk

    for entry in remote_catalog or []:
        rel = entry["rel_path"]
        if not rel.lower().endswith(".apk"):
            continue
        category, _, filename = rel.rpartition("/")
        key = (category, filename)
        if key in apks:
            continue
        apks[key] = ApkInfo(
            path=(apk_dir / category / filename) if category else (apk_dir / filename),
            name=Path(filename).stem,
            category=category,
            remote_only=True,
            size=entry.get("size", -1),
        )

    return sorted(apks.values(), key=lambda a: (a.category != "", a.category.lower(), a.name.lower()))


def scan_apk_dir(folder: Path, category: str = "") -> list[ApkInfo]:
    """Все *.apk одной папки (без подпапок). Имя/описание можно задать в
    <файл>.json рядом с APK — те же поля "name"/"description", что и в
    apk/README.txt. Используется и для apk/ (см. scan_apks), и для
    "Стандартных приложений" конкретной модели (см. stage_wizard.py)."""
    apks = []
    for apk_path in sorted(folder.glob("*.apk"), key=lambda p: p.name.lower()):
        name = apk_path.stem
        description = ""
        meta_path = apk_path.with_suffix(".json")
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                name = meta.get("name", name)
                description = meta.get("description", "")
            except (json.JSONDecodeError, OSError):
                pass
        apks.append(ApkInfo(path=apk_path, name=name, description=description, category=category))
    return apks
