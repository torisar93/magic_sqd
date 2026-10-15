"""Changan (прошивка changan_car — UNI-K, UNI-S, CS35 Plus New, CS75 Plus S311_G1 и родня): пароль подтверждения
(«please input verify password:») спрашивает сам сеанс adb shell — до любой команды, даже chmod и getprop (логи №3524,
№5981), — и читает его из stdin adb; на неверный или пустой ввод — «verify failed! verify password:» и ожидание до
таймаута, команда не запускается. До 15.10.2026 программа подавала пароль внутри команды («echo пароль | { … }») — он
не доходил ни разу (ни одной установки на таких магнитолах; ПК висел по 30 с на каждой команде). Разбор ChanganInstaller
(ChanganTweaker, владелец 15.10: «бесплатный софт ставит отлично»): пароль adb36987 — в stdin adb каждой команды,
«please input verify password: verify success!» вырезается из вывода. Так теперь и программа (app/adb_utils.py; Android —
usb/ShellVerifyPassword.kt).

Остаётся и магнитола, где пароль спрашивают только pm/cmd (тоже из stdin): ей пароль подаётся так же, в stdin adb.
adb install (файл идёт в stdin установщика — магнитола приняла бы его байты за пароль): файл в /data/local/tmp и
pm install с паролем (владелец, 11.10.2026)."""
from __future__ import annotations
import re
import subprocess
from pathlib import Path

import pytest

from app import adb_utils
from app.adb_utils import Adb, AdbError, AdbTimeoutError

KOTLIN = Path(__file__).resolve().parents[1] / "android/app/src/main/java/ru/magicsqd/mobile/usb"
NO_STDIN = re.compile(r"^\{\n(.*)\n\} < /dev/null$", re.S)
PROBE = "echo msqd_verify_probe"
STREAMED_ASK = ("Performing Streamed Install\nadb: failed to install C:\\apk\\File Manager+ v3.2.8.apk: "
                "please input verify password: verify failed! verify password:verify failed! verify password:\n")


@pytest.fixture(autouse=True)
def fresh_head_units(monkeypatch):
    """Каждый тест — с магнитолами, которые программа ещё не проверяла (conftest считает их обычными)."""
    monkeypatch.setattr(adb_utils, "_SESSION_PASSWORDS", {})


class HeadUnit:
    """adb магнитолы. password=None — обычная. level="session" — Changan: пароль спрашивает сеанс adb shell до любой
    команды, из stdin adb (input); неверный или пустой ввод — запрос снова и ожидание до таймаута. level="pm" — пароль
    спрашивают только pm/cmd (тоже из stdin): со stdin из /dev/null отвечают «verify failed» сразу; hangs=True — и без
    него ждут (UNI-V, LAMORE). adb install шлёт файл в stdin установщика — его байты магнитола принимает за пароль."""

    def __init__(self, password: str | None, level: str = "session", hangs: bool = False, pm_answer: str = "Success"):
        self.password = password
        self.level = level
        self.hangs = hangs
        self.pm_answer = pm_answer
        self.commands: list = []
        self.inputs: list = []
        self.files: dict[str, str] = {}

    def run(self, *args, check=True, timeout=120, input=None):
        self.inputs.append(input)
        if args[0] == "push":
            self.commands.append(("push", args[1], args[2]))
            self.files[args[2]] = args[1]
            return subprocess.CompletedProcess(args, 0, "1 file pushed\n", "")
        if args[0] in ("install", "uninstall"):
            self.commands.append(tuple(args))
            if self.password is None:
                return subprocess.CompletedProcess(args, 0, "Success\n", "")
            return subprocess.CompletedProcess(args, 1, STREAMED_ASK, "")
        assert args[0] == "shell"
        command = args[1]
        self.commands.append(command)
        devnull = NO_STDIN.match(command)
        real = devnull.group(1) if devnull else command
        asks = self.password is not None and (self.level == "session" or adb_utils._PACKAGE_COMMAND.search(real))
        prefix = ""
        if asks:
            line = None if input is None else input.split("\n", 1)[0]
            if line != self.password:
                if self.level == "pm" and devnull and not self.hangs:
                    return subprocess.CompletedProcess(args, 1, "please input verify password:verify failed!\n", "")
                said = "please input verify password:" + (" verify failed! verify password:" if line else "")
                raise AdbTimeoutError(f"Команда не ответила за {timeout} сек: adb shell {command}", said)
            if self.level == "pm" and "pm install -S" in real:  # stdin pm занят файлом — пароль туда не подать
                return subprocess.CompletedProcess(args, 1, "please input verify password:verify failed!\n", "")
            prefix = "please input verify password: verify success!\n"
        return self._execute(args, real, prefix)

    def _execute(self, args, real, prefix):
        if real == PROBE:
            out = "msqd_verify_probe\n"
        elif real.startswith("rm -f "):
            self.files.pop(real[len("rm -f "):], None)
            out = ""
        elif real.startswith("getprop sys.boot_completed"):
            out = "1\n"
        elif real.startswith("pm install"):
            return subprocess.CompletedProcess(args, 0 if self.pm_answer == "Success" else 1,
                                               prefix + self.pm_answer + "\n", "")
        elif real.startswith(("pm path ", "pm uninstall ")):
            out = "Success\n" if "uninstall" in real else "package:/data/app/x/base.apk\n"
        else:
            out = f"out:{real}\n"
        return subprocess.CompletedProcess(args, 0, prefix + out, "")

    def shells(self):
        return [c for c in self.commands if isinstance(c, str)]


