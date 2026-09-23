"""cars/_shared/adb_permissions.py:_find_service_component — поиск службы
спецвозможностей/доступа к уведомлениям в dumpsys package, чтобы включить её
через settings put secure.

Раньше искал "name=..." в 15 строках ВЫШЕ строки с разрешением службы — в
реальном dumpsys package такого нет, и служба не находилась ни у одного
приложения (жалоба 2026-09-23, Haval Jolion 2026: "Служба спецвозможностей не
найдена в dumpsys" у Simple Control, хотя тот её объявляет). Настоящий формат —
компонент в той же строке ("Service Resolver Table"). Фрагменты ниже — дословно
из dumpsys package эмулятора Android 9 с установленными APK; формат строки тот
же и в AOSP 12 (ComponentResolver.ServiceIntentResolver.dumpFilter)."""
from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
ADB_PERMISSIONS_PATH = ROOT / "cars/_shared/adb_permissions.py"


def _load_module():
    """cars/_shared/ вне обычных Python-пакетов проекта — тот же приём
    импорта по прямому пути, что и в test_uninstall_app_result.py."""
    spec = importlib.util.spec_from_file_location("adb_permissions_service_test", ADB_PERMISSIONS_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


adb_permissions = _load_module()

A11Y = "BIND_ACCESSIBILITY_SERVICE"

SIMPLE_CONTROL_DUMPSYS = """\
      androidx.work.impl.background.systemalarm.UpdateProxies:
        8aa36b4 ace.jun.simplecontrol/androidx.work.impl.background.systemalarm.ConstraintProxyUpdateReceiver filter b14c553
          Action: "androidx.work.impl.background.systemalarm.UpdateProxies"

Service Resolver Table:
  Non-Data Actions:
      android.accessibilityservice.AccessibilityService:
        5c52dd ace.jun.simplecontrol/.service.AccService filter cef2b51 permission android.permission.BIND_ACCESSIBILITY_SERVICE
          Action: "android.accessibilityservice.AccessibilityService"

Permissions:
  Permission [ace.jun.simplecontrol.DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION] (c7cce52):
    sourcePackage=ace.jun.simplecontrol
"""

FLOATING_DOCK_MOD_DUMPSYS = """\
        4e91111 com.joshepw.nexusfloatinghelper/.BootReceiver filter c4fdbb
          Action: "android.intent.action.BOOT_COMPLETED"
          Category: "android.intent.category.DEFAULT"
          mPriority=1000, mOrder=0, mHasPartialTypes=false

Service Resolver Table:
  Non-Data Actions:
      android.accessibilityservice.AccessibilityService:
        da2b4e4 com.joshepw.nexusfloatinghelper/.PoerNavAccessibilityService filter 9be83b5 permission android.permission.BIND_ACCESSIBILITY_SERVICE
          Action: "android.accessibilityservice.AccessibilityService"
"""


def test_finds_real_simple_control_service():
    assert adb_permissions._find_service_component("ace.jun.simplecontrol", SIMPLE_CONTROL_DUMPSYS, A11Y) \
        == "ace.jun.simplecontrol/ace.jun.simplecontrol.service.AccService"


def test_finds_real_floating_dock_mod_service():
    assert adb_permissions._find_service_component(
        "com.joshepw.nexusfloatinghelper", FLOATING_DOCK_MOD_DUMPSYS, A11Y) \
        == "com.joshepw.nexusfloatinghelper/com.joshepw.nexusfloatinghelper.PoerNavAccessibilityService"


def test_handles_crlf_from_adb_shell():
    """Старые adb/прошивки отдают вывод shell с \\r\\n."""
    assert adb_permissions._find_service_component(
        "ace.jun.simplecontrol", SIMPLE_CONTROL_DUMPSYS.replace("\n", "\r\n"), A11Y) \
        == "ace.jun.simplecontrol/ace.jun.simplecontrol.service.AccService"


def test_service_class_outside_package_namespace_kept_as_is():
    out = ("        1a2b3c com.example.app/org.lib.nav.NavService filter 4d5e6f "
           "permission android.permission.BIND_ACCESSIBILITY_SERVICE\n")
    assert adb_permissions._find_service_component("com.example.app", out, A11Y) \
        == "com.example.app/org.lib.nav.NavService"


def test_finds_notification_listener_same_way():
    out = ("Service Resolver Table:\n"
           "  Non-Data Actions:\n"
           "      android.service.notification.NotificationListenerService:\n"
           "        77aa11 com.example.nl/.NotifService filter 88bb22 "
           "permission android.permission.BIND_NOTIFICATION_LISTENER_SERVICE\n")
    assert adb_permissions._find_service_component("com.example.nl", out, "BIND_NOTIFICATION_LISTENER_SERVICE") \
        == "com.example.nl/com.example.nl.NotifService"


def test_app_without_accessibility_service_returns_none():
    """Оригинальный Floating Dock (joshepw, app-release 1.0.2) службы
    спецвозможностей не объявляет вовсе — в его dumpsys нет ни "Service
    Resolver Table", ни BIND_ACCESSIBILITY_SERVICE."""
    out = ("Activity Resolver Table:\n"
           "  Non-Data Actions:\n"
           "      android.intent.action.MAIN:\n"
           "        1f2e3d com.joshepw.nexusfloatinghelper/.MainActivity filter 4c5b6a\n")
    assert adb_permissions._find_service_component("com.joshepw.nexusfloatinghelper", out, A11Y) is None


def test_requested_permission_line_is_not_mistaken_for_service():
    """Некоторые приложения ошибочно просят BIND_ACCESSIBILITY_SERVICE как
    uses-permission — строка есть, но компонента в ней нет."""
    out = ("    requested permissions:\n"
           "      android.permission.INTERNET\n"
           "      android.permission.BIND_ACCESSIBILITY_SERVICE\n")
    assert adb_permissions._find_service_component("com.example.app", out, A11Y) is None


def test_old_name_lookup_still_works_as_fallback():
    out = "  Service name=.LegacyService\n  permission=android.permission.BIND_ACCESSIBILITY_SERVICE\n"
    assert adb_permissions._find_service_component("com.example.app", out, A11Y) \
        == "com.example.app/com.example.app.LegacyService"


def test_grant_all_permissions_enables_real_accessibility_service():
    """Сквозная проверка: по реальному dumpsys служба должна попасть в
    enabled_accessibility_services, а не в лог "не найдена в dumpsys"."""
    commands = []

    def shell(command, **kwargs):
        commands.append(command)
        if command.startswith("dumpsys package"):
            return SimpleNamespace(stdout=SIMPLE_CONTROL_DUMPSYS, stderr="", returncode=0)
        if command.startswith("settings get secure"):
            return SimpleNamespace(stdout="null\n", stderr="", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    log = []
    adb_permissions.grant_all_permissions(SimpleNamespace(log=log.append, shell=shell), "ace.jun.simplecontrol")
    assert ("settings put secure enabled_accessibility_services "
            "ace.jun.simplecontrol/ace.jun.simplecontrol.service.AccService") in commands
    assert "settings put secure accessibility_enabled 1" in commands
    assert "Служба специальных возможностей включена: " \
           "ace.jun.simplecontrol/ace.jun.simplecontrol.service.AccService" in log
    assert not any("не найдена в dumpsys (ace.jun.simplecontrol)" in line and "спецвозможностей" in line
                   for line in log)
