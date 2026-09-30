"""Удаление dex-хелпером, когда прошивка не пускает «pm uninstall» («error: closed» / CLSE — VOLGA/Geely N155,
Geely OneOS/Monji; откат в сток упирался в «удалите штатно», логи №797, №962, №1664, №1746). Хелпер наш
(helpers/uninstall_helper → cars/_shared/uninstall_helper.dex); на эмуляторе Android 9 проверен вживую — установленное
удаляет («Success»), штатное нет («Failure [DELETE_FAILED_INTERNAL_ERROR]»). Здесь — логика вокруг него."""
from __future__ import annotations
import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace

from app import rollback as rollback_module
from app.install_context import InstallContext
from app.uninstall_helper import REMOTE_HELPER, uninstall_via_helper

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "cars/_shared/uninstall_helper.dex"


def _fake_device(answer):
    calls = []
    push = lambda local, remote: calls.append(("push", local, remote))

    def shell(command):
        calls.append(("shell", command))
        return answer if "app_process" in command else ""
    return calls, push, shell


def test_helper_success_and_cleanup():
    calls, push, shell = _fake_device("Success\n")
    assert uninstall_via_helper(push, shell, HELPER, "ru.kinopoisk") == (True, "")
    assert calls[0] == ("push", str(HELPER), REMOTE_HELPER)
    assert ("shell", f"CLASSPATH={REMOTE_HELPER} app_process /data/local/tmp MagicSqdUninstaller ru.kinopoisk") in calls
    assert calls[-1] == ("shell", f"rm -f {REMOTE_HELPER}")


def test_helper_failure_text_and_guards(tmp_path):
    _, push, shell = _fake_device("Failure [DELETE_FAILED_INTERNAL_ERROR]")
    assert uninstall_via_helper(push, shell, HELPER, "com.android.gallery3d") == (
        False, "Failure [DELETE_FAILED_INTERNAL_ERROR]")
    calls, push, shell = _fake_device("Success")
    assert uninstall_via_helper(push, shell, HELPER, "a; rm -rf /")[0] is False and calls == []  # не в shell
    assert uninstall_via_helper(push, shell, tmp_path / "нет.dex", "ru.kinopoisk")[0] is False and calls == []


class FakeAdb:
    """pm uninstall отбит прошивкой (как N155), app_process — отвечает answer."""

    def __init__(self, answer):
        self.answer, self.commands = answer, []

    def shell(self, command, check=True, timeout=120):
        self.commands.append(command)
        if command.startswith("pm uninstall"):
            return SimpleNamespace(stdout="", stderr="error: closed")
        return SimpleNamespace(stdout=self.answer if "app_process" in command else "", stderr="")

    def push(self, local, remote, timeout=180):
        self.commands.append(f"push {remote}")


def test_rollback_removes_via_helper_when_pm_is_closed():
    log = []
    adb = FakeAdb("Success")
    outcome = rollback_module.rollback(adb, [{"package": "ru.kinopoisk", "name": "Кинопоиск", "path": "/a.apk"}],
                                       log.append, helper=HELPER)
    assert outcome["removed"] == ["ru.kinopoisk"] and outcome["manual"] == []
    assert "Магнитола не пускает pm uninstall — удаляю через dex-хелпер..." in log and "Удалено: Кинопоиск" in log


def test_rollback_falls_back_to_manual_when_helper_fails_or_is_absent():
    log = []
    outcome = rollback_module.rollback(FakeAdb("Failure [DELETE_FAILED_INTERNAL_ERROR]"),
                                       [{"package": "ru.kinopoisk", "name": "Кинопоиск"}], log.append, helper=HELPER)
    assert outcome["manual"] == ["ru.kinopoisk"]
    assert "dex-хелпер не удалил: Failure [DELETE_FAILED_INTERNAL_ERROR]" in log
    assert log[-1] == f"Не удалось удалить Кинопоиск: {rollback_module.MANUAL_REMOVAL}"
    adb = FakeAdb("Success")
    assert rollback_module.rollback(adb, [{"package": "ru.kinopoisk"}], log.append)["manual"] == ["ru.kinopoisk"]
    assert not any("app_process" in c for c in adb.commands)  # без хелпера — как раньше


def _adb_permissions():
    spec = importlib.util.spec_from_file_location("adb_permissions_helper_test", ROOT / "cars/_shared/adb_permissions.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_uninstall_button_uses_helper_in_new_versions_only():
    adb_permissions = _adb_permissions()
    closed = lambda command, **kw: SimpleNamespace(stdout="", stderr="error: closed")
    log = []
    new = SimpleNamespace(log=log.append, shell=closed, uninstall_via_helper=lambda package: True)
    adb_permissions.uninstall_app(new, "ru.kinopoisk")
    assert log[-1] == "Готово."
    log.clear()
    old = SimpleNamespace(log=log.append, shell=closed)  # версия программы до 1.0.51 — метода нет
    adb_permissions.uninstall_app(old, "ru.kinopoisk")
    assert log[-1].startswith("Не удалось удалить: эта магнитола не даёт удалять приложения через программу")


def test_install_context_runs_the_helper_from_shared_dir():
    log, commands = [], []
    fake = SimpleNamespace(
        shared_dir=HELPER.parent, log=log.append,
        push=lambda local, remote: commands.append(("push", local)),
        shell=lambda command, **kw: SimpleNamespace(stdout="Success" if "app_process" in command else "", stderr=""))
    assert InstallContext.uninstall_via_helper(fake, "ru.kinopoisk") is True
    assert commands == [("push", str(HELPER))]


def test_helper_dex_is_ours_and_built_from_source():
    data = HELPER.read_bytes()
    assert data[:4] == b"dex\n" and b"MagicSqdUninstaller" in data and b"Usage: MagicSqdUninstaller <package>" in data
    source = (ROOT / "helpers/uninstall_helper/src/MagicSqdUninstaller.java").read_text(encoding="utf-8")
    assert "installer.uninstall(args[0], receiver.intentSender())" in source
    assert (ROOT / "helpers/uninstall_helper/build.sh").is_file()


def test_android_uses_the_same_helper():
    kotlin = ROOT / "android/app/src/main/java/ru/magicsqd/mobile"
    permissions = (kotlin / "usb/AdbPermissions.kt").read_text(encoding="utf-8")
    assert "is AdbShellResult.Rejected -> if (helper != null) removeViaHelper(pkg, helper, log) else Removal.Blocked" in permissions
    assert 'app_process /data/local/tmp MagicSqdUninstaller $pkg' in permissions
    assert "removePackage(pkg, log, uninstallHelper(context))" in permissions
    bridge = (kotlin / "WebBridge.kt").read_text(encoding="utf-8")
    assert re.search(r"AdbPermissions\.removePackage\(pkg, ::pushAdbLog, AdbPermissions\.uninstallHelper\(context\)\)", bridge)
