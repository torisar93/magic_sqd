"""Режим стелс (cars/_shared/stealth.py; владелец, 09.11.2026): скрыть выбранные сторонние приложения (pm disable-user),
запомнить их на магнитоле и вернуть всё одной кнопкой. Один код для ПК и Android (py_runner.py)."""
from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cars/_shared"))
stealth = importlib.import_module("stealth")

GSPLIT = "com.salat.gsplit/com.salat.gsplit.presentation.AutoLaunchAccessibilityService"
KEYS = "ru.magicsqd.wheelkeys/.KeysService$Inner"


class HeadUnit:
    """Магнитола: сторонние/штатные пакеты, отключённые, secure-настройки и файлы — команды shell разбираются как есть."""

    def __init__(self, third=(), system=("android", "com.android.settings"), disabled=(), secure=None, files=None,
                 refuse=(), writable=True):
        self.third = list(third)
        self.system = list(system)
        self.disabled = set(disabled)
        self.secure = dict(secure or {})
        self.files = dict(files or {})
        self.refuse = set(refuse)
        self.writable = writable
        self.commands: list[str] = []

    def run(self, command: str) -> str:
        self.commands.append(command)
        m = re.fullmatch(r"pm list packages ?(.*)", command)
        if m:
            flags = m.group(1).split()
            pkgs = self.third + self.system
            if "-3" in flags:
                pkgs = self.third
            if "-s" in flags:
                pkgs = self.system
            if "-d" in flags:
                pkgs = [p for p in pkgs if p in self.disabled]
            if "-e" in flags:
                pkgs = [p for p in pkgs if p not in self.disabled]
            return "".join(f"package:{p}\n" for p in pkgs)
        m = re.fullmatch(r"pm disable-user --user 0 (\S+)", command)
        if m:
            if m.group(1) in self.refuse:
                return "Error: java.lang.SecurityException: Shell cannot change component state"
            self.disabled.add(m.group(1))
            return f"Package {m.group(1)} new state: disabled-user"
        m = re.fullmatch(r"pm enable (\S+)", command)
        if m:
            if m.group(1) not in self.third + self.system:
                return f"Error: java.lang.IllegalArgumentException: Unknown package: {m.group(1)}"
            self.disabled.discard(m.group(1))
            return f"Package {m.group(1)} new state: enabled"
        m = re.fullmatch(r"settings get secure (\S+)", command)
        if m:
            return self.secure.get(m.group(1), "null")
        m = re.fullmatch(r"settings put secure (\S+) '?([^']*)'?", command)
        if m:
            self.secure[m.group(1)] = m.group(2)
            return ""
        m = re.fullmatch(r"cat (\S+) 2>/dev/null; echo msqd_end", command)
        if m:
            return self.files.get(m.group(1), "") + "msqd_end\n"
        m = re.fullmatch(r"printf '%s\\n' (.*) > (\S+) && echo msqd_saved", command)
        if m:
            if not self.writable:
                return "/system/bin/sh: can't create: Read-only file system"
            self.files[m.group(2)] = "".join(part + "\n" for part in re.findall(r"'([^']*)'", m.group(1)))
            return "msqd_saved"
        m = re.fullmatch(r"cmd notification allow_listener '(\S+)'", command)
        if m:
            self.listeners = getattr(self, "listeners", []) + [m.group(1)]
            return ""
        m = re.fullmatch(r"rm -f (\S+)", command)
        if m:
            self.files.pop(m.group(1), None)
            return ""
        if command.startswith("for f in /system/app/"):
            return ""
        raise AssertionError("неожиданная команда: " + command)


class Ctx:
    def __init__(self, unit: HeadUnit, answers=()):
        self.unit = unit
        self.answers = list(answers)
        self.asked: list = []
        self.logs: list = []

    def log(self, message):
        self.logs.append(message)

    def sleep(self, seconds):
        pass

    def shell(self, command, check=True, timeout=120):
        return SimpleNamespace(stdout=self.unit.run(command), stderr="", returncode=0)

    def ask_choice(self, prompt, choices, title="Выбор", allow_manual=True):
        self.asked.append((prompt, list(choices), title, allow_manual))
        return self.answers.pop(0)


