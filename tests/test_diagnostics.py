"""Кнопка «Собрать подробный лог» (cars/_shared/diagnostics.py, «#py diagnostics.collect»; владелец, 18.10.2026: «может
мы можем давать в установщике кнопку которая бы снимала дополнительные более подробные логи и отправляла нам?»):
только чтение, разделы с ограничением строк, без идентификаторов устройства, один код для ПК и Android."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cars/_shared"))
diagnostics = importlib.import_module("diagnostics")

from tests.test_py_runner import FakeBridge, android, put  # noqa: E402,F401 — фикстура android

GETPROP = "\n".join([
    "[ro.product.model]: [SA8155 Digital Cockpit]",
    "[ro.product.name]: [lajolla]",
    "[ro.build.fingerprint]: [Qualcomm/lajolla/lajolla:11/RKQ1/eng.20240101:userdebug/test-keys]",
    "[ro.build.version.sdk]: [30]",
    "[ro.serialno]: [8b46ff40]",
    "[ro.boot.serialno]: [8b46ff40]",
    "[ro.product.serial]: [X123]",
    "[persist.vendor.vin]: [LVTDB21B1PD000001]",
    "[ro.boot.wifimacaddr]: [00:11:22:33:44:55]",
    "[ro.debuggable]: [1]",
    "[dalvik.vm.heapsize]: [512m]",
])
LOGCAT = "\n".join(f"10-18 16:3{i % 10}:00.000 W/PackageManager( 812): line {i}" for i in range(250))


class Ctx:
    """Контекст как у ПК (InstallContext): shell отвечает по началу команды; Exception — сбой связи."""

    def __init__(self, replies=None, fail=()):
        self.replies = replies or {}
        self.fail = list(fail)
        self.logs = []
        self.commands = []

    def log(self, message):
        self.logs.append(message)

    def shell(self, command, check=True, timeout=120):
        assert check is False
        self.commands.append(command)
        for key, exc in self.fail:
            if command.startswith(key):
                raise exc
        for key, value in self.replies.items():
            if command.startswith(key):
                return SimpleNamespace(stdout=value, stderr="", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=0)


def section(ctx, title):
    return next(message for message in ctx.logs if message.startswith(f"[{title}]"))


def test_reads_only_and_logs_every_section():
    ctx = Ctx({"getprop": GETPROP, "logcat": LOGCAT, "pm list users": "Users:\n\tUserInfo{0:Иван Петров:c13} running"})
    total = diagnostics.collect(ctx)
    assert ctx.logs[0].startswith(diagnostics.HEADER) and "ничего не меняется" in ctx.logs[0]
    assert ctx.logs[-1].startswith(f"=== Подробный лог собран: {total} строк.")
    sections = ctx.commands[:len(diagnostics.SECTIONS)]
    writes = ("install ", "uninstall", " rm ", "settings put", "pm grant", "disable", "reboot", "am start")
    assert not any(word in command for command in sections for word in writes)
    for title, _, _ in diagnostics.SECTIONS:
        section(ctx, title)


def test_no_device_identifiers_and_no_user_names():
    ctx = Ctx({"getprop": GETPROP, "pm list users": "Users:\n\tUserInfo{0:Иван Петров:c13} running"})
    diagnostics.collect(ctx)
    props = section(ctx, "Прошивка (getprop)")
    assert "ro.product.model]: [SA8155 Digital Cockpit]" in props and "ro.build.fingerprint" in props
    assert "ro.debuggable" in props and "dalvik.vm.heapsize" not in props
    text = "\n".join(ctx.logs)
    for secret in ("8b46ff40", "X123", "LVTDB21B1PD000001", "00:11:22:33:44:55", "Иван Петров"):
        assert secret not in text
    assert "UserInfo{0:…:c13}" in section(ctx, "Пользователи и ограничения")


def test_install_log_keeps_the_latest_lines_and_says_how_many_were_cut():
    ctx = Ctx({"logcat": LOGCAT})
    diagnostics.collect(ctx)
    journal = section(ctx, "Системный журнал про установку")
    assert "(ещё 50 строк не показано)" in journal
    assert journal.rstrip().endswith("line 249") and "line 49\n" not in journal


def test_long_lines_are_cut():
    ctx = Ctx({"date": "x" * 1000})
    diagnostics.collect(ctx)
    line = section(ctx, "Время на магнитоле").splitlines()[1]
    assert len(line) == 2 + diagnostics.LINE_CHARS + 1 and line.endswith("…")


def test_one_failed_section_is_skipped_two_in_a_row_stop():
    ctx = Ctx(fail=[("date", ConnectionError("timeout"))])
    diagnostics.collect(ctx)
    assert "[Время на магнитоле] не получилось: timeout" in ctx.logs
    assert ctx.logs[-1].startswith("=== Подробный лог собран")

    lost = Ctx(fail=[("date", ConnectionError("связь потеряна")), ("df", ConnectionError("связь потеряна"))])
    with pytest.raises(RuntimeError, match="Связь с магнитолой потеряна"):
        diagnostics.collect(lost)


def test_stop_button_is_not_swallowed():
    class InstallCancelled(RuntimeError):
        pass

    ctx = Ctx(fail=[("date", InstallCancelled("Остановлено пользователем."))])
    with pytest.raises(InstallCancelled):
        diagnostics.collect(ctx)


def test_same_code_on_android(android, tmp_path):
    shared = tmp_path / "_shared"
    put(shared, "diagnostics.py", (ROOT / "cars/_shared/diagnostics.py").read_text(encoding="utf-8"))
    bridge = FakeBridge({"getprop": (GETPROP, 0), "logcat": (LOGCAT, 0)})
    result = android.call(bridge, shared, "diagnostics", "collect")
    assert result["ok"] is True and result["result"] > 0
    assert bridge.logs[0].startswith(diagnostics.HEADER) and bridge.logs[-1].startswith("=== Подробный лог собран")
    assert "8b46ff40" not in "\n".join(bridge.logs)


def test_accessibility_services_one_per_line():
    """Службы спецвозможностей — по одной на строку: на Cityray их 12, одной строкой список обрезался на 300 символах
    и Back Button в него не попадал (владелец, 02.11.2026)."""
    services = ":".join(f"com.vendor{i}.app/com.vendor{i}.app.SomeAccessibilityService" for i in range(12))
    bound = ", ".join("{Service[label=App %d, feedbackType[FEEDBACK_GENERIC], capabilities=1]}" % i for i in range(3))
    reply = (f"accessibility_enabled=1\nenabled_accessibility_services={services}:nu.back.button/"
             f"nu.back.button.service.BackButtonService\n     Bound services:{bound}\n"
             "nu.back.button: SYSTEM_ALERT_WINDOW: allow; WRITE_SETTINGS: allow")
    ctx = Ctx({"echo \"accessibility_enabled": reply})
    diagnostics.collect(ctx)
    lines = section(ctx, diagnostics.A11Y_TITLE).splitlines()[1:]
    assert lines[1] == "  enabled_accessibility_services=com.vendor0.app/com.vendor0.app.SomeAccessibilityService"
    assert "      nu.back.button/nu.back.button.service.BackButtonService" in lines
    assert all(len(line) <= 2 + diagnostics.LINE_CHARS for line in lines)
    assert sum("{Service[label=App" in line for line in lines) == 3
    assert lines[-1] == "  nu.back.button: SYSTEM_ALERT_WINDOW: allow; WRITE_SETTINGS: allow"


class ProbeCtx(Ctx):
    def __init__(self, replies, shared):
        super().__init__(replies)
        self.shared_dir = shared
        self.pushed = []

    def push(self, local, remote, timeout=None):
        self.pushed.append((Path(local).name, remote))


def test_probe_install_catches_the_firmware_answer(tmp_path):
    """lajolla/Omoda C7 (№14285): к нажатию кнопки журнал про установку уже вытеснили — раздел пустой. Проба ставит
    крошечный installtest.apk и снимает журнал ровно с этого момента (logcat -T)."""
    (tmp_path / "installtest.apk").write_bytes(b"PK")
    ctx = ProbeCtx({"date": "11-09 17:30:00.000\n", "pm install": "Failure [INSTALL_FAILED_INTERNAL_ERROR]",
                    "logcat -d -v time -T": "11-09 17:30:01.000 E/PackageManager( 812): install vetoed\n"
                                            "\tat com.mega.pm.Policy.check(Policy.java:42)"}, tmp_path)
    diagnostics.collect(ctx)
    probe = section(ctx, diagnostics.PROBE_TITLE)
    assert ctx.pushed == [("installtest.apk", diagnostics.PROBE_REMOTE)]
    assert "INSTALL_FAILED_INTERNAL_ERROR" in probe and "install vetoed" in probe and "Policy.java:42" in probe
    assert any(c.startswith("logcat -d -v time -T '11-09 17:30:00.000'") for c in ctx.commands)
    assert f"rm -f {diagnostics.PROBE_REMOTE}" in ctx.commands
    assert not any(c.startswith("pm uninstall") for c in ctx.commands), "не встало — удалять нечего"

    ok = ProbeCtx({"date": "11-09 17:30:00.000", "pm install": "Success"}, tmp_path)
    diagnostics.collect(ok)
    assert f"pm uninstall {diagnostics.PROBE_PACKAGE}" in ok.commands
    assert "тестовое приложение удалено" in section(ok, diagnostics.PROBE_TITLE)


def test_probe_apk_is_published_next_to_the_module():
    """installtest.apk — в каталоге на сервере (cars/_shared, *.apk в git не хранятся); локально — если скачан."""
    if not (ROOT / "cars/_shared" / diagnostics.PROBE_APK).is_file():
        pytest.skip("installtest.apk нет в рабочей копии каталога")
    from app.apk_package import read_package_name
    assert read_package_name(ROOT / "cars/_shared" / diagnostics.PROBE_APK) == diagnostics.PROBE_PACKAGE