def _adb(device: HeadUnit, logs: list, serial: str = "SER") -> Adb:
    adb = Adb("adb", serial, log=logs.append)
    adb.run = device.run
    return adb


# --- обычная магнитола ---

def test_ordinary_head_unit_checked_once_then_commands_as_before():
    device, logs = HeadUnit(None), []
    adb = _adb(device, logs)
    assert adb.shell("pm list packages").stdout == "out:pm list packages\n"
    assert adb.shell("getprop ro.product.name").stdout == "out:getprop ro.product.name\n"
    # проверка одна (строка пароля в stdin echo не мешает), дальше — как раньше: pm/cmd со stdin из /dev/null
    assert device.commands == [PROBE, "{\npm list packages\n} < /dev/null", "getprop ro.product.name"]
    assert device.inputs == ["adb36987\n", None, None] and logs == []
    other = _adb(device, logs)  # новый Adb (этап, вызов) той же магнитолы — без повторной проверки
    other.shell("getprop ro.serialno")
    assert device.commands[-1] == "getprop ro.serialno" and device.commands.count(PROBE) == 1


def test_ordinary_head_unit_installs_with_plain_adb_install():
    device, logs = HeadUnit(None), []
    adb = _adb(device, logs)
    adb.install("C:/apk/a.apk")
    assert device.commands == [PROBE, ("install", "-r", "C:/apk/a.apk")] and logs == []


# --- Changan: пароль спрашивает сеанс adb shell ---

def test_session_password_goes_to_stdin_of_every_command():
    device, logs = HeadUnit("adb36987"), []
    adb = _adb(device, logs)
    result = adb.shell("chmod 644 /data/local/tmp/a.apk")  # №3524, №5981: висел даже chmod
    assert result.returncode == 0 and result.stdout == "out:chmod 644 /data/local/tmp/a.apk\n"  # ответ пароля вырезан
    assert adb.shell("getprop ro.product.name").stdout == "out:getprop ro.product.name\n"
    # пароль — в stdin adb, а не внутри команды; пароль ChanganInstaller — первым, найден с первой попытки
    assert device.commands == [PROBE, "chmod 644 /data/local/tmp/a.apk", "getprop ro.product.name"]
    assert device.inputs == ["adb36987\n"] * 3
    assert any("на каждую команду" in m for m in logs) and any("подошёл" in m for m in logs)
    other = _adb(device, [])  # следующий этап — сразу с паролем
    assert other.shell("getprop ro.serialno").stdout == "out:getprop ro.serialno\n"
    assert device.commands.count(PROBE) == 1


