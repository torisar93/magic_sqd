"""Работа со съёмными USB-флешками: список дисков и безопасное форматирование."""
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


def list_removable_drives() -> list[DriveInfo]:
    """Только съёмные USB-флешки (DRIVE_REMOVABLE) — внутренние диски и системный диск сюда не попадают."""
    drives = []
    bitmask = _kernel32.GetLogicalDrives()
    for i, letter in enumerate(string.ascii_uppercase):
        if not (bitmask >> i) & 1:
            continue
        root = f"{letter}:\\"
        if _drive_type(root) != DRIVE_REMOVABLE:
            continue

        volume_name_buf = ctypes.create_unicode_buffer(261)
        fs_name_buf = ctypes.create_unicode_buffer(261)
        ok = _kernel32.GetVolumeInformationW(
            root, volume_name_buf, len(volume_name_buf),
            None, None, None, fs_name_buf, len(fs_name_buf),
        )
        if not ok:
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
    """letter вида 'E:'. Дополнительная проверка прямо перед форматированием."""
    root = f"{letter}\\"
    if _drive_type(root) != DRIVE_REMOVABLE:
        raise UsbSafetyError(
            f"Диск {letter} не определяется как съёмная USB-флешка. Форматирование отменено."
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
        timeout=600, creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Не удалось отформатировать {letter}\\: {detail}")
    log("Форматирование завершено.")
