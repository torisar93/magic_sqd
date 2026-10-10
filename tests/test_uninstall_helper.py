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
    # Как на Geely OneOS/Monji: список приложений pm отдаёт, а pm uninstall закрыт прошивкой.
    lists = {"pm list packages -s": "package:android\n", "pm list packages -3": "package:ru.kinopoisk\n"}
    closed = lambda command, **kw: SimpleNamespace(stdout=lists.get(command, ""),
                                                   stderr="" if command in lists else "error: closed")
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
    # Прежний uninstall_helper.dex (класс MagicSqdUninstaller) остаётся для встроенного кода версий до 1.1.1; исходник —
    # тот же код под новым именем (MagicSqdPkgHelper → cars/_shared/msqd_pkg_helper.dex, его зовёт общий код каталога).
    data = HELPER.read_bytes()
    assert data[:4] == b"dex\n" and b"MagicSqdUninstaller" in data and b"Usage: MagicSqdUninstaller <package>" in data
    new = (ROOT / "cars/_shared/msqd_pkg_helper.dex").read_bytes()
    assert new[:4] == b"dex\n" and b"Usage: MagicSqdPkgHelper <package>" in new and b"MagicSqdUninstaller" not in new
    source = (ROOT / "helpers/uninstall_helper/src/MagicSqdPkgHelper.java").read_text(encoding="utf-8")
    assert "installer.uninstall(args[0], receiver.intentSender())" in source
    assert "OUT=../../cars/_shared/msqd_pkg_helper.dex" in (ROOT / "helpers/uninstall_helper/build.sh").read_text(encoding="utf-8")


def test_android_uses_the_same_helper():
    kotlin = ROOT / "android/app/src/main/java/ru/magicsqd/mobile"
    permissions = (kotlin / "usb/AdbPermissions.kt").read_text(encoding="utf-8")
    assert "is AdbShellResult.Rejected -> if (helper != null) removeViaHelper(pkg, helper, log) else Removal.Blocked" in permissions
    assert 'app_process /data/local/tmp MagicSqdUninstaller $pkg' in permissions
    assert "removePackage(pkg, log, uninstallHelper(context))" in permissions
    bridge = (kotlin / "WebBridge.kt").read_text(encoding="utf-8")
    assert re.search(r"AdbPermissions\.removePackage\(pkg, ::pushAdbLog, AdbPermissions\.uninstallHelper\(context\)\)", bridge)


# Свой хелпер каталога (cars/_shared/msqd_pkg_helper.dex): у прежнего в имени файла и класса было слово «uninstall», и
# прошивки, закрывшие pm uninstall, отклоняли с ним любую команду — 21 попытка, ни одной удачной (лог №4649).

def _closed_pm_ctx(tmp_path, helper_answer="Success", push_code=0, with_dex=True, old_helper=None):
    shared = tmp_path / "_shared"
    shared.mkdir()
    if with_dex:
        (shared / "msqd_pkg_helper.dex").write_bytes(b"dex")
    lists = {"pm list packages -s": "package:android\n", "pm list packages -3": "package:ru.kinopoisk\n"}
    log, commands = [], []

    def shell(command, check=True, timeout=120):
        commands.append(command)
        if command in lists:
            return SimpleNamespace(stdout=lists[command], stderr="", returncode=0)
        if command.startswith("pm uninstall"):
            return SimpleNamespace(stdout="", stderr="error: closed", returncode=1)
        if "app_process" in command:
            return SimpleNamespace(stdout=helper_answer, stderr="", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    def adb(*args, check=True, timeout=120):
        commands.append("adb " + " ".join(str(a) for a in args))
        return SimpleNamespace(stdout="", stderr="" if push_code == 0 else "remote couldn't create file",
                               returncode=push_code)

    ctx = SimpleNamespace(log=log.append, shell=shell, adb=adb, shared_dir=shared, sleep=lambda s: None)
    if old_helper is not None:
        ctx.uninstall_via_helper = old_helper
    return ctx, log, commands


def test_own_helper_removes_without_the_blocked_word(tmp_path):
    adb_permissions = _adb_permissions()
    ctx, log, commands = _closed_pm_ctx(tmp_path)
    adb_permissions.uninstall_app(ctx, "ru.kinopoisk")
    assert log[-2:] == ["Магнитола не пускает pm uninstall — удаляю через dex-хелпер...", "Готово."]
    assert f"adb push {tmp_path / '_shared' / 'msqd_pkg_helper.dex'} /data/local/tmp/msqd_pkg_helper.dex" in commands
    assert ("CLASSPATH=/data/local/tmp/msqd_pkg_helper.dex app_process /data/local/tmp MagicSqdPkgHelper ru.kinopoisk"
            in commands)
    assert "rm -f /data/local/tmp/msqd_pkg_helper.dex" in commands
    assert [c for c in commands if "uninstall" in c.lower()] == ["pm uninstall ru.kinopoisk"]


def test_own_helper_failure_is_named(tmp_path):
    adb_permissions = _adb_permissions()
    ctx, log, _ = _closed_pm_ctx(tmp_path, helper_answer="Failure [DELETE_FAILED_INTERNAL_ERROR]")
    adb_permissions.uninstall_app(ctx, "ru.kinopoisk")
    assert log[-2] == "dex-хелпер не удалил: Failure [DELETE_FAILED_INTERNAL_ERROR]"
    assert log[-1].startswith("Не удалось удалить: эта магнитола не даёт удалять приложения через программу")


def test_own_helper_not_pushed_says_why(tmp_path):
    adb_permissions = _adb_permissions()
    ctx, log, commands = _closed_pm_ctx(tmp_path, push_code=1)
    adb_permissions.uninstall_app(ctx, "ru.kinopoisk")
    assert log[-2] == "dex-хелпер не удалил: не записался на магнитолу (remote couldn't create file)"
    assert not any("app_process" in c for c in commands)


def test_without_own_helper_the_program_helper_is_tried(tmp_path):
    adb_permissions = _adb_permissions()
    tried = []
    ctx, log, _ = _closed_pm_ctx(tmp_path, with_dex=False, old_helper=lambda package: tried.append(package) or True)
    adb_permissions.uninstall_app(ctx, "ru.kinopoisk")
    assert tried == ["ru.kinopoisk"] and log[-1] == "Готово."


def test_helper_source_and_dex_names_avoid_the_blocked_word():
    import re as _re
    helper = ROOT / "helpers/uninstall_helper/src/MagicSqdPkgHelper.java"
    assert _re.search(r"public final class MagicSqdPkgHelper\b", helper.read_text(encoding="utf-8"))
    assert (ROOT / "cars/_shared/msqd_pkg_helper.dex").is_file()
    for name in ("msqd_pkg_helper.dex", "MagicSqdPkgHelper"):
        assert "uninstall" not in name.lower()

