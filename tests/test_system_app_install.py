"""Способ «в системную папку» (BAIC U5 Plus, владелец 2026-09-27): adb root → disable-verity → remount, APK в
/system/app/<пакет>/, chmod 644. Разрешения — НЕ здесь: Android видит такое приложение только после перезагрузки, а
ADB на этой магнитоле её не переживает — их выдаёт отдельный этап после неё (tests/test_system_apps_by_us.py).
Ответы adb — как у настоящего adbd Android 9 (remount_service.cpp, set_verity_enabled_state_service). Здесь — ПК
(app/install_context.py); Android — InstallEngine.kt (тот же порядок команд)."""
from __future__ import annotations
import threading
import zipfile
from types import SimpleNamespace

import pytest

from app import install_context
from app.install_context import InstallCancelled, InstallContext, parse_df_free_bytes

PKG = "com.dudu.autoui"
FOLDER = f"/system/app/{PKG}"
DF_OK = ("Filesystem       1K-blocks    Used Available Use% Mounted on\n"
         "/dev/block/dm-0    2031440 1494856    520200  75% /\n")


def _apk(tmp_path, name="DuduAutoUi1.001008.apk", libs=(("arm64-v8a", zipfile.ZIP_DEFLATED),)):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("AndroidManifest.xml", b"manifest")
        zf.writestr("classes.dex", b"dex")
        for abi, compression in libs:
            zf.writestr(zipfile.ZipInfo(f"lib/{abi}/libdudu.so"), b"\x7fELF" + b"0" * 5000, compress_type=compression)
    return path


class FakeDevice:
    """adb на магнитоле: ответы по команде, всё записанное — в commands."""

    def __init__(self, **overrides):
        self.commands = []
        self.booted_registered = set()
        self.answers = {
            "root": "restarting adbd as root",
            "disable-verity": "Verity already disabled on /system",
            "remount": "remount succeeded",
            "df /system/app": DF_OK,
            "getprop ro.product.cpu.abilist": "arm64-v8a,armeabi-v7a,armeabi",
            "getprop sys.boot_completed": "1",
        }
        self.answers.update(overrides)
        self.rebooted = 0

    def run(self, *args, check=True, timeout=120):
        self.commands.append(args)
        if args[0] == "shell":
            text = self._shell(args[1])
        elif args[0] == "reboot":
            self.rebooted += 1
            text = ""
        elif args[0] in ("wait-for-device", "wait-for-disconnect", "push"):
            text = ""
        else:
            text = self.answers.get(args[0], "")
        return SimpleNamespace(stdout=text, stderr="", returncode=0)

    def _shell(self, command):
        if command.startswith("pm path "):
            package = command.split()[-1]
            if self.rebooted and package in self.booted_registered:
                return f"package:/system/app/{package}/{package}.apk\n"
            return self.answers.get(command, "")
        if "echo MSQD_OK" in command:
            return "MSQD_OK\n"
        return self.answers.get(command, "")

    def shells(self):
        return [c[1] for c in self.commands if c[0] == "shell"]


@pytest.fixture()
def make_ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(install_context, "read_package_name", lambda path: PKG)

    def make(device, apks=None, method="system_app"):
        apks = apks if apks is not None else [_apk(tmp_path)]
        log = []
        ctx = InstallContext(adb_path="fake-adb", device_serial="HU123", model_dir=tmp_path, selected_apks=apks,
                             log_fn=log.append, cancel_flag=threading.Event(), shared_dir=None,
                             preferred_install_method=method, device_confirmed=True)
        ctx._adb.run = device.run
        ctx.sleep = lambda seconds: None
        granted = []
        ctx._grant_all_permissions_if_available = granted.append
        ctx._same_apk_already_installed = lambda apk: False
        ctx.test = SimpleNamespace(log=log, granted=granted)
        return ctx
    return make


def test_whole_flow_root_remount_push_chmod_without_reboot(make_ctx):
    device = FakeDevice()
    ctx = make_ctx(device)
    ctx.install_selected_apks()

    names = [c[0] if c[0] != "shell" else c[1] for c in device.commands]
    # Строго по очереди, как в .bat владельца: root, disable-verity, remount — и только потом файлы.
    assert names.index("root") < names.index("disable-verity") < names.index("remount") < names.index("push")
    pushes = [c for c in device.commands if c[0] == "push"]
    assert pushes[0][2] == f"{FOLDER}/{PKG}.apk"
    assert pushes[1][2] == f"{FOLDER}/lib/arm64/libdudu.so"  # сжатая .so — рядом, под arm64
    chmod = next(s for s in device.shells() if s.startswith("chmod 755"))
    assert f"chmod 644 {FOLDER}/{PKG}.apk {FOLDER}/lib/arm64/libdudu.so" in chmod
    assert f"echo magicsqd > {FOLDER}/.magicsqd" in chmod
    # Перезагрузку программа не делает (ADB её не переживает), разрешения тут не выдаются.
    assert device.rebooted == 0
    assert ctx.test.granted == []
    assert ctx.installed_apps == []  # «Откатить в сток» через pm uninstall системное не удалит
    assert not ctx.failed_apps
    assert any("Android увидит их после перезагрузки" in line for line in ctx.test.log)


