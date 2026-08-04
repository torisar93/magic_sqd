"""Сканирование папок cars/ и apk/."""
import json
from dataclasses import dataclass, field
from pathlib import Path


# Маркер "для этой модели/модификации пока нет известного способа
# установки" (см. app/stage_wizard.py: NO_INSTRUCTION_YET_HTML) — просто
# пустой файл, который кладут прямо в папку модели/модификации, без правки
# через редактор. Явный маркер значит "мы специально отслеживаем эту
# машину, способа пока не нашли", и вместо обычной ошибки показывает
# дружелюбный текст с кнопкой "Сообщить о проблеме" (см. report_dialog.py:
# REASONS).
NO_INSTRUCTION_MARKER = "no_instruction.txt"


@dataclass
class ModelInfo:
    brand: str
    name: str
    dir: Path
    stages_script: Path | None
    modification: str | None = None  # см. ModelGroup — None, если у модели нет слоя модификаций
    no_instruction: bool = False  # см. NO_INSTRUCTION_MARKER

    @property
    def display_label(self) -> str:
        """"Marka / Model" или "Marka / Model — Модификация" — для заголовков
        окон, лога и текста жалоб (см. gui.py, stage_wizard.py, usb_dialog.py)."""
        model_part = f"{self.name} — {self.modification}" if self.modification else self.name
        return f"{self.brand} / {model_part}"


@dataclass
class ModelGroup:
    """Один пункт в списке "Модель" в главном окне (см. gui.py) — либо сама
    по себе устанавливаемая модель (leaf — свои install.py/stages.py прямо
    в этой папке), либо "зонтик" с несколькими модификациями (рестайлинг,
    версии для разных рынков и т.п.) — например Geely Monjaro:
    "ОД Дорест"/"ПИ Дорест" как отдельные подпапки внутри cars/Geely/Monjaro/.
    Слой модификаций необязателен и определяется автоматически по форме
    папки (см. scan_cars) — ничего специально включать не нужно."""
    name: str
    leaf: ModelInfo | None
    modifications: list[ModelInfo]

    @property
    def has_modifications(self) -> bool:
        return bool(self.modifications)


@dataclass
class ApkInfo:
    path: Path
    name: str
    description: str = ""
    category: str = ""  # "" = лежит прямо в apk/ ("Без категории")
    remote_only: bool = False  # есть на сервере, но ещё не скачан локально
    size: int = -1  # известен, только когда remote_only=True (см. scan_apks)


def scan_cars(cars_dir: Path) -> dict[str, list[ModelGroup]]:
    """brand -> [ModelGroup, ...], отсортировано по имени.

    Слой модификаций (см. ModelGroup) определяется автоматически по форме
    папки, без отдельного маркера: если у папки модели есть СВОИ
    install.py/stages.py/instruction.html — это обычная модель (leaf), как
    и раньше. Если своих файлов нет, но есть подпапки — каждая подпапка со
    своими install.py/stages.py/instruction.html становится модификацией.
    Полностью пустая папка модели (ни своих файлов, ни подпапок) — как и
    раньше, просто leaf без instruction/stages (появится в списке, но с
    предупреждением и неактивной установкой, как и всегда для недоделанных
    моделей)."""
    brands: dict[str, list[ModelGroup]] = {}
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
                groups.append(ModelGroup(name=model_dir.name, leaf=leaf, modifications=[]))
            else:
                modifications = [
                    _build_model_info(brand_dir.name, model_dir.name, sub.name, sub)
                    for sub in sub_dirs
                ]
                groups.append(ModelGroup(name=model_dir.name, leaf=None, modifications=modifications))
        if groups:
            brands[brand_dir.name] = groups
    return brands


def _has_own_model_files(model_dir: Path) -> bool:
    return any((model_dir / name).exists() for name in ("stages.py", "install.py"))


_MODEL_PAYLOAD_DIR_NAMES = {"files", "usb_files"}


def _model_sub_dirs(model_dir: Path) -> list[Path]:
    """Подпапки — кандидаты в модификации (см. scan_cars). Исключает
    files/usb_files — это payload-папки САМОЙ модели (см. install.py/
    stages.py: ctx.file/ctx.usb_file), не модификации — иначе модель без
    своих install.py/stages.py, но уже с пустыми files/usb_files-
    заглушками (обычное промежуточное состояние при создании новой
    модели), была бы ошибочно принята за "зонтик" с модификациями "files"
    и "usb_files"."""
    return sorted(
        (p for p in model_dir.iterdir()
         if p.is_dir() and not p.name.startswith("_") and p.name not in _MODEL_PAYLOAD_DIR_NAMES),
        key=lambda p: p.name.lower())


def _build_model_info(brand: str, name: str, modification: str | None, leaf_dir: Path) -> ModelInfo:
    stages_script = leaf_dir / "stages.py"
    return ModelInfo(
        brand=brand,
        name=name,
        dir=leaf_dir,
        stages_script=stages_script if stages_script.exists() else None,
        modification=modification,
        no_instruction=(leaf_dir / NO_INSTRUCTION_MARKER).exists(),
    )


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
