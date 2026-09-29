"""localinstall (Chery-хелпер) ставит только новые приложения: поверх установленного PackageManager отвечает
«Attempt to re-install … without first uninstalling» (в logcat, вывод пустой). Когда localinstall на магнитоле уже
сработал, а приложение уже стоит (обновление, штатное системное — лог #1670, VK Video на Haval sa8155), ставим его
dex-хелпером: у него есть флаг замены. Прошивки, где shell не даёт флаг выдачи разрешений (Changan CS75 Plus,
лог #1689), получают второй запуск хелпера без него. Устройство — поддельное, по мотивам проверки на эмуляторе."""
from __future__ import annotations
import re
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import install_context
from app.install_context import (AppInstallFailed, InstallContext, NewerVersionInstalled, _LOCALINSTALL_METHOD)

PKG = "com.vk.vkvideo"
GRANT_DENIED = ("java.lang.SecurityException: You need the android.permission.INSTALL_GRANT_RUNTIME_PERMISSIONS "
                "permission to use the PackageManager.INSTALL_GRANT_RUNTIME_PERMISSIONS flag")


class FakeHeadUnit:
    """pm list packages, Chery LocalInstall и MonjiShellInstaller — как отвечает настоящий Android 9."""

    def __init__(self, installed=(), grant_denied=False, dex_failure=None):
        self.installed = set(installed)
        self.grant_denied = grant_denied
        self.dex_failure = dex_failure
        self.commands = []

    def shell(self, command, check=True, timeout=120):
        self.commands.append(command)
        out = ""
        if command == "pm list packages":
            out = "".join(f"package:{p}\n" for p in sorted(self.installed | {"android"}))
        elif "LocalInstall" in command:
            self.installed.add(PKG)  # поверх установленного — отказ в logcat, в выводе пусто
        elif "MonjiShellInstaller" in command:
            flags = int(re.search(r"--flags 0x([0-9a-f]+)", command).group(1), 16)
            if flags & 0x100 and self.grant_denied:
                out = GRANT_DENIED
            elif self.dex_failure:
                out = f"monji: wrote 8619 bytes\n{self.dex_failure}\n"
            elif PKG in self.installed and not flags & 0x2:
                out = "Failure status=5 message=INSTALL_FAILED_ALREADY_EXISTS: Attempt to re-install\n"
            else:
                self.installed.add(PKG)
                out = f"monji: session=1 flags={hex(flags)}\nmonji: wrote 8619 bytes\nSuccess\n"
        return SimpleNamespace(stdout=out, stderr="", returncode=0)

    def helper_runs(self):
        runs = []
        for command in self.commands:
            if "LocalInstall" in command:
                runs.append("localinstall")
            elif "MonjiShellInstaller" in command:
                runs.append("dex " + command.rsplit("--flags ", 1)[1])
        return runs


@pytest.fixture()
def make_ctx(tmp_path, monkeypatch):
    shared = tmp_path / "app/cars/_shared"
    shared.mkdir(parents=True)
    (shared / "chery_localinstall.apk").write_bytes(b"chery")
    (shared / "dex_shell_helper.dex").write_bytes(b"dex")
    model_dir = tmp_path / "app/cars/Haval/F7/New (F7x New)"
    model_dir.mkdir(parents=True)
    apk = tmp_path / "VK_Video_Rustore_Updated_030726.apk"
    apk.write_bytes(b"apk")
    monkeypatch.setattr(install_context, "read_package_name", lambda path: PKG)

    def make(device: FakeHeadUnit, locked: bool):
        log = []
        ctx = InstallContext(adb_path="fake-adb", device_serial="fake", model_dir=model_dir, selected_apks=[apk],
                             log_fn=log.append, cancel_flag=threading.Event(), shared_dir=shared)
        ctx.shell = device.shell
        ctx.push = lambda local, remote, timeout=180: None
        ctx.sleep = lambda seconds: None
        grants = []
        ctx._grant_all_permissions_if_available = grants.append
        ctx._install_method = _LOCALINSTALL_METHOD if locked else None
        ctx.test = SimpleNamespace(apk=apk, log=log, grants=grants)
        return ctx

    return make


def test_locked_localinstall_updates_installed_app_via_dex_helper(make_ctx):
    device = FakeHeadUnit(installed={PKG})
    ctx = make_ctx(device, locked=True)
    ctx.install_apk_auto(ctx.test.apk)
    assert device.helper_runs() == ["localinstall", "dex 0x116"]
    assert any("уже стоит на магнитоле, а localinstall ставит только новые приложения — обновляю через dex-хелпер"
               in line for line in ctx.test.log), ctx.test.log