def test_gps_app_gets_mock_location_mark_for_later(make_ctx, monkeypatch):
    monkeypatch.setattr(install_context, "read_apk_mock_location", lambda path: True)
    device = FakeDevice()
    ctx = make_ctx(device)
    ctx.install_selected_apks()
    assert f"echo 'magicsqd mock_location' > {FOLDER}/.magicsqd" in device.shells()


def test_fresh_disable_verity_asks_for_reboot_instead_of_writing(make_ctx):
    device = FakeDevice(**{"disable-verity": "Verity disabled on /system Now reboot your device for settings to take effect"})
    ctx = make_ctx(device)
    with pytest.raises(InstallCancelled) as err:
        ctx.install_selected_apks()
    assert str(err.value).startswith("Проверка системного раздела отключена, но начнёт действовать только после "
                                     "перезагрузки. Перезагрузите магнитолу, снова включите ADB")
    names = [c[0] for c in device.commands]
    assert "remount" not in names and "push" not in names and device.rebooted == 0


def test_no_root_stops_with_clear_text_and_tries_nothing_else(make_ctx):
    device = FakeDevice(root="adbd cannot run as root in production builds")
    ctx = make_ctx(device)
    with pytest.raises(InstallCancelled) as err:
        ctx.install_selected_apks()
    assert str(err.value).startswith("«DuduAutoUi1.001008.apk» не установлено: магнитола не дала права root")
    assert not any(c[0] in ("install", "push") for c in device.commands)
    assert not any(s.startswith("pm install") for s in device.shells())


def test_remount_failure_is_named(make_ctx):
    device = FakeDevice(remount="remount of /system failed: Permission denied remount failed")
    ctx = make_ctx(device)
    with pytest.raises(InstallCancelled, match="системный раздел не открылся на запись"):
        ctx.install_selected_apks()
    assert device.rebooted == 0


def test_not_enough_space_on_system(make_ctx):
    device = FakeDevice(**{"df /system/app": "Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/block/dm-0 2031440 2031000 440 99% /\n"})
    ctx = make_ctx(device)
    with pytest.raises(InstallCancelled, match="на системном разделе магнитолы не хватает места"):
        ctx.install_selected_apks()
    assert not any(c[0] == "push" for c in device.commands)


def test_old_flat_copy_from_bat_is_replaced(make_ctx):
    device = FakeDevice(**{f"pm path {PKG}": "package:/system/app/DuduAutoUi1.001008.apk\n"})
    device.booted_registered.add(PKG)
    ctx = make_ctx(device)
    ctx.install_selected_apks()
    assert f"rm -rf {FOLDER} /system/app/DuduAutoUi1.001008.apk" in device.shells()


def test_never_tried_automatically(make_ctx):
    """Без выбора модели способ не пробуется никогда — даже когда все остальные отказали."""
    device = FakeDevice()
    device.answers["install"] = "Failure [INSTALL_FAILED_ABORTED]"
    ctx = make_ctx(device, method="")
    ctx.install_apk = lambda *a, **k: (_ for _ in ()).throw(install_context.AdbError("Failure [INSTALL_FAILED_ABORTED]"))
    with pytest.raises(InstallCancelled):
        ctx.install_selected_apks()
    assert not any(c[0] in ("root", "remount") for c in device.commands)
    assert not any(s.startswith("rm -rf /system") for s in device.shells())


def test_already_installed_same_file_only_gets_permissions(make_ctx):
    device = FakeDevice()
    ctx = make_ctx(device)
    ctx._same_apk_already_installed = lambda apk: True
    ctx.install_selected_apks()
    assert ctx.test.granted == [PKG]
    assert not any(c[0] in ("root", "reboot", "push") for c in device.commands)


def test_libs_stored_and_aligned_are_not_copied(tmp_path, make_ctx):
    apk = tmp_path / "aligned.apk"
    with zipfile.ZipFile(apk, "w") as zf:
        zf.writestr("AndroidManifest.xml", b"m")
        info = zipfile.ZipInfo("lib/arm64-v8a/libx.so")
        # Выравниваем данные на 4096 через поле extra, как zipalign -p.
        header_offset = zf.fp.tell()
        name_len = len(info.filename)
        pad = (4096 - (header_offset + 30 + name_len + 4) % 4096) % 4096
        info.extra = b"\xd9\x35" + (pad).to_bytes(2, "little") + b"\x00" * pad
        zf.writestr(info, b"\x7fELF" + b"1" * 100, compress_type=zipfile.ZIP_STORED)
    device = FakeDevice()
    ctx = make_ctx(device, apks=[apk])
    assert ctx._extract_native_libs(apk) is None


