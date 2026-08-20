"""Работа со съёмными USB-флешками: список дисков и безопасное форматирование."""
from __future__ import annotations
import ctypes
import os
import string
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .adb_utils import find_powershell_path

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.GetLogicalDrives.argtypes = []
_kernel32.GetLogicalDrives.restype = ctypes.c_uint32

_kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
_kernel32.GetDriveTypeW.restype = ctypes.c_uint

_kernel32.GetVolumeInformationW.argtypes = [
    ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
    ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_uint32),
    ctypes.POINTER(ctypes.c_uint32), ctypes.c_wchar_p, ctypes.c_uint32,
]
_kernel32.GetVolumeInformationW.restype = ctypes.c_bool

_kernel32.GetDiskFreeSpaceExW.argtypes = [
    ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulonglong),
    ctypes.POINTER(ctypes.c_ulonglong), ctypes.POINTER(ctypes.c_ulonglong),
]
_kernel32.GetDiskFreeSpaceExW.restype = ctypes.c_bool


@dataclass
class DriveInfo:
    letter: str  # например "E:"
    label: str
    total_bytes: int
    free_bytes: int

    @property
    def display(self):
        size_gb = self.total_bytes / (1024 ** 3)
        label = self.label or "без метки"
        return f"{self.letter}\\   [{label}]   {size_gb:.1f} ГБ"


def _drive_type(root: str) -> int:
    return _kernel32.GetDriveTypeW(root)


_DRIVE_TYPE_NAMES = {
    0: "UNKNOWN", 1: "NO_ROOT_DIR", 2: "REMOVABLE",
    3: "FIXED", 4: "REMOTE", 5: "CDROM", 6: "RAMDISK",
}


def list_drives(include_all: bool = False, base_dir: Path | None = None) -> list[DriveInfo]:
    """По умолчанию — только съёмные USB-флешки (DRIVE_REMOVABLE), как и
    раньше. include_all=True — галочка "Показать все диски" в диалоге:
    часть USB-флешек (особенно большого объёма) Windows определяет как
    DRIVE_FIXED, а не DRIVE_REMOVABLE (бит "removable media" не выставлен
    производителем в дескрипторе устройства) — тогда съёмная флешка вообще
    не появляется в обычном списке. Автоматически отличить такую флешку от
    настоящего внутреннего диска не всегда надёжно возможно, поэтому вместо
    попытки угадать — просто даём технику самому увидеть все локальные
    диски и выбрать нужный (как в аналогичных программах, например Rufus).
    Системный диск и диск, на котором лежит сама программа, не показываем
    в любом режиме — их форматирование в принципе невозможно (см.
    assert_safe_to_format), нет смысла даже предлагать выбрать."""
    system_drive = os.environ.get("SystemDrive", "C:").upper()
    app_drive = ""
    if base_dir is not None:
        try:
            app_drive = Path(base_dir).resolve().drive.upper()
        except OSError:
            pass

    drives = []
    bitmask = _kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not (bitmask >> i) & 1:
            continue
        root = f"{letter}:\\"
        drive_type = _drive_type(root)
        # Печатаем ДЛЯ ЛЮБОГО диска, не только съёмного — попадает в общий
        # "log everything" (см. main_web.py:_enable_debug_log_all,
        # перехватывает весь stdout) без отдельного флага debug_mode здесь:
        # sys.stdout у собранного exe всегда настоящий поток, даже без
        # консоли (проверено), print() безопасен в любой сборке.
        print(f"[usb_utils] {root} type={drive_type} ({_DRIVE_TYPE_NAMES.get(drive_type, 'UNKNOWN')})")

        wanted_types = (DRIVE_REMOVABLE, DRIVE_FIXED) if include_all else (DRIVE_REMOVABLE,)
        if drive_type not in wanted_types:
            continue
        letter_upper = f"{letter}:".upper()
        if letter_upper == system_drive or letter_upper == app_drive:
            continue

        volume_name_buf = ctypes.create_unicode_buffer(261)
        fs_name_buf = ctypes.create_unicode_buffer(261)
        ok = _kernel32.GetVolumeInformationW(
            root, volume_name_buf, len(volume_name_buf),
            None, None, None, fs_name_buf, len(fs_name_buf),
        )
        if not ok:
            print(f"[usb_utils] {root} GetVolumeInformationW failed (носитель не готов/не вставлен)")
            continue  # диск определён, но носитель не готов/не вставлен

        total_bytes = ctypes.c_ulonglong(0)
        free_bytes = ctypes.c_ulonglong(0)
        _kernel32.GetDiskFreeSpaceExW(root, ctypes.byref(free_bytes), ctypes.byref(total_bytes), None)

        drives.append(DriveInfo(
            letter=f"{letter}:",
            label=volume_name_buf.value,
            total_bytes=total_bytes.value,
            free_bytes=free_bytes.value,
        ))
    return drives


class UsbSafetyError(RuntimeError):
    pass


def assert_safe_to_format(letter: str, base_dir: Path):
    """letter вида 'E:'. Дополнительная проверка прямо перед форматированием.
    DRIVE_FIXED разрешён наравне с DRIVE_REMOVABLE — см. list_drives():
    диск с этим типом мог попасть в диалог только через явную галочку
    "Показать все диски", так что подставить сюда что-то не из списка
    (например, из консоли) всё равно нельзя — выбор всегда идёт через UI."""
    root = f"{letter}\\"
    if _drive_type(root) not in (DRIVE_REMOVABLE, DRIVE_FIXED):
        raise UsbSafetyError(
            f"Диск {letter} не определяется как локальный/съёмный диск. Форматирование отменено."
        )

    system_drive = os.environ.get("SystemDrive", "C:").upper()
    if letter.upper() == system_drive:
        raise UsbSafetyError("Нельзя форматировать системный диск.")

    try:
        app_drive = Path(base_dir).resolve().drive.upper()
    except OSError:
        app_drive = ""
    if app_drive and letter.upper() == app_drive:
        raise UsbSafetyError("Нельзя форматировать диск, на котором запущена сама программа.")


def format_drive(letter: str, filesystem: str, label: str, base_dir: Path, log=lambda m: None):
    """letter вида 'E:'. filesystem: 'FAT32' или 'exFAT'."""
    assert_safe_to_format(letter, base_dir)
    drive_letter = letter.rstrip(":")
    safe_label = "".join(ch for ch in (label or "CARINSTALL") if ch.isalnum())[:11] or "CARINSTALL"

    log(f"Форматирование {letter}\\ в {filesystem}...")
    ps_command = (
        f"Format-Volume -DriveLetter {drive_letter} -FileSystem {filesystem} "
        f"-NewFileSystemLabel '{safe_label}' -Confirm:$false -Force | Out-Null"
    )
    result = subprocess.run(
        [find_powershell_path(), "-NoProfile", "-NonInteractive", "-Command", ps_command],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Не удалось отформатировать {letter}\\: {detail}")
    log("Форматирование завершено.")