def test_second_known_password_is_tried_after_the_first_hangs():
    device, logs = HeadUnit("adb369875"), []
    adb = _adb(device, logs)
    assert adb.shell("pm path ru.vk.store").stdout == "package:/data/app/x/base.apk\n"
    assert device.commands == [PROBE, PROBE, "pm path ru.vk.store"]
    assert device.inputs == ["adb36987\n", "adb369875\n", "adb369875\n"]


def test_no_known_password_fails_fast_with_reason():
    device, logs = HeadUnit("something-else"), []
    adb = _adb(device, logs)
    result = adb.shell("pm path ru.vk.store", check=False)
    assert result.returncode == 1 and "не подошли" in result.stderr
    with pytest.raises(AdbError, match="нужен пароль от этой прошивки"):
        adb.shell("getprop ro.product.name")  # без повторных проверок и ожиданий
    assert device.commands == [PROBE, PROBE] and sum("не подошли" in m for m in logs) == 1


def test_install_on_session_password_unit_goes_through_local_tmp():
    """Случай владельца 15.10 (UNI-K, CS75 Plus): adb install не пробуем вовсе — push + pm install с паролем."""
    device, logs = HeadUnit("adb36987"), []
    adb = _adb(device, logs)
    result = adb.install("C:/apk/File Manager+ v3.2.8.apk")
    assert result.stdout == "Success\n"
    probe, push, pm, rm = device.commands
    assert probe == PROBE and push[:2] == ("push", "C:/apk/File Manager+ v3.2.8.apk")
    remote = push[2]
    assert re.fullmatch(r"/data/local/tmp/msqd_install_\d+\.apk", remote)  # без пробелов и кириллицы
    assert pm == f"pm install -r {remote}" and rm == f"rm -f {remote}" and device.files == {}
    assert device.inputs == ["adb36987\n", None, "adb36987\n", "adb36987\n"]


def test_install_failure_shows_installer_answer_and_cleans_up():
    device, logs = HeadUnit("adb36987", pm_answer="Failure [INSTALL_FAILED_VERSION_DOWNGRADE]"), []
    adb = _adb(device, logs)
    with pytest.raises(AdbError, match="INSTALL_FAILED_VERSION_DOWNGRADE"):
        adb.install("C:/apk/a.apk")
    assert device.commands[-1].startswith("rm -f /data/local/tmp/") and device.files == {}


def test_install_with_unknown_password_fails_with_reason():
    device, logs = HeadUnit("something-else"), []
    adb = _adb(device, logs)
    with pytest.raises(AdbError, match="не подошли"):
        adb.install("C:/apk/a.apk")
    assert not any(isinstance(c, tuple) for c in device.commands)  # файл не гоняли впустую


def test_uninstall_on_session_password_unit_goes_through_pm():
    device, logs = HeadUnit("adb36987"), []
    adb = _adb(device, logs)
    assert adb.uninstall("ru.vk.store").stdout == "Success\n"
    assert device.commands[-1] == "pm uninstall ru.vk.store" and device.inputs[-1] == "adb36987\n"


def test_boot_wait_goes_through_password(monkeypatch):
    device, logs = HeadUnit("adb36987"), []
    adb = _adb(device, logs)
    monkeypatch.setattr(adb_utils.time, "sleep", lambda s: None)
    adb.wait_boot_completed(timeout=5)
    assert adb.verify_password == "adb36987" and device.inputs[-1] == "adb36987\n"


def test_wifi_reconnect_checks_the_head_unit_again():
    device, logs = HeadUnit(None), []
    _adb(device, logs, "192.168.43.1:5555").shell("getprop ro.product.name")
    adb_utils.forget_session_password("192.168.43.1:5555")  # adb connect — по адресу может быть другая магнитола
    changan = HeadUnit("adb36987")
    assert _adb(changan, logs, "192.168.43.1:5555").shell("getprop ro.product.name").returncode == 0
    assert changan.commands[0] == PROBE and changan.inputs[1] == "adb36987\n"


# --- магнитола, где пароль спрашивают только pm/cmd ---