def test_libs_follow_device_abi_order(tmp_path, make_ctx):
    apk = _apk(tmp_path, libs=(("arm64-v8a", zipfile.ZIP_DEFLATED), ("armeabi-v7a", zipfile.ZIP_DEFLATED)))
    ctx = make_ctx(FakeDevice(**{"getprop ro.product.cpu.abilist": "armeabi-v7a,armeabi"}), apks=[apk])
    isa, lib_dir, size = ctx._extract_native_libs(apk)
    assert isa == "arm" and [p.name for p in lib_dir.iterdir()] == ["libdudu.so"] and size == 5004


def test_no_libs_for_this_processor(tmp_path, make_ctx):
    apk = _apk(tmp_path, libs=(("x86", zipfile.ZIP_DEFLATED),))
    ctx = make_ctx(FakeDevice(), apks=[apk])
    with pytest.raises(install_context.AdbError, match="нет библиотек под процессор магнитолы"):
        ctx._extract_native_libs(apk)


@pytest.mark.parametrize("text, expected", [
    (DF_OK, 520200 * 1024),
    ("Filesystem Size Used Free Blksize\n/system 1.9G 1.8G 120.5M 4096\n", int(120.5 * 1024 * 1024)),
    ("df: /system/app: No such file or directory\n", None),
    ("", None),
])
def test_parse_df(text, expected):
    assert parse_df_free_bytes(text) == expected


# --- Android (Kotlin-тестов в проекте нет — проверяем исходники, как tests/test_android_replug_and_scan.py) ---
import re  # noqa: E402
from pathlib import Path  # noqa: E402

KOTLIN = Path(__file__).resolve().parents[1] / "android/app/src/main/java/ru/magicsqd/mobile/usb"


def _kotlin(name: str) -> str:
    text = (KOTLIN / name).read_text(encoding="utf-8")
    return "\n".join(line.split("//")[0] for line in re.sub(r"/\*.*?\*/", "", text, flags=re.S).splitlines())


def test_android_system_app_only_when_model_chose_it():
    engine = _kotlin("InstallEngine.kt")
    assert '"system_app" to { apk, staged, methodLog -> installSystemApp(apk, staged, methodLog) }' in engine
    assert 'private val EXCLUSIVE_METHODS = setOf("system_app")' in engine
    # В автоматическом переборе его нет, выбран моделью — только он; память способа его не подставляет.
    # methods — встроенные способы плюс свой способ модели «py:», если он выбран (tests/test_py_install_method.py).
    assert "methods.indices.filter { methods[it].first !in EXCLUSIVE_METHODS }" in engine
    assert "val exclusive = preferredMethod in EXCLUSIVE_METHODS || pyMethod != null" in engine
    assert "exclusive -> listOf(preferredIndex)" in engine
    assert "?.takeIf { it !in EXCLUSIVE_METHODS }" in engine


def test_android_same_order_as_pc_and_no_reboot():
    session = _kotlin("AdbSession.kt")
    open_partition = session[session.index("fun openSystemPartition("):session.index("fun remountSystem(")]
    assert open_partition.index('"disable-verity:"') > open_partition.index("rootAndReconnect(context, log)")
    assert "return remountSystem(log)" in open_partition
    assert '"reboot:"' not in open_partition and "fun rebootAndWait(" not in session  # ADB перезагрузку не переживает
    engine = _kotlin("InstallEngine.kt")
    assert 'confirmedMethod?.let { methods[it].first } == "system_app"' in engine
    assert "systemAppsWritten++" in engine and "finishSystemApps" not in engine
    assert "echo 'magicsqd mock_location' > /system/app/$currentPackageName/$SYSTEM_APP_MARKER" in engine
    assert '"grant_system_apps" -> AdbPermissions.grantSystemApps(log)?.let { return StageRunResult.Failed(it) }' in engine
    method = engine[engine.index("private fun installSystemApp("):engine.index("private fun shellText(")]
    for piece in ('"/system/app/$pkg"', '"$folder/$pkg.apk"', "cat $staged > $target", "chmod 644",
                  "echo magicsqd > $marker", 'dirs += listOf("$folder/lib", "$folder/lib/$isa")'):
        assert piece in method, piece


def test_android_buttons_see_our_system_apps():
    # Без вырезания комментариев: «/*» в строке с маской папок приняли бы за начало комментария.
    permissions = (KOTLIN / "AdbPermissions.kt").read_text(encoding="utf-8")
    assert 'for f in /system/app/*/$SYSTEM_APP_MARKER; do [ -f \\"\\$f\\" ] && echo \\"\\$f \\$(cat \\"\\$f\\")\\"; done' in permissions
    assert "fun grantSystemApps(log: (String) -> Unit): String?" in permissions
    assert "if (pkg in systemAppsByUs(log)) { removeSystemAppByUs(context, pkg, log); return }" in permissions
    assert 'safeShell("rm -rf /system/app/$pkg", log, 60_000)' in permissions



def test_android_permissions_button_without_picker():
    app_js = (KOTLIN.parents[4] / "assets/js/app.js").read_text(encoding="utf-8")
    assert 'const DIRECT_ACTIONS = { grant_system_apps: [{ kind: "grant_system_apps" }] };' in app_js
    assert "commands: DIRECT_ACTIONS[action.kind] || action.commands || []" in app_js
