"""«Запустить приложение» — честный итог вместо безусловного «Готово.» (владелец, 2026-09-27: у техника на Geely
Preface «не срабатывает запуск приложений», а в журнале — «Готово.» по десять раз, лог #1348). ПК —
cars/_shared/adb_permissions.py:launch_main_activity; ответы магнитолы — как на эмуляторе Android 9 (monkey,
cmd package resolve-activity, am start, dumpsys activity activities). Android — tests/test_android_replug_and_scan.py."""
from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("adb_permissions_launch", ROOT / "cars/_shared/adb_permissions.py")
adb_permissions = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adb_permissions
spec.loader.exec_module(adb_permissions)

PKG = "com.salat.gbinder"
MONKEY = "monkey -p com.salat.gbinder -c android.intent.category.LAUNCHER 1"
RESOLVE = ("cmd package resolve-activity --brief -a android.intent.action.MAIN "
           "-c android.intent.category.LAUNCHER com.salat.gbinder")
SCREEN = "dumpsys activity activities | grep -E 'Display #|ResumedActivity'"
MONKEY_OK = 'args: [-p, com.salat.gbinder, -c, android.intent.category.LAUNCHER, 1]\nEvents injected: 1\n## Network stats: elapsed time=18ms'
MONKEY_NONE = 'data="com.salat.gbinder"\n** No activities found to run, monkey aborted.'
ON_MAIN = ("Display #0 (activities from top to bottom):\n"
           "    mResumedActivity: ActivityRecord{3ea7a39 u0 com.salat.gbinder/.MainActivity t61}\n"
           "  ResumedActivity: ActivityRecord{3ea7a39 u0 com.salat.gbinder/.MainActivity t61}")
LAUNCHER_ONLY = ("Display #0 (activities from top to bottom):\n"
                 "  ResumedActivity: ActivityRecord{1a2b u0 com.android.launcher3/.Launcher t2}")
ON_SECOND = ("Display #0 (activities from top to bottom):\n"
             "  ResumedActivity: ActivityRecord{1a2b u0 com.android.launcher3/.Launcher t2}\n"
             "Display #2 (activities from top to bottom):\n"
             "  ResumedActivity: ActivityRecord{9f u0 com.salat.gbinder/.MainActivity t70}")


def launch(responses):
    log, commands = [], []

    def shell(command, check=True):
        commands.append(command)
        return SimpleNamespace(stdout=responses.get(command, ""), stderr="")

    adb_permissions.launch_main_activity(SimpleNamespace(log=log.append, shell=shell, sleep=lambda s: None), PKG)
    return log, commands


def test_launched_and_on_screen():
    log, _ = launch({MONKEY: MONKEY_OK, SCREEN: ON_MAIN})
    assert log == ["Запускаю приложение: com.salat.gbinder", "Готово."]


def test_no_launcher_icon_is_said_plainly():
    # MicroG (app.revanced.android.gms) — значка для запуска нет, раньше всё равно было «Готово.».
    log, commands = launch({MONKEY: MONKEY_NONE})
    assert log[-1].startswith("Не удалось запустить: у приложения нет значка для запуска")
    assert commands == [MONKEY]


def test_monkey_missing_falls_back_to_am_start():
    log, commands = launch({MONKEY: "/system/bin/sh: monkey: inaccessible or not found",
                            RESOLVE: "priority=0 preferredOrder=0 match=0x108000\ncom.salat.gbinder/.MainActivity",
                            "am start -n com.salat.gbinder/.MainActivity": "Starting: Intent { cmp=com.salat.gbinder/.MainActivity }",
                            SCREEN: ON_MAIN})
    assert "am start -n com.salat.gbinder/.MainActivity" in commands
    assert log[-1] == "Готово."


def test_am_start_error_is_shown():
    log, _ = launch({MONKEY: "Error: something", RESOLVE: "com.salat.gbinder/.MainActivity",
                     "am start -n com.salat.gbinder/.MainActivity":
                         "Starting: Intent { cmp=com.salat.gbinder/.MainActivity }\nError type 3\n"
                         "Error: Activity class {com.salat.gbinder/com.salat.gbinder.MainActivity} does not exist."})
    assert log[-1] == ("Не удалось запустить: Error: Activity class {com.salat.gbinder/com.salat.gbinder.MainActivity} "
                       "does not exist.")


def test_nothing_to_start_shows_device_answer():
    log, _ = launch({MONKEY: "/system/bin/sh: monkey: inaccessible or not found", RESOLVE: "No activity found"})
    assert log[-1] == "Не удалось запустить: /system/bin/sh: monkey: inaccessible or not found"


def test_started_but_not_on_screen_says_what_is_there():
    log, _ = launch({MONKEY: MONKEY_OK, SCREEN: LAUNCHER_ONLY})
    assert log[-1].startswith("Команда запуска прошла, но через полторы секунды приложения на экране нет "
                              "(на экране: com.android.launcher3/.Launcher)")


def test_opened_on_additional_display():
    log, _ = launch({MONKEY: MONKEY_OK, SCREEN: ON_SECOND})
    assert log[-1] == ("Готово: приложение открылось на дополнительном экране магнитолы (экран 2), "
                       "а не на основном.")


def test_screen_unknown_keeps_old_behaviour():
    log, _ = launch({MONKEY: MONKEY_OK})
    assert log[-1] == "Готово."