def test_changan_firmware_gets_second_run_without_grant_flag(make_ctx):
    device = FakeHeadUnit(installed={PKG}, grant_denied=True)
    ctx = make_ctx(device, locked=True)
    ctx.install_apk_auto(ctx.test.apk)
    assert device.helper_runs() == ["localinstall", "dex 0x116", "dex 0x16"]
    assert any("не даёт хелперу выдавать разрешения" in line for line in ctx.test.log)


def test_dex_helper_method_itself_retries_without_grant_flag(make_ctx):
    # Новое приложение способом dex_shell_install на такой прошивке: раньше сразу отказ, теперь второй запуск.
    device = FakeHeadUnit(grant_denied=True)
    ctx = make_ctx(device, locked=False)
    ctx.install_apk_dex_shell(ctx.test.apk)
    assert PKG in device.installed
    assert ctx.test.grants == [PKG]  # разрешения выдаёт программа — флаг их выдачи хелперу не дали


def test_localinstall_not_yet_confirmed_does_not_fall_back(make_ctx):
    # Пока localinstall на магнитоле не сработал, отказ — обычный: иначе на Geely OneOS (localinstall не работает
    # вовсе) первое уже стоящее приложение «подтвердило» бы localinstall, и новые пропускались бы.
    device = FakeHeadUnit(installed={PKG})
    ctx = make_ctx(device, locked=False)
    with pytest.raises(install_context.AdbError, match="localinstall не подтвердил успех"):
        ctx.install_apk_localinstall(ctx.test.apk)
    assert not any("MonjiShellInstaller" in c for c in device.commands)


def test_new_app_via_localinstall_is_unchanged(make_ctx):
    device = FakeHeadUnit()
    ctx = make_ctx(device, locked=True)
    ctx.install_apk_auto(ctx.test.apk)
    assert ctx.test.grants == [PKG]
    assert not any("MonjiShellInstaller" in c for c in device.commands)


def test_update_refused_as_downgrade_is_the_usual_skip(make_ctx):
    device = FakeHeadUnit(installed={PKG}, dex_failure="Failure [INSTALL_FAILED_VERSION_DOWNGRADE]")
    ctx = make_ctx(device, locked=True)
    with pytest.raises(NewerVersionInstalled):
        ctx.install_apk_auto(ctx.test.apk)


def test_update_that_fails_skips_only_this_app_with_a_clear_reason(make_ctx):
    device = FakeHeadUnit(installed={PKG}, dex_failure="Failure status=1 message=INSTALL_FAILED_ABORTED")
    ctx = make_ctx(device, locked=True)
    with pytest.raises(AppInstallFailed, match="localinstall поверх не ставит, dex-хелпер не обновил"):
        ctx.install_apk_auto(ctx.test.apk)


# Android — то же поведение (Kotlin-тестов в проекте нет, проверяем исходники).
KOTLIN = Path(__file__).resolve().parents[1] / "android/app/src/main/java/ru/magicsqd/mobile"


def _code(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def test_android_dex_helper_retries_without_grant_flag():
    code = _code(KOTLIN / "usb/AdbInstall.kt")
    dex = re.split(r"\n}(?:\n|$)", code[code.index("fun installApkViaDexShell("):])[0]
    assert 'GRANT_FLAG_DENIED = "INSTALL_GRANT_RUNTIME_PERMISSIONS permission to use"' in code
    assert "var installResult = runHelper(DEX_SHELL_INSTALL_FLAGS)" in dex
    retry = dex.index("installResult = runHelper(DEX_SHELL_INSTALL_FLAGS and INSTALL_GRANT_RUNTIME_PERMISSIONS.inv())")
    assert retry < dex.index("val after = installedPackages(")


def test_android_confirmed_localinstall_updates_installed_app_via_dex_helper():
    engine = _code(KOTLIN / "usb/InstallEngine.kt")
    branch = engine[engine.index("if (confirmedMethod != null)"):]
    fallback = branch.index('label == "localinstall" && packageInstalled(currentPackageName, log)')
    assert fallback < branch.index("dropStaged()")  # залитый APK ещё на магнитоле — dex-хелпер берёт его же
    assert 'INSTALL_METHODS.first { it.first == "dex_shell_install" }.second' in branch
    assert "localinstall поверх не ставит, dex-хелпер не обновил" in branch
