"""Changan (прошивка changan_car — UNI-K, CS35 Plus New и родня): команды shell спрашивают пароль подтверждения со
stdin («please input verify password:»), без него — «verify failed!», и ни одной установки (логи №1073 — ПК, №3524–3529 —
Android). Программа подаёт известный пароль сама (app/adb_utils.py: Adb.shell; Android — usb/ShellVerifyPassword.kt).
Проверено и на эмуляторе с поддельным запросом пароля в cmd package (06.10.2026)."""
from __future__ import annotations
import re
import subprocess
from pathlib import Path

import pytest

from app import adb_utils
from app.adb_utils import Adb, AdbError

KOTLIN = Path(__file__).resolve().parents[1] / "android/app/src/main/java/ru/magicsqd/mobile/usb"
WRAPPED = re.compile(r"^echo (\S+) \| \{\n(.*)\n\}$", re.S)


class FakeChangan:
    """adb shell магнитолы: correct=None — пароля не спрашивает; иначе команда проходит, только если первой строкой
    stdin пришёл верный пароль. Команде, которой stdin нужен под данные (cat … | pm install -S), пароль не подать."""

    def __init__(self, correct: str | None):
        self.correct = correct
        self.commands: list[str] = []

    def run(self, *args, check=True, timeout=120):
        assert args[0] == "shell"
        command = args[1]
        self.commands.append(command)
        if self.correct is None:
            return subprocess.CompletedProcess(args, 0, f"out:{command}\n", "")
        m = WRAPPED.match(command)
        if m is None or "pm install -S" in m.group(2) or m.group(1) != self.correct:
            return subprocess.CompletedProcess(args, 1, "please input verify password:verify failed!\n", "")
        real = m.group(2)
        out = "1\n" if real.startswith("getprop") else "Success\n"
        return subprocess.CompletedProcess(args, 0, "please input verify password:" + out, "")


def _adb(device: FakeChangan, logs: list) -> Adb:
    adb = Adb("adb", "SER", log=logs.append)
    adb.run = device.run
    return adb


def test_ordinary_device_one_call_no_password():
    device, logs = FakeChangan(None), []
    adb = _adb(device, logs)
    result = adb.shell("pm list packages")
    assert result.stdout == "out:pm list packages\n"
    assert device.commands == ["pm list packages"] and adb.verify_password is None and logs == []


def test_changan_password_found_and_prompt_stripped():
    device, logs = FakeChangan("adb36987"), []
    adb = _adb(device, logs)
    result = adb.shell("pm install -r /data/local/tmp/a.apk")
    assert result.returncode == 0 and result.stdout == "Success\n"
    assert adb.verify_password == "adb36987"
    # как есть → первый пароль (не тот) → второй
    assert [WRAPPED.match(c).group(1) if WRAPPED.match(c) else None for c in device.commands] == [None, "adb369875", "adb36987"]
    assert any("спрашивает пароль" in m for m in logs) and any("подошёл" in m for m in logs)


def test_found_password_is_fed_to_every_next_command():
    device, logs = FakeChangan("adb369875"), []
    adb = _adb(device, logs)
    adb.shell("pm install -r /data/local/tmp/a.apk")
    device.commands.clear()
    assert adb.shell("pm install -r /data/local/tmp/b.apk").stdout == "Success\n"
    assert device.commands == ["echo adb369875 | {\npm install -r /data/local/tmp/b.apk\n}"]


def test_stream_install_cannot_take_password_but_password_is_kept():
    device, logs = FakeChangan("adb369875"), []
    adb = _adb(device, logs)
    adb.shell("pm install -r /data/local/tmp/a.apk")
    result = adb.shell("cat /data/local/tmp/a.apk | pm install -S 5", check=False)
    assert result.returncode == 1 and "verify failed" in result.stdout
    assert adb.verify_password == "adb369875"
    with pytest.raises(AdbError, match="verify failed"):
        adb.shell("cat /data/local/tmp/a.apk | pm install -S 5")


def test_no_known_password_fits():
    device, logs = FakeChangan("something-else"), []
    adb = _adb(device, logs)
    result = adb.shell("pm install -r /data/local/tmp/a.apk", check=False)
    assert result.returncode == 1 and "verify failed" in result.stdout and adb.verify_password is None
    assert any("не подошли" in m for m in logs)
    with pytest.raises(AdbError, match="verify failed"):
        adb.shell("pm install -r /data/local/tmp/a.apk")


def test_boot_wait_goes_through_password(monkeypatch):
    device, logs = FakeChangan("adb36987"), []
    adb = _adb(device, logs)
    monkeypatch.setattr(adb_utils.time, "sleep", lambda s: None)
    adb.wait_boot_completed(timeout=5)
    assert adb.verify_password == "adb36987"


def test_android_copy_matches_desktop():
    code = (KOTLIN / "ShellVerifyPassword.kt").read_text(encoding="utf-8")
    passwords = re.search(r"PASSWORDS = listOf\(([^)]*)\)", code).group(1)
    assert tuple(re.findall(r'"([^"]+)"', passwords)) == adb_utils.VERIFY_PASSWORDS
    assert 'fun wrap(command: String, password: String) = "echo $password | {\\n$command\\n}"' in code
    assert adb_utils.with_verify_password("a && b", "pw") == "echo pw | {\na && b\n}"
    transport = (KOTLIN / "UsbAdbTransport.kt").read_text(encoding="utf-8")
    shell_fn = transport[transport.index("fun runAdbShellCommand("):]
    assert shell_fn.split("\n\n")[0].rstrip().endswith(
        'ShellVerifyPassword.run(command, log) { runAdbService(transport, "shell:$it", log, timeoutMs) }')
    session = (KOTLIN / "AdbSession.kt").read_text(encoding="utf-8")
    assert session.count("ShellVerifyPassword.onConnected(result.bannerFromDevice)") == 2  # USB и Wi-Fi
    assert 'banner.contains("changan", ignoreCase = true)' in code