def test_package_level_password_also_goes_to_stdin():
    device, logs = HeadUnit("adb369875", level="pm"), []
    adb = _adb(device, logs)
    result = adb.shell("pm path com.example")
    assert result.returncode == 0 and adb.verify_password == "adb369875"
    # проверка сеанса: пароля нет; pm как есть — запрос; пароли по очереди в stdin
    assert device.commands == [PROBE, "{\npm path com.example\n} < /dev/null", "pm path com.example",
                               "pm path com.example"]
    assert device.inputs == ["adb36987\n", None, "adb36987\n", "adb369875\n"]
    device.commands.clear()
    adb.shell("pm grant com.example android.permission.CAMERA")
    assert device.commands == ["pm grant com.example android.permission.CAMERA"]
    assert device.inputs[-1] == "adb369875\n"


def test_package_level_stream_install_cannot_take_password_but_password_is_kept():
    device, logs = HeadUnit("adb369875", level="pm"), []
    adb = _adb(device, logs)
    adb.shell("pm path com.example")
    result = adb.shell("cat /data/local/tmp/a.apk | pm install -S 5", check=False)
    assert result.returncode == 1 and "verify failed" in result.stdout
    assert adb.verify_password == "adb369875"


def test_package_level_prompt_seen_before_timeout_still_gets_password():
    device, logs = HeadUnit("adb36987", level="pm", hangs=True), []
    adb = _adb(device, logs)
    result = adb.shell("pm path com.example")  # «< /dev/null» не помог — запрос виден до таймаута
    assert result.returncode == 0 and adb.verify_password == "adb36987"


# --- adb ---

def test_timeout_without_prompt_is_still_an_error():
    adb = Adb("adb", "SER")

    def slow(*args, check=True, timeout=120, input=None):
        raise AdbTimeoutError("Команда не ответила за 30 сек", "")

    adb.run = slow
    with pytest.raises(AdbTimeoutError):
        adb.shell("pm path com.example")
    assert "SER" not in adb_utils._SESSION_PASSWORDS  # магнитола не ответила — решать о пароле рано


def test_run_timeout_keeps_what_command_printed(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 30, output=b"please input verify password:", stderr=None)

    monkeypatch.setattr(adb_utils.subprocess, "run", fake_run)
    with pytest.raises(AdbTimeoutError) as caught:
        Adb("adb", "SER").run("shell", "pm path x", timeout=30)
    assert caught.value.output == "please input verify password:" and "не ответила за 30 сек" in str(caught.value)


def test_run_gives_adb_the_password_as_stdin(monkeypatch):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(adb_utils.subprocess, "run", fake_run)
    Adb("adb", "SER").run("shell", "getprop", input="adb36987\n")
    Adb("adb", "SER").run("shell", "getprop")
    assert seen[0]["input"] == "adb36987\n" and "stdin" not in seen[0]
    assert seen[1]["stdin"] is subprocess.DEVNULL and "input" not in seen[1]


def test_success_answer_is_cut_from_output():
    text = "please input verify password: verify success!\nSuccess\n"
    assert adb_utils.strip_verify_prompt(text) == "Success\n"
    assert adb_utils.strip_verify_prompt("please input verify password:package:a") == "package:a"


def test_android_copy_matches_desktop():
    code = (KOTLIN / "ShellVerifyPassword.kt").read_text(encoding="utf-8")
    passwords = re.search(r"PASSWORDS = listOf\(([^)]*)\)", code).group(1)
    assert tuple(re.findall(r'"([^"]+)"', passwords)) == adb_utils.VERIFY_PASSWORDS
    assert "echo $password |" not in code  # пароль — в поток команды, не внутрь неё
    transport = (KOTLIN / "UsbAdbTransport.kt").read_text(encoding="utf-8")
    assert "stdin: ByteArray? = null" in transport


@pytest.mark.parametrize("command,package", [
    ("pm list packages", True), ("cmd package list packages", True), ("/system/bin/pm path a.b", True),
    ("settings put global x 1 && pm grant a.b c.d", True), ("getprop ro.product.name", False),
    ("rm -f /data/local/tmp/x_pm.apk", False), ("ls /sdcard/pm.txt", False), ("npm -v", False),
    ("echo cmdline", False),
])
def test_package_commands_are_recognised(command, package):
    assert bool(adb_utils._PACKAGE_COMMAND.search(command)) is package
