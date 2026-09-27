"""Кнопки «Доп. действий» и приложения, которые программа сама положила в /system/app (способ «в системную папку»,
BAIC U5 Plus). Android считает их системными — без метки .magicsqd их не было бы в списках «Запустить», «Выдать
разрешения», «Удалить», а «Удалить»/«Отключить» отказывали бы как штатным. Штатные по-прежнему защищены.
Разрешения им — отдельной кнопкой после перезагрузки (grant_system_apps_permissions): до неё Android их не видит,
а ADB на BAIC U5 Plus перезагрузку не переживает. Здесь — ПК (cars/_shared/adb_permissions.py, серверный контент);
Android — AdbPermissions.kt."""
from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("adb_permissions_ours", ROOT / "cars/_shared/adb_permissions.py")
adb_permissions = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adb_permissions
spec.loader.exec_module(adb_permissions)

OURS = "com.dudu.autoui"
MARKERS = f"/system/app/{OURS}/.magicsqd magicsqd\n/system/app/gb.xxy.hr/.magicsqd magicsqd mock_location\n"
SYSTEM = f"package:android\npackage:com.android.settings\npackage:{OURS}\n"  # gb.xxy.hr — ещё до перезагрузки
THIRD = "package:ru.yandex.yandexnavi\n"


def _ctx(answers, adb_answers=None):
    log, shells, adbs = [], [], []

    def shell(command, check=True, **kwargs):
        shells.append(command)
        return SimpleNamespace(stdout=answers.get(command, ""), stderr="")

    def adb(*args, check=True, timeout=120):
        adbs.append(args)
        return SimpleNamespace(stdout=(adb_answers or {}).get(args[0], ""), stderr="", returncode=0)

    ctx = SimpleNamespace(log=log.append, shell=shell, adb=adb, wait_for_device=lambda timeout=90: None)
    return ctx, SimpleNamespace(log=log, shells=shells, adbs=adbs)


LOOP = adb_permissions._SYSTEM_APP_MARKERS_COMMAND
BASE = {LOOP: MARKERS, "pm list packages -s": SYSTEM, "pm list packages -3": THIRD,
        "pm list packages": THIRD + SYSTEM}


def test_lists_include_our_system_apps_seen_by_android():
    ctx, _ = _ctx(BASE)
    assert adb_permissions.list_installed_packages(ctx) == [OURS, "ru.yandex.yandexnavi"]


def test_no_markers_list_as_before():
    ctx, seen = _ctx({**BASE, LOOP: ""})
    assert adb_permissions.list_installed_packages(ctx) == ["ru.yandex.yandexnavi"]
    assert "pm list packages -s" not in seen.shells  # без наших приложений — лишней команды нет


def test_ours_can_be_disabled_stock_still_cannot():
    ctx, seen = _ctx({**BASE, f"pm disable-user --user 0 {OURS}": f"Package {OURS} new state: disabled-user"})
    adb_permissions.disable_app(ctx, OURS)
    assert seen.log[-1] == "Готово."
    ctx, seen = _ctx(BASE)
    adb_permissions.disable_app(ctx, "com.android.settings")
    assert "штатное приложение магнитолы" in seen.log[-1]
    assert not any(s.startswith("pm disable-user") for s in seen.shells)


def test_uninstall_ours_removes_folder_with_root_and_remount():
    ctx, seen = _ctx(BASE, {"root": "restarting adbd as root", "remount": "remount succeeded"})
    adb_permissions.uninstall_app(ctx, OURS)
    assert [a[0] for a in seen.adbs] == ["root", "remount"]
    assert f"rm -rf /system/app/{OURS}" in seen.shells
    assert not any(s.startswith("pm uninstall") for s in seen.shells)
    assert seen.log[-1] == ("Готово: приложение удалено из системной папки — оно исчезнет с магнитолы после "
                            "перезагрузки.")


def test_uninstall_ours_without_remount_says_why():
    ctx, seen = _ctx(BASE, {"root": "restarting adbd as root", "remount": "remount failed"})
    adb_permissions.uninstall_app(ctx, OURS)
    assert seen.log[-1].startswith("Не удалось удалить: системный раздел не открылся на запись")
    assert not any(s.startswith("rm -rf") for s in seen.shells)


def test_stock_app_still_refused_for_uninstall():
    ctx, seen = _ctx(BASE)
    adb_permissions.uninstall_app(ctx, "com.android.settings")
    assert "штатное приложение магнитолы" in seen.log[-1]
    assert seen.adbs == []


def _grant_ctx(answers):
    ctx, seen = _ctx(answers)
    granted, mock = [], []
    adb_permissions_grant = adb_permissions.grant_all_permissions
    adb_permissions_mock = adb_permissions.set_mock_location_app
    adb_permissions.grant_all_permissions = lambda c, package: granted.append(package)
    adb_permissions.set_mock_location_app = lambda c, package: mock.append(package)
    seen.granted, seen.mock = granted, mock
    seen.restore = lambda: (setattr(adb_permissions, "grant_all_permissions", adb_permissions_grant),
                            setattr(adb_permissions, "set_mock_location_app", adb_permissions_mock))
    return ctx, seen


def test_permissions_after_reboot_for_all_our_apps_and_gps_mark():
    after_reboot = {**BASE, "pm list packages -s": SYSTEM + "package:gb.xxy.hr\n"}
    ctx, seen = _grant_ctx(after_reboot)
    try:
        adb_permissions.grant_system_apps_permissions(ctx)
    finally:
        seen.restore()
    assert seen.granted == [OURS, "gb.xxy.hr"]
    assert seen.mock == ["gb.xxy.hr"]  # метка «mock_location» — GPS-приложение с пометкой


def test_permissions_before_reboot_say_what_to_do():
    import pytest
    ctx, seen = _grant_ctx(BASE)  # gb.xxy.hr Android ещё не видит
    try:
        with pytest.raises(Exception) as err:
            adb_permissions.grant_system_apps_permissions(ctx)
    finally:
        seen.restore()
    assert seen.granted == [OURS]
    assert str(err.value) == ("Android ещё не видит gb.xxy.hr — приложения появятся после перезагрузки магнитолы. "
                              "Перезагрузите её, снова включите ADB в инженерном меню и повторите.")
    assert type(err.value).__name__ == "InstallCancelled"  # без «Ошибка установки:» в окне этапа


def test_permissions_without_our_apps():
    import pytest
    ctx, seen = _grant_ctx({**BASE, LOOP: ""})
    try:
        with pytest.raises(Exception, match="нет приложений, записанных программой в системную папку"):
            adb_permissions.grant_system_apps_permissions(ctx)
    finally:
        seen.restore()


def test_generated_permissions_button_imports_only_where_used():
    from app import car_generator as cg
    button = cg.ActionSpec(label="Выдать разрешения установленным приложениям", kind="grant_system_apps")
    with_button = cg._render_install_py(cg.NewCarSpec(brand="BAIC", model="U5 Plus", steps=[
        cg.StepSpec(type="actions", title="Выдача разрешений", actions=[button])]))
    assert "from adb_permissions import grant_system_apps_permissions  # noqa: E402" in with_button
    assert "def action_step_1_1(ctx):\n    \"\"\"Выдать разрешения установленным приложениям.\"\"\"\n" \
           "    grant_system_apps_permissions(ctx)\n" in with_button
    other = cg._render_install_py(cg.NewCarSpec(brand="X", model="Y", steps=[
        cg.StepSpec(type="actions", title="Доп.", actions=[cg.ActionSpec(label="Запустить", kind="launch_activity")])]))
    assert "grant_system_apps_permissions" not in other