def test_hide_with_checkbox_window_disables_saves_and_keeps_services():
    unit = HeadUnit(third=["com.salat.gsplit", "ru.yandex.music", "ru.magicsqd.wheelkeys"],
                    secure={"enabled_accessibility_services": f"{GSPLIT}:{KEYS}"})
    ctx = Ctx(unit, [json.dumps(["com.salat.gsplit", "ru.yandex.music"])])
    assert stealth.hide(ctx) == ["com.salat.gsplit", "ru.yandex.music"]
    prompt, choices, title, manual = ctx.asked[0]
    assert title == stealth.TITLE + stealth.MULTI_MARK and manual is False
    assert choices == ["com.salat.gsplit", "ru.magicsqd.wheelkeys", "ru.yandex.music"]
    assert unit.disabled == {"com.salat.gsplit", "ru.yandex.music"}
    assert stealth.read_state(ctx) == {"p": ["com.salat.gsplit", "ru.yandex.music"], "a": [GSPLIT], "n": []}
    assert "Режим стелс: скрыто 2" in ctx.logs[-1] and stealth.RESTORE_LABEL in ctx.logs[-1]


def test_state_is_written_before_anything_is_disabled():
    unit = HeadUnit(third=["a.b", "c.d"])
    ctx = Ctx(unit, ['["a.b"]'])
    stealth.hide(ctx)
    write = next(i for i, c in enumerate(unit.commands) if c.startswith("printf"))
    disable = next(i for i, c in enumerate(unit.commands) if c.startswith("pm disable-user"))
    assert write < disable


def test_old_interface_asks_one_by_one_until_done():
    unit = HeadUnit(third=["a.b", "c.d", "e.f"])
    ctx = Ctx(unit, ["c.d", "a.b", stealth.DONE_CHOICE])
    assert stealth.hide(ctx) == ["a.b", "c.d"]
    assert ctx.asked[1][1] == [stealth.DONE_CHOICE, "a.b", "e.f"]
    assert ctx.asked[1][2] == stealth.TITLE


def test_nothing_hidden_when_state_cannot_be_saved():
    unit = HeadUnit(third=["a.b"], writable=False)
    ctx = Ctx(unit, ['["a.b"]'])
    assert stealth.hide(ctx) == []
    assert unit.disabled == set()
    assert "ничего не скрываю" in ctx.logs[-1]


def test_refused_package_is_not_remembered():
    unit = HeadUnit(third=["a.b", "c.d"], refuse={"c.d"})
    ctx = Ctx(unit, ['["a.b", "c.d"]'])
    assert stealth.hide(ctx) == ["a.b"]
    assert stealth.read_state(ctx)["p"] == ["a.b"]
    assert any(m.startswith("Не удалось скрыть c.d") for m in ctx.logs)


def test_already_hidden_and_disabled_apps_are_not_offered_again():
    unit = HeadUnit(third=["a.b", "c.d", "e.f", "g.h"], disabled={"c.d", "g.h"},
                    files={stealth.STATE_FILE: "p c.d\n"})
    ctx = Ctx(unit, ['["a.b"]'])
    stealth.hide(ctx)
    assert ctx.asked[0][1] == ["a.b", "e.f"]
    assert stealth.read_state(ctx)["p"] == ["c.d", "a.b"]


def test_restore_enables_everything_brings_services_back_and_forgets_the_list():
    listener = "ru.yandex.music/.MediaListener"
    unit = HeadUnit(third=["com.salat.gsplit", "ru.yandex.music"], disabled={"com.salat.gsplit", "ru.yandex.music"},
                    secure={"enabled_accessibility_services": KEYS},
                    files={stealth.STATE_FILE: f"p com.salat.gsplit\np ru.yandex.music\np gone.app\na {GSPLIT}\n"
                                               f"n {listener}\n"})
    ctx = Ctx(unit)
    assert stealth.restore(ctx) == ["com.salat.gsplit", "ru.yandex.music"]
    assert unit.disabled == set()
    assert unit.secure["enabled_accessibility_services"] == f"{KEYS}:{GSPLIT}"
    assert unit.secure["accessibility_enabled"] == "1"
    # Служба вернувшегося приложения у Android «упавшая» — сбрасывается, только если уйдёт из списка: вписать → убрать →
    # вписать (эмулятор Android 15, 09.11.2026).
    writes = [c for c in unit.commands if c.startswith("settings put secure enabled_accessibility_services")]
    assert [w.split(" ", 4)[4] for w in writes] == [f"'{KEYS}:{GSPLIT}'", f"'{KEYS}'", f"'{KEYS}:{GSPLIT}'"]
    assert unit.secure["enabled_notification_listeners"] == listener and unit.listeners == [listener]
    assert stealth.STATE_FILE not in unit.files
    assert any("gone.app" in m and "Удалены" in m for m in ctx.logs)


def test_restore_keeps_what_did_not_come_back():
    unit = HeadUnit(third=["a.b"], disabled={"a.b"}, files={stealth.STATE_FILE: "p a.b\n"})
    unit.run = (lambda original: lambda c: "Error: closed" if c.startswith("pm enable") else original(c))(unit.run)
    ctx = Ctx(unit)
    assert stealth.restore(ctx) == []
    assert stealth.read_state(ctx)["p"] == ["a.b"]


