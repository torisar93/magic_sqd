"""Changan (прошивка changan_car — UNI-K, CS35 Plus New и родня): команды shell спрашивают пароль подтверждения со
stdin («please input verify password:»), без него — «verify failed!», и ни одной установки (логи №1073 — ПК, №3524–3529 —
Android). Программа подаёт известный пароль сама (app/adb_utils.py: Adb.shell; Android — usb/ShellVerifyPassword.kt).
Проверено и на эмуляторе с поддельным запросом пароля в cmd package (06.10.2026).

11.10.2026 (владелец: «если устройство просит пароль — вводили; способ adb install должен срабатывать… пуш в локал
темп и потом pm install … < password»): adb install, на котором магнитола спросила пароль, ставит файл из
/data/local/tmp через pm install с паролем; запрос пароля, ждавший ввода до таймаута (UNI-V, LAMORE — №4782, №4829),
больше не ждёт: pm/cmd без пароля получают stdin из /dev/null."""
from __future__ import annotations
import re
import subprocess
from pathlib import Path

import pytest

from app import adb_utils
from app.adb_utils import Adb, AdbError, AdbTimeoutError

KOTLIN = Path(__file__).resolve().parents[1] / "android/app/src/main/java/ru/magicsqd/mobile/usb"
WRAPPED = re.compile(r"^echo (\S+) \| \{\n(.*)\n\}$", re.S)
NO_STDIN = re.compile(r"^\{\n(.*)\n\} < /dev/null$", re.S)
STREAMED_ASK = ("Performing Streamed Install\nadb: failed to install C:\\apk\\File Manager+ v3.2.8.apk: "
                "please input verify password: verify failed!\n")


class FakeChangan:
    """adb магнитолы: correct=None — пароля не спрашивает; иначе команда проходит, только если первой строкой
    stdin пришёл верный пароль. Команде, которой stdin нужен под данные (cat … | pm install -S), пароль не подать.
    adb install пароль не передаёт (stdin занят файлом). hangs=True — запрос ждёт ввода, пока stdin не закрыт
    (UNI-V, LAMORE): без «< /dev/null» команда упирается в таймаут, успев напечатать запрос."""

    def __init__(self, correct: str | None, hangs: bool = False, pm_answer: str = "Success"):
        self.correct = correct
        self.hangs = hangs
        self.pm_answer = pm_answer
        self.commands: list = []
        self.files: dict[str, str] = {}

    def run(self, *args, check=True, timeout=120):
        if args[0] == "push":
            self.commands.append(("push", args[1], args[2]))
            self.files[args[2]] = args[1]
            return subprocess.CompletedProcess(args, 0, "1 file pushed\n", "")
        if args[0] == "install":
            self.commands.append(("install", *args[1:]))
            if self.correct is None:
                return subprocess.CompletedProcess(args, 0, "Performing Streamed Install\nSuccess\n", "")
            return subprocess.CompletedProcess(args, 1, STREAMED_ASK, "")
        assert args[0] == "shell"
        command = args[1]
        self.commands.append(command)
        if command.startswith("rm -f "):
            self.files.pop(command[len("rm -f "):], None)
            return subprocess.CompletedProcess(args, 0, "", "")
        if self.correct is None:
            real = NO_STDIN.match(command).group(1) if NO_STDIN.match(command) else command
            return subprocess.CompletedProcess(args, 0, f"out:{real}\n", "")
        m = WRAPPED.match(command)
        if m is None and self.hangs and not NO_STDIN.match(command):
            raise AdbTimeoutError("Команда не ответила за 30 сек: adb shell " + command, "please input verify password:")
        if m is None or "pm install -S" in m.group(2) or m.group(1) != self.correct:
            return subprocess.CompletedProcess(args, 1, "please input verify password:verify failed!\n", "")
        real = m.group(2)
        if real.startswith("getprop"):
            out = "1\n"
        elif real.startswith("pm install"):
            out = self.pm_answer + "\n"
            return subprocess.CompletedProcess(args, 0 if self.pm_answer == "Success" else 1,
                                               "please input verify password:" + out, "")
        else:
            out = "Success\n"
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
    # pm/cmd — со stdin из /dev/null (запрос пароля не ждёт ввода), остальные команды — как есть
    assert device.commands == ["{\npm list packages\n} < /dev/null"] and adb.verify_password is None and logs == []
    assert adb.shell("getprop ro.product.name").stdout == "out:getprop ro.product.name\n"
    assert device.commands[-1] == "getprop ro.product.name"


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


# --- adb install с паролем: файл в /data/local/tmp + pm install < пароль (11.10.2026) ---

def test_ordinary_device_installs_with_plain_adb_install():
    device, logs = FakeChangan(None), []
    adb = _adb(device, logs)
    adb.install("C:/apk/a.apk")
    assert device.commands == [("install", "-r", "C:/apk/a.apk")] and logs == []


