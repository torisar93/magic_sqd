"""Объект ctx, передаваемый в run(ctx) "usb"-этапа stages.py модели (режим
"USB-флешка", см. stage_wizard.py/usb_dialog.py)."""
from __future__ import annotations
import shutil
import zipfile
from pathlib import Path

from .install_context import InstallCancelled

# Тот же размер, что и в Android-версии (см. UsbFlashWrite.kt:
# WRITE_CHUNK_SIZE) — потоковое копирование чанками вместо shutil.copy2
# целиком нужно для настоящего прогресса по байтам ВНУТРИ одного файла:
# usb_files реально содержат прошивки "под гигабайт" (Haval M6 — до
# ~700МБ), и раньше техник видел только "Копирую: файл.iso", а следующая
# строка появлялась через несколько минут, без единого признака, что
# программа вообще что-то делает (жалоба клиента, 2026-09-21).
_COPY_CHUNK_SIZE = 4 * 1024 * 1024


class UsbContext:
    def __init__(self, drive_root: Path, model_dir: Path, selected_apks, log_fn, cancel_flag,
                 variant: str | None = None, shared_dir: Path | None = None,
                 on_progress=lambda *a: None, files_total: int = 0):
        """variant — выбранный техником вариант содержимого (Full/Lite/...),
        если у usb-этапа есть несколько (см. StepSpec.variants в
        car_generator.py, stage_wizard.py._run_usb_stage) — None для
        обычных однонаборных этапов, как и раньше. shared_dir — см.
        InstallContext: cars/_shared/, для наборов файлов, общих на много
        моделей (StepSpec.usb_shared_folder), одна копия на всех, а не в
        каждой модели отдельно.

        on_progress(path, bytes_done, bytes_total, files_done, files_total,
        state) — вызывается по мере копирования (state="running", в момент
        побайтового чтения ОДНОГО файла) и по завершении каждого файла
        (state="done"). files_total — общее число файлов, которые в ЦЕЛОМ
        запишутся за этот запуск usb-этапа (посчитано ЗАРАНЕЕ в
        app/web/api/usb_api.py — та же структура, что фактически копирует
        сгенерированный usb_step_N, см. car_generator.py, но пересчитанная
        отдельно ДО старта, чтобы фронтенд успел показать список файлов
        раньше первого события прогресса). files_done — растёт на 1 после
        КАЖДОГО целого файла (не чанка), общий счётчик на весь ctx — не
        сбрасывается между copy_dir/copy_selected_apks/extract_zip в рамках
        одного запуска, поэтому очередь на экране идёт непрерывно, даже
        если модель зовёт несколько таких методов подряд."""
        self.drive_root = Path(drive_root)
        self.model_dir = Path(model_dir)
        self.files_dir = self.model_dir / "files"
        self.usb_files_dir = self.model_dir / "usb_files"
        self.shared_dir = Path(shared_dir) if shared_dir is not None else None
        self.selected_apks = [Path(p) for p in selected_apks]
        self.variant = variant
        self._log_fn = log_fn
        self._cancel_flag = cancel_flag
        self._on_progress = on_progress
        self._files_total = files_total
        self._files_done = 0

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
    def _copy_stream(self, src: Path, dst: Path) -> None:
        """Потоковое копирование одного файла чанками (вместо shutil.copy2
        целиком) — даёт настоящий прогресс по байтам внутри файла, см.
        докстринг модуля/__init__. shutil.copystat в конце — та же
        метадата (mtime и т.п.), что copy2 сохранял раньше."""
        total = src.stat().st_size
        done = 0
        path_str = str(src)
        with open(src, "rb") as fsrc, open(dst, "wb") as fdst:
            while True:
                self.check_cancelled()
                chunk = fsrc.read(_COPY_CHUNK_SIZE)
                if not chunk:
                    break
                fdst.write(chunk)
                done += len(chunk)
                self._on_progress(path_str, done, total, self._files_done, self._files_total, "running")
        shutil.copystat(src, dst)
        self._files_done += 1
        self._on_progress(path_str, total, total, self._files_done, self._files_total, "done")

    def copy_file(self, local_path, dest_relative_path):
        self.check_cancelled()
        local_path = Path(local_path)
        target = self.drive_root / dest_relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        self.log(f"Копирую: {local_path.name} -> {dest_relative_path}")
        self._copy_stream(local_path, target)
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
                self._copy_stream(path, target)

    def copy_selected_apks(self, dest_relative_dir=""):
        for apk in self.selected_apks:
            self.copy_file(apk, str(Path(dest_relative_dir) / apk.name))

    def extract_zip(self, zip_path, dest_relative_dir=""):
        """Распаковывает .zip-архив в корень флешки (или подпапку) — часть
        пакетов обновления (например Geely Coolray "Community Full") ждут
        именно распакованное содержимое в корне FAT32-флешки, а не сам
        .zip-файл (в отличие от файлов прошивки того же Coolray, которые
        нужно класть В КОРЕНЬ КАК ЕСТЬ, без распаковки — см. copy_file для
        этого случая). Прогресс — по числу элементов архива (у zipfile нет
        простого способа получить побайтовый прогресс ВНУТРИ одного extract,
        а члены архива обычно значительно меньше "прошивок под гигабайт" из
        copy_file/copy_dir — этого достаточно)."""
        self.check_cancelled()
        zip_path = Path(zip_path)
        dest_root = self.drive_root / dest_relative_dir
        dest_root.mkdir(parents=True, exist_ok=True)
        self.log(f"Распаковываю: {zip_path.name} -> /{dest_relative_dir}")
        path_str = str(zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                self.check_cancelled()
                zf.extract(member, dest_root)
                self._files_done += 1
                self._on_progress(path_str, member.file_size, member.file_size,
                                   self._files_done, self._files_total, "done")
        self.log(f"Распаковано: {zip_path.name}")

    def write_text(self, dest_relative_path, content, encoding="utf-8"):
        self.check_cancelled()
        target = self.drive_root / dest_relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding=encoding)
        self.log(f"Записан файл: {dest_relative_path}")
        self._files_done += 1
        size = target.stat().st_size
        self._on_progress(str(target), size, size, self._files_done, self._files_total, "done")


def write_flash_files(ctx: UsbContext, block: dict) -> None:
    """Блок «Запись на флешку» этапа «Флешка» (см. car_generator.py:
    FlashBlockSpec/_render_flash_blocks_entry) — то же, что
    сгенерированный usb_step_N целого этапа (файлы в корень флешки, выбранные
    приложения, общий набор из _shared/), но только то, что отмечено в ЭТОМ
    блоке: у этапа их может быть несколько, на разные флешки. На Android то же
    делает WebBridge.kt: usbRunStage по списку файлов блока."""
    for raw in block.get("files") or []:
        path = Path(raw)
        if path.is_dir():
            ctx.copy_dir(path, path.name)
        elif path.is_file():
            ctx.copy_file(path, path.name)
        else:
            raise FileNotFoundError(f"Файл «{path.name}» не найден среди файлов модели — "
                                    "проверьте интернет и повторите запись.")
    if block.get("copy_selected_apks") and ctx.selected_apks:
        ctx.copy_selected_apks(block.get("apks_dest", ""))
    shared_folder = block.get("shared_folder")
    if shared_folder and ctx.shared_dir is not None and (ctx.shared_dir / shared_folder).is_dir():
        ctx.copy_dir(ctx.shared_dir / shared_folder, "")