def test_restore_without_list_does_not_touch_other_disabled_apps():
    unit = HeadUnit(third=["a.b"], disabled={"a.b"})
    ctx = Ctx(unit)
    assert stealth.restore(ctx) == []
    assert "a.b" in unit.disabled
    assert "Включить приложение" in ctx.logs[-1]


def test_no_answer_from_head_unit_is_an_error_not_an_empty_list():
    ctx = Ctx(HeadUnit())
    ctx.shell = lambda command, check=True, timeout=120: SimpleNamespace(stdout="", stderr="", returncode=-1)
    with pytest.raises(RuntimeError, match="не ответила"):
        stealth.restore(ctx)


def test_state_ignores_garbage_lines():
    unit = HeadUnit(files={stealth.STATE_FILE: "p a.b\np rm -rf /\nx y.z\na bad component\nn c.d/.Svc\n"})
    assert stealth.read_state(Ctx(unit)) == {"p": ["a.b"], "a": [], "n": ["c.d/.Svc"]}


# --- Android: тот же модуль через py_runner (Chaquopy) и окно с галочками в интерфейсе обеих платформ -------------------

from tests.test_py_runner import FakeBridge, TEST_SECRET, android  # noqa: E402,F401 — фикстура android
from app import code_signing as pc_code_signing  # noqa: E402


def test_android_py_runner_hides_with_checkbox_answer(android, tmp_path):
    shared = tmp_path / "_shared"
    shared.mkdir()
    for name in ("adb_permissions.py", "stealth.py"):
        data = (ROOT / "cars/_shared" / name).read_bytes()
        (shared / name).write_bytes(data)
        (shared / f"{name}.sig").write_text(pc_code_signing.sign(TEST_SECRET, name, data), encoding="ascii")
    bridge = FakeBridge({f"cat {stealth.STATE_FILE} 2>/dev/null; echo msqd_end": ("msqd_end", 0),
                         "pm list packages -3": ("package:a.b\npackage:c.d", 0),
                         "pm list packages -d": ("", 0),
                         "settings get secure": ("null", 0),
                         "printf '%s\\n' 'p a.b' > ": ("msqd_saved", 0),
                         "pm disable-user --user 0 a.b": ("Package a.b new state: disabled-user", 0)},
                        answers=['["a.b"]'])
    assert android.call(bridge, shared, "stealth", "hide", [])["ok"] is True
    prompt, title, choices, manual = bridge.asks[0]
    assert title == "Режим стелс⁣" and choices == ["a.b", "c.d"] and manual is False
    assert "pm disable-user --user 0 a.b" in bridge.commands
    assert "Режим стелс: скрыто 1 — a.b" in bridge.logs[-1]


def test_both_interfaces_know_the_checkbox_mark_and_answer_with_json():
    assert stealth.MULTI_MARK == "⁣"
    for path, show in (("android/app/src/main/assets/js/app.js", "function promptAskMany("),
                       ("app/web/frontend/js/screens/stage_wizard.js", "function showAskManyDialog(")):
        src = (ROOT / path).read_text(encoding="utf-8")
        assert 'const ASK_MANY_MARK = "\\u2063";' in src, path
        assert f'"{stealth.TITLE}": "Скрыть"' in src, path
        assert show in src and "JSON.stringify(choices.filter((c) => selected.has(c)))" in src, path
        assert 'dialog.addEventListener("cancel", (e) => { e.preventDefault(); done(' in src, path


def test_grant_disable_enable_take_a_checkbox_list():
    """«Выдать разрешения», «Отключить/Включить приложение» — окно с галочками отвечает JSON-списком; файлы моделей
    (install.py) передают его общим функциям как есть (владелец 09.11: «такой же список … для кнопок выдать права, вкл
    откл приложения»)."""
    adb_permissions = importlib.import_module("adb_permissions")
    unit = HeadUnit(third=["a.b", "c.d"])
    ctx = Ctx(unit)
    adb_permissions.disable_app(ctx, '["a.b", "c.d", "bad name;rm"]')
    assert unit.disabled == {"a.b", "c.d"}
    assert any("1 из 2: a.b" in m for m in ctx.logs) and "обработаны" in ctx.logs[-1]
    adb_permissions.enable_app(ctx, '["c.d"]')
    assert unit.disabled == {"a.b"}
    adb_permissions.disable_app(ctx, "c.d")  # одно имя — как раньше
    assert unit.disabled == {"a.b", "c.d"}
