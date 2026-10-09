"""cars/_shared/adb_permissions.py:uninstall_app раньше безусловно писал
"Готово." независимо от того, что реально ответило устройство на "pm
uninstall" (реальный случай — лог #536 на сервере: техник попробовал
"pm uninstall android", ядро системы, заведомо защищено от удаления — и
всё равно увидел "Готово.", как будто оно правда удалилось). Теперь
результат смотрим в тексте, тем же приёмом, что и install_context.py:
_check_pm_install_result на десктопе (см. android/.../AdbPermissions.kt
за портом того же исправления на Android)."""
from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
ADB_PERMISSIONS_PATH = ROOT / "cars/_shared/adb_permissions.py"


def _load_module():
    """cars/_shared/ вне обычных Python-пакетов проекта — тот же приём
    импорта по прямому пути, что и в test_android_wizard_spec_usb_apks.py."""
    spec = importlib.util.spec_from_file_location("adb_permissions_test", ADB_PERMISSIONS_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


adb_permissions = _load_module()


_SYSTEM = "package:android\npackage:com.android.systemui\n"


def _make_ctx(stdout="", stderr=""):
    """stdout/stderr — ответ на pm uninstall; список штатных (pm list packages -s) — как у настоящей магнитолы: пустым
    он не бывает, а пустой ответ программа теперь понимает как «не узнать» и приложение не трогает."""
    log = []

    def shell(command, **kwargs):
        if command == "pm list packages -s":
            return SimpleNamespace(stdout=_SYSTEM, stderr="")
        return SimpleNamespace(stdout=stdout, stderr=stderr)

    ctx = SimpleNamespace(log=log.append, shell=shell, sleep=lambda seconds: None)
    return ctx, log


def test_uninstall_app_reports_success():
    ctx, log = _make_ctx(stdout="Success\n")
    adb_permissions.uninstall_app(ctx, "com.example.app")
    assert log == ["Удаляю приложение: com.example.app", "Готово."]


def test_uninstall_app_reports_failure_instead_of_fake_gotovo():
    """Регрессия на лог #536: pm uninstall отвечает Failure — раньше это всё равно печаталось как "Готово.".
    (Там был сам «android» — штатные с 1.0.42 отсекаются раньше, здесь пакет не из списка штатных.)"""
    ctx, log = _make_ctx(stdout="Failure [DELETE_FAILED_INTERNAL_ERROR]\n")
    adb_permissions.uninstall_app(ctx, "com.example.app")
    assert log == [
        "Удаляю приложение: com.example.app",
        "Не удалось удалить: Failure [DELETE_FAILED_INTERNAL_ERROR]",
    ]


def test_uninstall_app_reports_empty_response_as_failure():
    """Устройство вообще ничего не ответило (сорвалась связь и т.п.) — не
    должно молча выглядеть как успех."""
    ctx, log = _make_ctx(stdout="", stderr="")
    adb_permissions.uninstall_app(ctx, "com.example.app")
    assert log == ["Удаляю приложение: com.example.app", "Не удалось удалить: устройство не ответило"]


def test_uninstall_app_checks_stderr_too():
    ctx, log = _make_ctx(stdout="", stderr="Failure [NOT_FOUND]")
    adb_permissions.uninstall_app(ctx, "com.example.missing")
    assert log == ["Удаляю приложение: com.example.missing", "Не удалось удалить: Failure [NOT_FOUND]"]


# disable_app/enable_app — тот же класс бага: раньше безусловное «Готово.».
# Успех pm печатает как «Package <пакет> new state: ...».

def test_disable_app_reports_success():
    ctx, log = _make_ctx(stdout="Package com.baidu.carlife new state: disabled-user\n")
    adb_permissions.disable_app(ctx, "com.baidu.carlife")
    assert log == ["Отключаю приложение: com.baidu.carlife", "Готово."]


def test_disable_app_reports_refusal():
    # Пакет не из списка штатных (их программа отсекает раньше), но прошивка его защищает сама.
    ctx, log = _make_ctx(stderr="Exception occurred while executing: java.lang.SecurityException: "
                                "Cannot disable a protected package: com.baidu.carlife\n")
    adb_permissions.disable_app(ctx, "com.baidu.carlife")
    assert log[-1].startswith("Не удалось отключить: ") and "SecurityException" in log[-1]


def test_enable_app_reports_success_and_failure():
    ctx, log = _make_ctx(stdout="Package com.x new state: enabled\n")
    adb_permissions.enable_app(ctx, "com.x")
    assert log[-1] == "Готово."
    ctx, log = _make_ctx(stdout="")
    adb_permissions.enable_app(ctx, "com.typo")
    assert log[-1] == "Не удалось включить: устройство не ответило"


# Штатные приложения удалять и отключать нельзя (владелец, 2026-09-26; лог #1013 — техник отключил сам
# «android», pm ответил «new state: disabled-user»). В списке выбора их больше нет, а это — на случай
# ручного ввода имени и старых моделей.

def _ctx_by_command(responses):
    log, commands = [], []

    def shell(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(stdout=responses.get(command, ""), stderr="")

    return SimpleNamespace(log=log.append, shell=shell, sleep=lambda seconds: None), log, commands


SYSTEM_LIST = "package:android\npackage:com.android.systemui\npackage:com.geely.launcher3\n"


def test_uninstall_refuses_stock_app_without_touching_it():
    ctx, log, commands = _ctx_by_command({"pm list packages -s": SYSTEM_LIST})
    adb_permissions.uninstall_app(ctx, "com.android.systemui")
    assert log == ["Удаляю приложение: com.android.systemui",
                   "Не удалось удалить: это штатное приложение магнитолы — удалять его через программу нельзя."]
    assert not any(c.startswith("pm uninstall") for c in commands)


def test_disable_refuses_stock_app_without_touching_it():
    ctx, log, commands = _ctx_by_command({"pm list packages -s": SYSTEM_LIST})
    adb_permissions.disable_app(ctx, "android")
    assert log == ["Отключаю приложение: android",
                   "Не удалось отключить: это штатное приложение магнитолы — отключать его через программу нельзя."]
    assert not any(c.startswith("pm disable") for c in commands)


def test_third_party_app_still_removed_and_disabled():
    ctx, log, _ = _ctx_by_command({"pm list packages -s": SYSTEM_LIST, "pm uninstall ru.yandex.music": "Success\n"})
    adb_permissions.uninstall_app(ctx, "ru.yandex.music")
    assert log[-1] == "Готово."
    ctx, log, _ = _ctx_by_command({"pm list packages -s": SYSTEM_LIST,
                                   "pm disable-user --user 0 ru.yandex.music": "Package ru.yandex.music new state: disabled-user\n"})
    adb_permissions.disable_app(ctx, "ru.yandex.music")
    assert log[-1] == "Готово."


def test_enable_is_not_restricted():
    # Включить можно и штатное — чтобы вернуть отключённое раньше.
    ctx, log, _ = _ctx_by_command({"pm list packages -s": SYSTEM_LIST, "pm enable android": "Package android new state: enabled\n"})
    adb_permissions.enable_app(ctx, "android")
    assert log[-1] == "Готово."


def test_unknown_stock_list_refuses_instead_of_removing():
    """Магнитола иногда отвечает на pm list packages пустым выводом (логи №4374, №4543). Раньше пустой список штатных
    значил «не штатное» — введённое вручную штатное приложение удалилось бы. Теперь — отказ с понятной причиной."""
    ctx, log, commands = _ctx_by_command({"pm uninstall com.android.systemui": "Success\n"})
    adb_permissions.uninstall_app(ctx, "com.android.systemui")
    assert log[-1].startswith("Не удалось удалить: магнитола не отдала список штатных приложений")
    assert commands.count("pm list packages -s") == adb_permissions._PM_LIST_TRIES
    assert not any(c.startswith("pm uninstall") for c in commands)
    ctx, log, commands = _ctx_by_command({})
    adb_permissions.disable_app(ctx, "com.android.systemui")
    assert log[-1].startswith("Не удалось отключить: магнитола не отдала список штатных приложений")
    assert not any(c.startswith("pm disable") for c in commands)


def test_unknown_stock_list_but_listed_as_third_party_is_removed():
    # Список штатных не пришёл, но в списке сторонних приложение есть — значит, не штатное.
    ctx, log, commands = _ctx_by_command({"pm list packages -3": "package:ru.yandex.music\n",
                                          "pm uninstall ru.yandex.music": "Success\n"})
    adb_permissions.uninstall_app(ctx, "ru.yandex.music")
    assert log[-1] == "Готово."
    assert "pm uninstall ru.yandex.music" in commands


def _ctx_with_answers(answers):
    """Ответы на одну и ту же команду по очереди: (stdout, код возврата)."""
    log, commands, pauses = [], [], []

    def shell(command, check=True, timeout=120):
        commands.append((command, timeout))
        stdout, code = answers.pop(0) if answers else ("", 0)
        return SimpleNamespace(stdout=stdout, stderr="", returncode=code)

    return SimpleNamespace(log=log.append, shell=shell, sleep=pauses.append), log, commands, pauses


def test_package_list_retried_when_unit_answers_empty():
    ctx, log, commands, pauses = _ctx_with_answers([("", 0), ("package:ru.yandex.music\n", 0)])
    assert adb_permissions._pm_packages(ctx, "-3") == ["ru.yandex.music"]
    assert [c for c, _ in commands] == ["pm list packages -3", "pm list packages -3"]
    assert pauses == [adb_permissions._PM_LIST_PAUSE]
    assert log == ["Список приложений получен с 2-й попытки (pm list packages -3)."]


def test_package_list_answer_logged_when_never_received():
    ctx, log, commands, pauses = _ctx_with_answers([("", 0), ("Error: binder\nfailed", 255), ("", 0)])
    assert adb_permissions._pm_packages(ctx, "-3") == []
    assert len(commands) == adb_permissions._PM_LIST_TRIES and len(pauses) == adb_permissions._PM_LIST_TRIES - 1
    assert all(timeout == adb_permissions._PM_LIST_TIMEOUT for _, timeout in commands)
    assert log == ["Магнитола не отдала список приложений (pm list packages -3, попыток: 3): ответ «пусто», код 0."]


def test_package_list_answer_text_is_logged():
    ctx, log, _, _ = _ctx_with_answers([("Error: binder\nfailed", 255)] * 3)
    assert adb_permissions._pm_packages(ctx, "-3") == []
    assert log == ["Магнитола не отдала список приложений (pm list packages -3, попыток: 3): ответ «Error: binder ⏎ "
                   "failed», код 255."]


def test_package_list_first_try_is_silent():
    ctx, log, commands, pauses = _ctx_with_answers([("package:a.b\npackage:c.d\n", 0)])
    assert adb_permissions._pm_packages(ctx, "-3") == ["a.b", "c.d"]
    assert len(commands) == 1 and pauses == [] and log == []

