"""Файл, который не встанет никаким способом, — сразу понятная причина вместо перебора способов (владелец,
2026-10-01). Правила — app/apk_check.py (та же копия на Android); строки отказов — из настоящих логов."""
from __future__ import annotations
import re
import threading
import zipfile
from pathlib import Path

import pytest
from types import SimpleNamespace

from app import apk_check, install_context
from app.install_context import InstallContext, UnsuitableApk

ROOT = Path(__file__).resolve().parents[1]


def _zip(path: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            archive.writestr(name, b"x")
    return path


def test_file_problem(tmp_path):
    assert apk_check.file_problem(_zip(tmp_path / "ok.apk", ["AndroidManifest.xml", "classes.dex"])) is None
    broken = tmp_path / "Spotify signed.apk"
    broken.write_bytes(b"\x00" * 4096)  # не архив (как самодельная пересборка, лог №1986)
    assert "не APK: файл повреждён или недокачан" in apk_check.file_problem(broken)
    xapk = _zip(tmp_path / "Settings+App_2.0_APKPure.xapk", ["manifest.json", "base.apk", "config.arm64_v8a.apk"])
    assert "пакет из нескольких частей (XAPK/APKS)" in apk_check.file_problem(xapk)  # лог №2099
    renamed = _zip(tmp_path / "app.apk", ["manifest.json", "base.apk"])  # XAPK, переименованный в .apk
    assert "пакет из нескольких частей" in apk_check.file_problem(renamed)
    empty = _zip(tmp_path / "x.apk", ["readme.txt"])
    assert "внутри нет AndroidManifest.xml" in apk_check.file_problem(empty)
    assert apk_check.file_problem(xapk).startswith("«Settings+App_2.0_APKPure.xapk»")
    assert apk_check.file_problem(tmp_path / "нет-такого.apk") is None  # не скачан — своё сообщение у программы


@pytest.mark.parametrize("reason, expected", [
    # №1942: YouTube Morphe под x86
    ("Failure [INSTALL_FAILED_NO_MATCHING_ABIS: Failed to extract native libraries, res=-113]", "не под процессор этой магнитолы"),
    # №2087: ContraCam на магнитоле с Android 12 (API 31)
    ("Exception occurred while executing 'install': java.lang.IllegalArgumentException: Error: Failed to parse APK "
     "file: /data/local/tmp/ContraCam_4.0.107-Google.apk: Requires newer sdk version #32 (current version is #31)",
     "требует Android новее, чем на магнитоле (нужен API 32, на магнитоле 31)"),
    ("Failure [INSTALL_FAILED_OLDER_SDK: Failed parse during installPackageLI]", "требует Android новее"),
    # №1986: самодельная пересборка Spotify
    ("monji: wrote 151844495 bytes Failure status=4 message=INSTALL_PARSE_FAILED_NOT_APK: Failed to parse "
     "/data/app/vmdl780809347.tmp/base.apk", "не может прочитать"),
    # №2099: .xapk через pm install -S
    ("Failure [INSTALL_PARSE_FAILED_UNEXPECTED_EXCEPTION: Failed to parse /data/app/vmdl1078287920.tmp/base.apk: "
     "AndroidManifest.xml]", "не может прочитать"),
    ("Failure [INSTALL_FAILED_MISSING_SPLIT: Missing split for com.x]", "только часть приложения"),
    ("Failure [INSTALL_PARSE_FAILED_NO_CERTIFICATES: Package /data/app/x/base.apk has no certificates]",
     "не принимает подпись этого APK"),
    # №2404: старый Android магнитолы Harman не понимает подпись v2/v3 (YT Morphe) — тот же код, что и без подписи
    ("Failure [INSTALL_PARSE_FAILED_NO_CERTIFICATES: Failed to collect certificates from /data/app/vmdl71700060.tmp/"
     "base.apk: Attempt to get length of null array]", "не принимает подпись этого APK"),
    # localinstall: причина — итог хелпера из logcat (install_context.localinstall_status)
    ("localinstall не подтвердил успех (новых пакетов: нет): install status=1 INSTALL_FAILED_NO_MATCHING_ABIS: x",
     "не под процессор этой магнитолы"),
    # №2583: мод Навигатора на Tank 300 со штатным yandex.auto.auth (adb install)
    ("adb: failed to install C:\\apk\\Яндекс\\YN4_broadcast_MOD_kill.apk: Failure [INSTALL_FAILED_DUPLICATE_PERMISSION: "
     "Package ru.yandex.yandexnavi attempting to redeclare permission com.yandex.permission.READ_CREDENTIALS already "
     "owned by yandex.auto.auth]", "конфликтует с уже установленным yandex.auto.auth"),
    # №2597: Яндекс Музыка рядом с модом Навигатора (dex-хелпер)
    ("monji: wrote 44653580 bytes Failure status=5 message=INSTALL_FAILED_DUPLICATE_PERMISSION: Package ru.yandex.music "
     "attempting to redeclare permission com.yandex.permission.READ_CREDENTIALS already owned by ru.yandex.yandexnavi",
     "конфликтует с уже установленным ru.yandex.yandexnavi"),
    ("Failure [INSTALL_FAILED_DUPLICATE_PERMISSION]", "конфликтует с уже установленным приложением"),
    # №2673 (ПК, dex-хелпер): Settings.apk из каталога Geely Cityray на Geely Preface FS11
    ("monji: session=1036765551 flags=0x116 monji: wrote 20109392 bytes Failure status=5 message="
     "INSTALL_FAILED_SHARED_USER_INCOMPATIBLE: Reconciliation failed...: Reconcile failed: Package com.android.settings has "
     "no signatures that match those in shared user android.uid.system; ignoring!",
     "подменяет системное приложение com.android.settings — такие ставятся только с подписью прошивки"),
    # №2755 (Android 1.0.54): тот же отказ через «dex-хелпер не подтвердил успех»
    ("dex-хелпер не подтвердил успех (новых пакетов: нет): monji: session=1083604760 flags=0x116 monji: wrote 20109392 "
     "bytes Failure status=5 message=INSTALL_FAILED_SHARED_USER_INCOMPATIBLE: Reconciliation failed...",
     "подменяет системное приложение — такие ставятся только с подписью прошивки"),
    # №2673: Time_Zone.apk поверх постоянного системного приложения
    ("monji: session=2056387230 flags=0x116 monji: wrote 209310 bytes Failure status=4 message=INSTALL_FAILED_INVALID_APK: "
     "Package com.autolink.timesync.service is a persistent app. Persistent apps are not updateable.",
     "обновляет постоянное системное приложение com.autolink.timesync.service — прошивка не даёт"),
])
def test_rejections_that_mean_the_file_itself(reason, expected):
    text = apk_check.rejection_message("app.apk", reason)
    assert text and expected in text and text.startswith("«app.apk»")


@pytest.mark.parametrize("reason", [
    "pm install не вернул Success: ",                                           # способ закрыт прошивкой
    "localinstall не подтвердил успех (новых пакетов: нет): ",
    "Failure [INSTALL_FAILED_VERSION_DOWNGRADE]",                               # своя обработка — пропуск
    "Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE: Package x signatures do not match]",
    "Failure [INSTALL_FAILED_CONFLICTING_PROVIDER: Can't install because provider name x is already used]",
    "Error: Unable to open file: /sdcard/Download/x.apk Consider using a file under /data/local/tmp/",
    "jdwp_whitelist: не удалось прочитать имя пакета — нужно для JDWP-патча",
])
def test_ordinary_method_failures_keep_trying(reason):
    assert apk_check.rejection_message("app.apk", reason) is None


def test_duplicate_permission_names_the_owner_and_the_permission():
    reason = ("Failure [INSTALL_FAILED_DUPLICATE_PERMISSION: Package ru.yandex.yandexnavi attempting to redeclare "
              "permission com.yandex.permission.READ_CREDENTIALS already owned by yandex.auto.auth].")
    text = apk_check.rejection_message("YN4.apk", reason)
    assert text == ("«YN4.apk» конфликтует с уже установленным yandex.auto.auth: оба объявляют разрешение "
                    "com.yandex.permission.READ_CREDENTIALS, а подписаны разными ключами — не встанет никаким способом. "
                    "Нужна сборка с той же подписью, что у yandex.auto.auth, или удалите yandex.auto.auth, если это не "
                    "штатное приложение магнитолы.")


def test_android_copy_matches():
    android = ROOT / "android/app/src/main/python/apk_check.py"
    assert android.read_bytes() == (ROOT / "app/apk_check.py").read_bytes()


# --- ПК: пропуск без перебора способов, остальные ставятся, честный итог -------------------------------------------

@pytest.fixture()
def make_ctx(tmp_path, monkeypatch):
    def make(apks: list[Path], method_errors: dict[str, str] | None = None):
        log = []
        ctx = InstallContext(adb_path="fake-adb", device_serial="fake", model_dir=tmp_path, selected_apks=apks,
                             log_fn=log.append, cancel_flag=threading.Event(), shared_dir=None)
        ctx.require_device = lambda: None
        ctx._after_app_installed = lambda *args, **kwargs: None
        tried = []

        def install_with_method(method, path, extra_args):
            tried.append((Path(path).name, method))
            error = (method_errors or {}).get(Path(path).name)
            if error:
                raise install_context.AdbError(error)
        ctx._install_with_method = install_with_method
        ctx.test_log, ctx.tried = log, tried
        return ctx
    return make


def test_unsuitable_file_is_skipped_before_any_method_and_the_rest_installs(tmp_path, make_ctx):
    xapk = _zip(tmp_path / "Settings+App_2.0_APKPure.xapk", ["manifest.json", "base.apk"])
    good = _zip(tmp_path / "good.apk", ["AndroidManifest.xml"])
    ctx = make_ctx([xapk, good])
    ctx.install_selected_apks()
    assert [name for name, _ in ctx.tried] == ["good.apk"]  # XAPK ни одним способом не пробовали
    assert len(ctx.failed_apps) == 1 and "пакет из нескольких частей" in ctx.failed_apps[0]
    assert ctx.apps_ok == 1
    assert any("остальные приложения из списка установлены" in line for line in ctx.test_log)


def test_device_rejection_of_the_file_stops_the_method_search(tmp_path, make_ctx):
    x86 = _zip(tmp_path / "YouTube_Morphe-x86.apk", ["AndroidManifest.xml"])
    abi = "Failure [INSTALL_FAILED_NO_MATCHING_ABIS: Failed to extract native libraries, res=-113]"
    ctx = make_ctx([x86], {"YouTube_Morphe-x86.apk": abi})
    ctx.install_selected_apks()
    assert len(ctx.tried) == 1  # раньше — все способы подряд с той же ошибкой (лог №1942)
    assert ctx.apps_ok == 0 and "не под процессор этой магнитолы" in ctx.failed_apps[0]
    assert any(line.startswith("Не установлено: «YouTube_Morphe-x86.apk» собран не под процессор") for line in ctx.test_log)


def test_duplicate_permission_stops_the_method_search(tmp_path, make_ctx):
    music = _zip(tmp_path / "Yandex-Music-2024.02.2_33.1.apk", ["AndroidManifest.xml"])
    good = _zip(tmp_path / "good.apk", ["AndroidManifest.xml"])
    dup = ("Failure [INSTALL_FAILED_DUPLICATE_PERMISSION: Package ru.yandex.music attempting to redeclare permission "
           "com.yandex.permission.READ_CREDENTIALS already owned by ru.yandex.yandexnavi]")
    ctx = make_ctx([music, good], {"Yandex-Music-2024.02.2_33.1.apk": dup})
    ctx.install_selected_apks()
    # №2597: раньше — 7 способов подряд с той же ошибкой и сырой список отказов в конце
    assert [name for name, _ in ctx.tried].count("Yandex-Music-2024.02.2_33.1.apk") == 1
    assert ctx.apps_ok == 1 and "конфликтует с уже установленным ru.yandex.yandexnavi" in ctx.failed_apps[0]


def test_system_app_replacement_stops_the_method_search(tmp_path, make_ctx):
    settings = _zip(tmp_path / "Settings.apk", ["AndroidManifest.xml"])
    good = _zip(tmp_path / "good.apk", ["AndroidManifest.xml"])
    shared = ("Failure status=5 message=INSTALL_FAILED_SHARED_USER_INCOMPATIBLE: Reconciliation failed...: Reconcile "
              "failed: Package com.android.settings has no signatures that match those in shared user android.uid.system")
    ctx = make_ctx([settings, good], {"Settings.apk": shared})
    ctx.install_selected_apks()
    # №2673/№2678: раньше — 8 способов подряд, и после отказа каждый следующий падал с «error: closed»
    assert [name for name, _ in ctx.tried].count("Settings.apk") == 1
    assert ctx.apps_ok == 1 and "подменяет системное приложение com.android.settings" in ctx.failed_apps[0]


def test_locked_method_rejection_gets_the_clear_reason(tmp_path, make_ctx):
    first = _zip(tmp_path / "first.apk", ["AndroidManifest.xml"])
    contra = _zip(tmp_path / "ContraCam.apk", ["AndroidManifest.xml"])
    sdk = "Error: Failed to parse APK file: /data/local/tmp/ContraCam.apk: Requires newer sdk version #32 (current version is #31)"
    ctx = make_ctx([first, contra], {"ContraCam.apk": sdk})
    ctx.install_selected_apks()
    assert ctx.apps_ok == 1
    assert ctx.failed_apps == [apk_check.rejection_message("ContraCam.apk", sdk)]


def test_unsuitable_is_a_skippable_app_failure():
    assert issubclass(UnsuitableApk, install_context.AppInstallFailed)  # install_selected_apks пропускает и идёт дальше


def test_runner_reports_nothing_installed_as_an_error(tmp_path):
    from app.runner import InstallRunner
    results = []
    runner = InstallRunner(adb_path="fake-adb", on_log=lambda m: None,
                           on_finished=lambda ok, msg, **kw: results.append((ok, msg)))

    class FakeModel:
        dir = tmp_path

    def run_fn(ctx):  # единственный файл техника не годится — встало ноль приложений
        ctx.failed_apps.append("«x.xapk» — не APK, а пакет из нескольких частей (XAPK/APKS)")

    runner._run(FakeModel(), "fake-device", [], run_fn, [], "", True)
    assert results == [(False, "Не установлено: «x.xapk» — не APK, а пакет из нескольких частей (XAPK/APKS)")]


# --- Android: то же поведение (Kotlin-тестов нет — проверяем исходник движка) ---------------------------------------

def _kotlin(path: str) -> str:
    text = (ROOT / "android/app/src/main/java/ru/magicsqd/mobile" / path).read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def test_android_engine_uses_the_same_rules():
    engine = _kotlin("usb/InstallEngine.kt")
    assert 'Python.getInstance().getModule("apk_check")' in engine
    assert 'apkCheck.callAttr("file_problem", path, name)' in engine
    assert 'apkCheck.callAttr("rejection_message", name, reason)' in engine
    loop = engine[engine.index("for ((index, path) in apkPaths.withIndex())"):]
    # проверка файла — до заливки на магнитолу
    assert loop.index("val problem = fileProblem(path, file.name)") < loop.index("AdbSession.push(apk, stagedRemote, log)")
    # в переборе отказ «сам файл» обрывает перебор и пропускает приложение
    # (через rejection: «нет подписи» на уже стоящем приложении — свой текст, см. test_android_replug_and_scan.py)
    assert "unsuitable = rejection(file.name, currentPackageName, r.reason, log)\n                        if (unsuitable != null) break" in loop
    assert "if (unsuitable != null) { skipUnsuitable(unsuitable); continue }" in loop
    assert 'if (skipped.isNotEmpty() && okCount == 0) {' in engine
    assert 'return StageRunResult.Failed("Не установлено: " + skipped.joinToString("; "))' in engine


# --- Уже стоящее приложение: путь к base.apk и «нет подписи» на Geely G426 (логи №3750, №3752, №3988) ---------------

_DUMPSYS = """Packages:
  Package [ru.mehanik88.cityraysettings] (4f1a2b3):
    userId=10123
    codePath=/data/app/~~AbC==/ru.mehanik88.cityraysettings-XyZ==
    resourcePath=/data/app/~~AbC==/ru.mehanik88.cityraysettings-XyZ==
    versionCode=10203 minSdk=26 targetSdk=33
    splits=[base]
  Package [com.other] (1):
    codePath=/data/app/com.other-1
"""


@pytest.mark.parametrize("pm_path, dumpsys, expected", [
    ("package:/data/app/x-1/base.apk\n", None, "/data/app/x-1/base.apk"),          # обычный случай — pm path
    ("package:/data/app/x/base.apk\npackage:/data/app/x/split_config.arm64_v8a.apk\n", None, ""),  # из частей
    ("", None, None),                                                                # пути нет — нужен dumpsys
    ("", _DUMPSYS, "/data/app/~~AbC==/ru.mehanik88.cityraysettings-XyZ==/base.apk"),  # G426: путь из dumpsys
    ("", _DUMPSYS.replace("splits=[base]", "splits=[base, config.arm64_v8a]"), ""),  # из частей по dumpsys
    ("", "Packages:\n  Package [ru.mehanik88.cityraysettings] (1):\n    codePath=/system/app/GSettings\n", None),
    ("", "Unable to find package: ru.mehanik88.cityraysettings\n", None),            # пакета нет
])
def test_installed_base_apk(pm_path, dumpsys, expected):
    assert apk_check.installed_base_apk(pm_path, dumpsys, "ru.mehanik88.cityraysettings") == expected


def test_already_installed_message_names_the_package_and_the_way_out():
    text = apk_check.already_installed_message("GSettings_1.2.3(10203)_release.apk", "ru.mehanik88.cityraysettings")
    assert text.startswith("«GSettings_1.2.3(10203)_release.apk»: на магнитоле уже стоит ru.mehanik88.cityraysettings")
    assert "«Удалить приложение»" in text and "нужна другая сборка" not in text


_NO_CERTS = ("dex-хелпер не подтвердил успех (новых пакетов: нет): monji: session=64462960 flags=0x116 monji: wrote "
             "6390474 bytes Failure status=4 message=INSTALL_PARSE_FAILED_NO_CERTIFICATES: Failed collecting certificates")


def _device(ctx, responses):
    def shell(command, check=True, timeout=120):
        return SimpleNamespace(stdout=responses.get(command, ""), stderr="", returncode=0)
    ctx.shell = shell


def test_no_certificates_on_installed_app_says_already_installed(tmp_path, make_ctx, monkeypatch):
    gsettings = _zip(tmp_path / "GSettings_1.2.3(10203)_release.apk", ["AndroidManifest.xml"])
    monkeypatch.setattr(install_context, "read_package_name", lambda path: "ru.mehanik88.cityraysettings")
    ctx = make_ctx([gsettings], {gsettings.name: _NO_CERTS})
    _device(ctx, {"pm list packages ru.mehanik88.cityraysettings": "package:ru.mehanik88.cityraysettings\n"})
    ctx.install_selected_apks()
    assert len(ctx.tried) == 1 and ctx.apps_ok == 0
    assert ctx.failed_apps == [apk_check.already_installed_message(gsettings.name, "ru.mehanik88.cityraysettings")]


def test_no_certificates_on_new_app_keeps_the_file_verdict(tmp_path, make_ctx, monkeypatch):
    unsigned = _zip(tmp_path / "unsigned.apk", ["AndroidManifest.xml"])
    monkeypatch.setattr(install_context, "read_package_name", lambda path: "com.example.unsigned")
    ctx = make_ctx([unsigned], {unsigned.name: _NO_CERTS})
    _device(ctx, {"pm list packages com.example.unsigned": "package:com.example.unsigned.helper\n"})  # не тот пакет
    ctx.install_selected_apks()
    assert ctx.failed_apps == [apk_check.rejection_message(unsigned.name, _NO_CERTS)]
