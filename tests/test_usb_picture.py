"""ПК и мак: что видно, когда магнитолы нет (app/usb_picture.py, с 1.1.22). Повод — маки с паролем по QR: «Магнитола
не подключена» по 14 раз подряд без единой подсказки, нет ли магнитолы на USB или adb видит её offline (№13512, №14266)."""
from __future__ import annotations
import json
import re
from pathlib import Path

from app import usb_picture

ROOT = Path(__file__).resolve().parents[1]
ADB = ("List of devices attached\n"
       "BGUF4R71275f60         offline usb:1-1 product:gwmv2_extend model:AOSP_on_Harman_Platform device:gwmv2_extend "
       "transport_id:3\n"
       "192.168.43.1:5555      unauthorized transport_id:4\n")
MAC_NEW = {"SPUSBHostDataType": [{"_name": "USB 3.1 Bus", "Driver": "AppleT8132USBXHCI", "_items": [
    {"_name": "x9 for arm64", "USBDeviceKeyVendorID": "0x18d1", "USBDeviceKeyProductID": "0x4ee7",
     "USBDeviceKeyVendorName": "Desay SV", "USBDeviceKeySerialNumber": "SECRET123"}]}]}
MAC_OLD = {"SPUSBDataType": [{"_name": "USB31Bus", "_items": [
    {"_name": "Mass Storage", "vendor_id": "0x058f  (Alcor Micro, Corp.)", "product_id": "0x6387",
     "manufacturer": "Generic", "serial_num": "SECRET456"}]}]}


def _runner(outputs):
    def run(args, timeout):
        key = args[1] if args[0].endswith("system_profiler") else args[0]
        value = outputs.get(key, "")
        if isinstance(value, Exception):
            raise value
        return value
    return run


def test_adb_states_without_serials():
    lines = usb_picture.adb_lines("adb", lambda args, timeout: ADB)
    assert lines == ["adb devices: устройство 1 — offline (usb:1-1 product:gwmv2_extend "
                     "model:AOSP_on_Harman_Platform device:gwmv2_extend).",
                     "adb devices: устройство 2 — unauthorized."]
    assert not any("BGUF" in line or "192.168" in line for line in lines)
    assert usb_picture.adb_lines("adb", lambda a, t: "List of devices attached\n\n") == ["adb devices: устройств нет."]
    assert usb_picture.adb_lines("adb", _runner({"adb": FileNotFoundError()}))[0].startswith("adb devices: не ответил")


def test_mac_usb_both_formats_without_serials():
    new = usb_picture.mac_usb_lines(_runner({"SPUSBHostDataType": json.dumps(MAC_NEW)}))
    assert new == ["USB компьютера: 18d1:4ee7 «x9 for arm64» (Desay SV)"]
    old = usb_picture.mac_usb_lines(_runner({"SPUSBHostDataType": json.dumps({"SPUSBHostDataType": []}),
                                             "SPUSBDataType": json.dumps(MAC_OLD)}))
    assert old == ["USB компьютера: 058f:6387 «Mass Storage» (Generic)"]
    empty = usb_picture.mac_usb_lines(_runner({"SPUSBHostDataType": json.dumps({"SPUSBHostDataType": [{"_name": "Bus"}]})}))
    assert empty == ["USB компьютера: ни одного устройства, кроме самих USB-шин."]
    assert "SECRET" not in " ".join(new + old)
    # system_profiler не ответил — не «пусто» (разбор 09.11.2026: одна фраза на оба случая путала)
    silent = usb_picture.mac_usb_lines(_runner({"SPUSBHostDataType": "", "SPUSBDataType": OSError()}))
    assert silent == ["USB компьютера: system_profiler не ответил — список USB не получен."]


def test_windows_powershell_writes_utf8():
    """Без этого русские имена устройств в журнале — «�������⭮� USB-���ன�⢮» (cp866 консоли, №14732)."""
    assert usb_picture._WIN_SCRIPT.startswith("[Console]::OutputEncoding = [Text.Encoding]::UTF8;")


def test_windows_usb_without_serials_and_hubs():
    out = ("VID_18D1&PID_4EE7|x9h_a01g|Error\nVID_18D1&PID_4EE7&MI_01|ADB Interface|OK\n"
           "VID_8087&PID_0029|Generic USB Hub|OK\nVID_18D1&PID_4EE7|x9h_a01g|Error\n")
    lines = usb_picture.windows_usb_lines(_runner({"powershell": out}))
    assert lines == ["USB компьютера: 18d1:4ee7 «x9h_a01g» (Error)", "USB компьютера: 18d1:4ee7&MI_01 «ADB Interface»"]


def test_describe_never_raises():
    lines = usb_picture.describe("adb", "darwin", _runner({"adb": ADB, "SPUSBHostDataType": "not json",
                                                           "SPUSBDataType": OSError()}))
    assert lines[0].startswith("adb devices: устройство 1 — offline") and lines[-1].startswith("USB компьютера:")


def test_wizard_logs_usb_picture_next_to_no_device_window():
    js = (ROOT / "app/web/frontend/js/screens/stage_wizard.js").read_text(encoding="utf-8")
    find = re.search(r"async function findDevice\(.*?\n  }\n", js, flags=re.S).group(0)
    assert find.index('showUserError({ id: unauthorized ? "unauthorized" : "no_device" }') < find.index("logUsbPicture();")
    helper = re.search(r"async function logUsbPicture\(\) \{.*?\n  }\n", js, flags=re.S).group(0)
    assert "Date.now() - usbPictureAt < 60000" in helper and "install_usb_picture" in helper
    bridge = (ROOT / "app/web/bridge.py").read_text(encoding="utf-8")
    assert "def install_usb_picture(self) -> list:\n        return self._install.usb_picture()" in bridge
