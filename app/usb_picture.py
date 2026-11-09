"""Что компьютер видит, когда магнитолы в adb нет (с 1.1.22; владелец 07.11.2026: «да, делай и выпускай 1.1.22»).

Повод — маки на Apple Silicon с моделями, где ADB открывают паролем по QR (Jolion 2026, Cityray после 2026): пароль
получен, а дальше «Магнитола не подключена» по 14 раз подряд (логи №13512, №14266), и по логу не понять, нет ли
магнитолы на USB вовсе (кабель, окно macOS «Разрешить подключение аксессуара?») или adb видит её «offline»/
«unauthorized». Так же, как Android с 1.1.21 (UsbPicture.kt): ответ adb и список USB-устройств компьютера — производитель,
имя и VID:PID. Серийные номера не пишем: у adb вместо них «устройство 1, 2…», у USB их не берём вовсе.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
MAX_USB_LINES = 15
_HEX4 = re.compile(r"0x([0-9a-fA-F]{1,4})")
_WIN_ID = re.compile(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})(&MI_\d+)?", re.I)


def _run(args: list[str], timeout: int) -> str:
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                            creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL)
    return result.stdout or ""


def adb_lines(adb_path: str, run=_run) -> list[str]:
    """«adb devices -l» без серийных номеров: состояние (device/offline/unauthorized) и что adb знает о магнитоле."""
    try:
        output = run([adb_path, "devices", "-l"], 15)
    except (OSError, subprocess.SubprocessError) as exc:
        return [f"adb devices: не ответил ({type(exc).__name__})."]
    rows = [line.split() for line in output.splitlines()[1:] if line.strip() and not line.startswith("*")]
    if not rows:
        return ["adb devices: устройств нет."]
    lines = []
    for number, parts in enumerate(rows, 1):
        state = parts[1] if len(parts) > 1 else "?"
        known = [part for part in parts[2:] if part.split(":", 1)[0] in ("usb", "product", "model", "device")]
        lines.append(f"adb devices: устройство {number} — {state}" + (f" ({' '.join(known)})" if known else "") + ".")
    return lines


def _hex4(value) -> str:
    found = _HEX4.search(str(value or ""))
    return found.group(1).lower().zfill(4) if found else ""


def _mac_devices(node, out: list) -> None:
    """Устройства из system_profiler: и новый формат (SPUSBHostDataType, USBDeviceKeyVendorID…), и старый
    (SPUSBDataType, vendor_id/product_id/manufacturer). Шины и корневые хабы без VID/PID пропускаются."""
    if isinstance(node, list):
        for item in node:
            _mac_devices(item, out)
        return
    if not isinstance(node, dict):
        return
    flat = {key.lower().replace("_", ""): value for key, value in node.items()}
    vid = next((_hex4(v) for k, v in flat.items() if k.endswith("vendorid")), "")
    pid = next((_hex4(v) for k, v in flat.items() if k.endswith("productid")), "")
    if vid and pid and node.get("_name"):
        maker = next((str(v) for k, v in flat.items() if k in ("manufacturer", "usbdevicekeyvendorname", "vendorname")), "")
        out.append(f"{vid}:{pid} «{node['_name']}»" + (f" ({maker})" if maker and maker not in node["_name"] else ""))
    for value in node.values():
        if isinstance(value, (list, dict)):
            _mac_devices(value, out)


def mac_usb_lines(run=_run) -> list[str]:
    answered = False
    for data_type in ("SPUSBHostDataType", "SPUSBDataType"):  # Apple Silicon на macOS 15+ — первый, старые — второй
        try:
            data = json.loads(run(["/usr/sbin/system_profiler", data_type, "-json"], 25) or "{}")
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
        answered = answered or bool(data.get(data_type))
        devices: list[str] = []
        _mac_devices(data.get(data_type) or [], devices)
        if devices:
            return [f"USB компьютера: {device}" for device in devices[:MAX_USB_LINES]]
    # «Пусто» и «не узнать» — разные вещи: первое значит, что Mac не видит магнитолу по USB (кабель, порт, переходник).
    if not answered:
        return ["USB компьютера: system_profiler не ответил — список USB не получен."]
    return ["USB компьютера: ни одного устройства, кроме самих USB-шин."]


# Вывод PowerShell — в UTF-8: по умолчанию он в кодировке консоли (cp866), и русские имена устройств в журнале
# становились «�������⭮� USB-���ன�⢮» (1.1.22, все 7 журналов с диагностикой, №14732).
_WIN_SCRIPT = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
               "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB\\VID_*' } | "
               "ForEach-Object { $_.InstanceId.Split('\\')[1] + '|' + $_.FriendlyName + '|' + $_.Status }")


def windows_usb_lines(run=_run) -> list[str]:
    try:
        output = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _WIN_SCRIPT], 20)
    except (OSError, subprocess.SubprocessError):
        return ["USB компьютера: список не получен."]
    lines, seen = [], set()
    for row in output.splitlines():
        ident, _, rest = row.strip().partition("|")
        name, _, status = rest.partition("|")
        found = _WIN_ID.search(ident)
        if not found or "hub" in name.lower():
            continue
        line = f"{found.group(1).lower()}:{found.group(2).lower()}{found.group(3) or ''} «{name.strip()}»" + (
            f" ({status.strip()})" if status.strip() and status.strip() != "OK" else "")
        if line not in seen:
            seen.add(line)
            lines.append(f"USB компьютера: {line}")
    return lines[:MAX_USB_LINES] or ["USB компьютера: ни одного USB-устройства."]


def describe(adb_path: str, platform: str = sys.platform, run=_run) -> list[str]:
    """Строки для журнала: что ответил adb и что видно на USB. Ни одна проверка не роняет программу."""
    lines = adb_lines(adb_path, run)
    try:
        if platform == "darwin":
            lines += mac_usb_lines(run)
        elif platform == "win32":
            lines += windows_usb_lines(run)
    except Exception as exc:  # noqa: BLE001 — диагностика необязательна, работе не мешает
        lines.append(f"USB компьютера: не получилось ({type(exc).__name__}).")
    return lines