def test_install_that_asks_password_goes_through_local_tmp():
    device, logs = FakeChangan("adb36987"), []
    adb = _adb(device, logs)
    result = adb.install("C:/apk/File Manager+ v3.2.8.apk")
    assert result.stdout == "Success\n"  # приглашение вырезано
    install, push, *pm, rm = device.commands
    assert install == ("install", "-r", "C:/apk/File Manager+ v3.2.8.apk")
    assert push[:2] == ("push", "C:/apk/File Manager+ v3.2.8.apk")
    remote = push[2]
    assert re.fullmatch(r"/data/local/tmp/msqd_install_\d+\.apk", remote)  # без пробелов и кириллицы
    assert [WRAPPED.match(c).groups() for c in pm] == [("adb369875", f"pm install -r {remote}"),
                                                       ("adb36987", f"pm install -r {remote}")]
    assert rm == f"rm -f {remote}" and device.files == {}
    assert adb.verify_password == "adb36987"
    assert any("ставлю с паролем" in m for m in logs) and any("подошёл" in m for m in logs)


def test_next_install_goes_straight_through_local_tmp():
    device, logs = FakeChangan("adb369875"), []
    adb = _adb(device, logs)
    adb.install("C:/apk/a.apk")
    device.commands.clear()
    adb.install("C:/apk/b.apk", extra_args=["-g", "-t"])
    push, pm, rm = device.commands  # без adb install — файл не гоняем впустую
    assert push[0] == "push" and pm == f"echo adb369875 | {{\npm install -r -g -t {push[2]}\n}}"
    assert rm == f"rm -f {push[2]}"


def test_password_seen_in_shell_first_skips_plain_adb_install():
    device, logs = FakeChangan("adb369875"), []
    adb = _adb(device, logs)
    adb.shell("pm path com.example")  # проверка «тот же файл уже стоит» идёт до установки
    device.commands.clear()
    adb.install("C:/apk/a.apk")
    assert [c[0] if isinstance(c, tuple) else c.split()[0] for c in device.commands] == ["push", "echo", "rm"]


def test_password_install_failure_shows_installer_answer():
    device, logs = FakeChangan("adb369875", pm_answer="Failure [INSTALL_FAILED_VERSION_DOWNGRADE]"), []
    adb = _adb(device, logs)
    with pytest.raises(AdbError, match="INSTALL_FAILED_VERSION_DOWNGRADE"):
        adb.install("C:/apk/a.apk")
    assert device.commands[-1].startswith("rm -f /data/local/tmp/") and device.files == {}


def test_no_known_password_install_fails_with_reason():
    device, logs = FakeChangan("something-else"), []
    adb = _adb(device, logs)
    with pytest.raises(AdbError, match="verify failed"):
        adb.install("C:/apk/a.apk")
    assert sum("не подошли" in m for m in logs) == 1


# --- запрос пароля, который ждал ввода до таймаута (UNI-V, LAMORE) ---

def test_package_command_does_not_wait_for_input():
    device, logs = FakeChangan("adb369875", hangs=True), []
    adb = _adb(device, logs)
    result = adb.shell("pm path com.example")  # раньше — 30 с ожидания и отказ без пароля
    assert result.returncode == 0 and adb.verify_password == "adb369875"
    assert device.commands[0] == "{\npm path com.example\n} < /dev/null"


def test_prompt_seen_before_timeout_still_gets_password():
    device, logs = FakeChangan("adb36987", hangs=True), []
    adb = _adb(device, logs)
    result = adb.shell("dumpsys package com.example")  # не pm/cmd — stdin не трогаем, но запрос виден до таймаута
    assert result.returncode == 0 and adb.verify_password == "adb36987"


def test_timeout_without_prompt_is_still_an_error():
    adb = Adb("adb", "SER")

    def slow(*args, check=True, timeout=120):
        raise AdbTimeoutError("Команда не ответила за 30 сек", "")

    adb.run = slow
    with pytest.raises(AdbTimeoutError):
        adb.shell("pm path com.example")


def test_run_timeout_keeps_what_command_printed(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30, output=b"please input verify password:", stderr=None)

    monkeypatch.setattr(adb_utils.subprocess, "run", fake_run)
    with pytest.raises(AdbTimeoutError) as caught:
        Adb("adb", "SER").run("shell", "pm path x", timeout=30)
    assert caught.value.output == "please input verify password:" and "не ответила за 30 сек" in str(caught.value)


@pytest.mark.parametrize("command,package", [
    ("pm list packages", True), ("cmd package list packages", True), ("/system/bin/pm path a.b", True),
    ("settings put global x 1 && pm grant a.b c.d", True), ("getprop ro.product.name", False),
    ("rm -f /data/local/tmp/x_pm.apk", False), ("ls /sdcard/pm.txt", False), ("npm -v", False),
    ("echo cmdline", False),
])
def test_package_commands_are_recognised(command, package):
    assert bool(adb_utils._PACKAGE_COMMAND.search(command)) is package
