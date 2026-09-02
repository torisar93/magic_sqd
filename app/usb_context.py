"""Объект ctx, передаваемый в run(ctx) "usb"-этапа stages.py модели (режим
"USB-флешка", см. stage_wizard.py/usb_dialog.py)."""
from __future__ import annotations
import shutil
import zipfile
from pathlib import Path

from .install_context import InstallCancelled


class UsbContext:
    def __init__(self, drive_root: Path, model_dir: Path, selected_apks, log_fn, cancel_flag,
                 variant: str | None = None, shared_dir: Path | None = None):
        """variant — выбранный техником вариант содержимого (Full/Lite/...),
        если у usb-этапа есть несколько (см. StepSpec.variants в
        car_generator.py, stage_wizard.py._run_usb_stage) — None для
        обычных однонаборных этапов, как и раньше. shared_dir — см.
        InstallContext: cars/_shared/, для наборов файлов, общих на много
        моделей (StepSpec.usb_shared_folder), одна копия на всех, а не в
        каждой модели отдельно."""
        self.drive_root = Path(drive_root)
        self.model_dir = Path(model_dir)
        self.files_dir = self.model_dir / "files"
        self.usb_files_dir = self.model_dir / "usb_files"
        self.shared_dir = Path(shared_dir) if shared_dir is not None else None
        self.selected_apks = [Path(p) for p in selected_apks]
        self.variant = variant
        self._log_fn = log_fn
        self._cancel_flag = cancel_flag

    # --- служебное -------------------------------------------------
    def log(self, message):
        self._log_fn(str(message))

    def check_cancelled(self):
        if self._cancel_flag.is_set():
            raise InstallCancelled("Копирование остановлено пользователем.")

    # --- файлы модели ------------------------------------------------
    def file(self, relative_path):
        """Путь к файлу внутри files/ данной модели (общие с ADB-режимом файлы)."""
        return self.files_dir / relative_path

    def usb_file(self, relative_path):
        """Путь к файлу внутри usb_files/ данной модели."""
        return self.usb_files_dir / relative_path

    # --- копирование на флешку ----------------------------------------
    def copy_file(self, local_path, dest_relative_path):
        self.check_cancelled()
        local_path = Path(local_path)
        target = self.drive_root / dest_relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        self.log(f"Копирую: {local_path.name} -> {dest_relative_path}")
        shutil.copy2(local_path, target)
        return target

    def copy_dir(self, local_dir, dest_relative_dir=""):
        local_dir = Path(local_dir)
        dest_root = self.drive_root / dest_relative_dir
        for path in local_dir.rglob("*"):
            self.check_cancelled()
            rel = path.relative_to(local_dir)
            target = dest_root / rel
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                self.log(f"Копирую: {rel}")
                shutil.copy2(path, target)

    def copy_selected_apks(self, dest_relative_dir=""):
        for apk in self.selected_apks:
            self.copy_file(apk, str(Path(dest_relative_dir) / apk.name))

    def extract_zip(self, zip_path, dest_relative_dir=""):
        """Распаковывает .zip-архив в корень флешки (или подпапку) — часть
        пакетов обновления (например Geely Coolray "Community Full") ждут
        именно распакованное содержимое в корне FAT32-флешки, а не сам
        .zip-файл (в отличие от файлов прошивки того же Coolray, которые
        нужно класть В КОРЕНЬ КАК ЕСТЬ, без распаковки — см. copy_file для
        этого случая)."""
        self.check_cancelled()
        zip_path = Path(zip_path)
        dest_root = self.drive_root / dest_relative_dir
        dest_root.mkdir(parents=True, exist_ok=True)
        self.log(f"Распаковываю: {zip_path.name} -> /{dest_relative_dir}")
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                self.check_cancelled()
                zf.extract(member, dest_root)
        self.log(f"Распаковано: {zip_path.name}")

    def write_text(self, dest_relative_path, content, encoding="utf-8"):
        self.check_cancelled()
        target = self.drive_root / dest_relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding=encoding)
        self.log(f"Записан файл: {dest_relative_path}")
