"""Система техника одной строкой — для шапки журнала сессии («Компьютер: …», stage_wizard.js): по client_id её
не понять. Отдельный модуль без pywebview — чтобы его можно было проверять тестами без интерфейса."""
from __future__ import annotations
import platform
import sys


def os_description() -> str:
    try:
        if sys.platform == "darwin":
            return f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
        if sys.platform == "win32":
            build = sys.getwindowsversion().build
            name = "Windows 11" if platform.release() == "10" and build >= 22000 else f"Windows {platform.release()}"
            return f"{name} (сборка {build}, {platform.machine()})"
        return platform.platform()
    except Exception:  # noqa: BLE001 - шапка лога не должна мешать запуску
        return ""
