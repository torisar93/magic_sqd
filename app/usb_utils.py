"""Работа со съёмными USB-флешками — платформенный диспетчер.

Публичный интерфейс общий (DriveInfo, list_drives, format_drive,
UsbSafetyError — см. app/web/api/usb_api.py), реализации разные:
usb_utils_win.py (WinAPI/PowerShell) и usb_utils_mac.py (diskutil). Раньше
это был один файл только с Windows-кодом — ctypes.WinDLL("kernel32") падает
уже на ИМПОРТЕ на любой другой ОС, а не только при вызове (см. CLAUDE_
MACBOOK_HANDOFF.md/память проекта про порт на macOS)."""
import sys
from pathlib import Path

if sys.platform == "win32":
    from .usb_utils_win import DriveInfo, UsbSafetyError, format_drive, list_drives
elif sys.platform == "darwin":
    from .usb_utils_mac import DriveInfo, UsbSafetyError, format_drive, list_drives
else:
    # Раньше падало здесь же, на импорте — это ломало pytest-сборку ЛЮБОГО
    # теста, транзитивно импортирующего usb_api.py/qr_adb_api.py на CI
    # (.github/workflows/tests.yml: runs-on ubuntu-latest, sys.platform ==
    # "linux") — сами эти тесты (например, tests/test_usb_context_progress.py)
    # реальные list_drives()/format_drive() не вызывают вовсе, тестируют
    # только логику поверх них. Откладываем ошибку до реального ВЫЗОВА,
    # а не до самого импорта модуля — на Windows/macOS (единственные
    # платформы, для которых собирается приложение) поведение не меняется.
    class UsbSafetyError(RuntimeError):
        pass

    class DriveInfo:
        pass

    def _unsupported_platform(*_args, **_kwargs):
        raise NotImplementedError(f"USB-флешки пока не поддерживаются на {sys.platform}")

    list_drives = _unsupported_platform
    format_drive = _unsupported_platform


def drive_root_path(drive_letter: str) -> Path:
    """Путь к корню накопителя из drive_letter, как его вернул list_drives()
    (DriveInfo.letter). На Windows это буква с двоеточием ("E:") — Path("E:")
    БЕЗ обратного слеша означает текущий каталог НА этом диске, а не его
    корень, обратный слеш обязателен ("E:\\") — тот же приём, что и в
    app/web/api/usb_api.py:_worker. На macOS/Linux letter — уже полный
    абсолютный путь точки монтирования ("/Volumes/..." — см.
    usb_utils_mac.py: DriveInfo.letter), Path(...) как есть.

    Раньше app/web/api/qr_adb_api.py строил путь как
    Path(f"{drive_letter}\\...") БЕЗУСЛОВНО (без этой проверки платформы) —
    на macOS "/Volumes/CARINSTALL" превращалось в файл с буквальным именем
    "CARINSTALL\\svlog.flag" внутри /Volumes/, т.е. запись/чтение шли МИМО
    настоящей флешки целиком. Найдено 2026-09-21 при разборе жалоб «пароль
    QR ADB неверный» — вероятная реальная причина на macOS."""
    if sys.platform == "win32":
        return Path(f"{drive_letter}\\")
    return Path(drive_letter)


__all__ = ["DriveInfo", "UsbSafetyError", "format_drive", "list_drives", "drive_root_path"]
