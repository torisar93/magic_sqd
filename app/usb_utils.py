"""Работа со съёмными USB-флешками — платформенный диспетчер.

Публичный интерфейс общий (DriveInfo, list_drives, format_drive,
UsbSafetyError — см. app/web/api/usb_api.py), реализации разные:
usb_utils_win.py (WinAPI/PowerShell) и usb_utils_mac.py (diskutil). Раньше
это был один файл только с Windows-кодом — ctypes.WinDLL("kernel32") падает
уже на ИМПОРТЕ на любой другой ОС, а не только при вызове (см. CLAUDE_
MACBOOK_HANDOFF.md/память проекта про порт на macOS)."""
import sys

if sys.platform == "win32":
    from .usb_utils_win import DriveInfo, UsbSafetyError, format_drive, list_drives
elif sys.platform == "darwin":
    from .usb_utils_mac import DriveInfo, UsbSafetyError, format_drive, list_drives
else:
    raise NotImplementedError(f"USB-флешки пока не поддерживаются на {sys.platform}")

__all__ = ["DriveInfo", "UsbSafetyError", "format_drive", "list_drives"]
