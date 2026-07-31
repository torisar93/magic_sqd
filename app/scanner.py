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
    install_script: Path | None
    usb_install_script: Path | None
    usb_files_dir: Path | None
    stages_script: Path | None


@dataclass
class ApkInfo:
    path: Path
    name: str
    description: str = ""


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
            install_script = model_dir / "install.py"
            usb_install_script = model_dir / "usb_install.py"
            usb_files_dir = model_dir / "usb_files"
            stages_script = model_dir / "stages.py"
            models.append(ModelInfo(
                brand=brand_dir.name,
                name=model_dir.name,
                dir=model_dir,
                instruction_html=instruction if instruction.exists() else None,
                install_script=install_script if install_script.exists() else None,
                usb_install_script=usb_install_script if usb_install_script.exists() else None,
                usb_files_dir=usb_files_dir if usb_files_dir.exists() else None,
                stages_script=stages_script if stages_script.exists() else None,
            ))
        if models:
            brands[brand_dir.name] = models
    return brands


def scan_apks(apk_dir: Path) -> list[ApkInfo]:
    """Список общих APK из папки apk/. Имя/описание можно задать в <файл>.json."""
    apks = []
    if not apk_dir.exists():
        return apks

    for apk_path in sorted(apk_dir.glob("*.apk"), key=lambda p: p.name.lower()):
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
        apks.append(ApkInfo(path=apk_path, name=name, description=description))
    return apks
