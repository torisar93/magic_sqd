"""Логика подготовки USB-флешки: usb_install.py модели либо копирование по умолчанию."""
import importlib.util
from pathlib import Path


def run_usb_install(model, ctx):
    """Если у модели есть usb_install.py — запускает его run(ctx).
    Иначе копирует usb_files/ модели и отмеченные галочками APK в корень флешки."""
    if model.usb_install_script:
        module = _load_module(model.usb_install_script)
        if not hasattr(module, "run"):
            raise RuntimeError("usb_install.py должен содержать функцию run(ctx)")
        module.run(ctx)
        return

    if ctx.usb_files_dir.exists():
        ctx.log("Копирую файлы модели из usb_files/")
        ctx.copy_dir(ctx.usb_files_dir, "")
    else:
        ctx.log("У модели нет папки usb_files/ — пропускаю файлы модели")

    if ctx.selected_apks:
        ctx.log(f"Копирую {len(ctx.selected_apks)} отмеченных приложений")
        ctx.copy_selected_apks("")
    else:
        ctx.log("Ни одно приложение не отмечено галочкой")


def _load_module(script_path: Path):
    spec = importlib.util.spec_from_file_location(
        f"car_usb_script_{abs(hash(str(script_path)))}", script_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
