"""Кнопка «Очистить кэш лаунчера» моделей Changan (cars/_shared/launcher_cache.py; владелец, 15.10.2026: «чистку
лаунчера кнопкой тоже обязательно добавь»): adb root, ожидание магнитолы после перезапуска adbd, кэш домашнего
лаунчера и iFlytek, перезапуск лаунчера. Один код для ПК и Android (py_runner.py)."""
from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cars/_shared"))
launcher_cache = importlib.import_module("launcher_cache")

from tests.test_py_runner import FakeBridge, TEST_SECRET, android, put  # noqa: E402,F401 — фикстура android


class Ctx:
    """Контекст как у ПК (InstallContext): ответы shell — по очереди; Exception в очереди — связь ещё не вернулась."""

    def __init__(self, root: str, replies):
        self.root = root
        self.replies = list(replies)
        self.calls: list = []
        self.logs: list = []

    def log(self, message):
        self.logs.append(message)

    def adb(self, *args, check=True, timeout=120):
        self.calls.append(("adb", *args))
        return SimpleNamespace(stdout=self.root, stderr="", returncode=0)

    def sleep(self, seconds):
        self.calls.append(("sleep", seconds))

    def wait_for_device(self, timeout=90):
        self.calls.append(("wait", timeout))

    def shell(self, command, check=True, timeout=120):
        self.calls.append(("shell", command))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(stdout=reply, stderr="", returncode=0)


def test_root_restart_waits_and_retries_until_the_head_unit_is_back():
    ctx = Ctx("restarting adbd as root\n", [ConnectionError("device offline"),
                                            "msqd_cleared:com.android.launcherWT\nmsqd_done\n"])
    assert launcher_cache.clear(ctx) == ["com.android.launcherWT"]
    kinds = [c[0] for c in ctx.calls]
    assert kinds == ["adb", "sleep", "wait", "shell", "sleep", "wait", "shell"]
    assert any("Лаунчер перезапущен" in m for m in ctx.logs)


def test_already_root_runs_at_once_and_iflytek_counted_once():
    out = "msqd_cleared:com.iflytek.autofly.launcher\nmsqd_cleared:com.iflytek.autofly.launcher\nmsqd_done\n"
    ctx = Ctx("adbd is already running as root\n", [out])
    assert launcher_cache.clear(ctx) == ["com.iflytek.autofly.launcher"]
    assert [c[0] for c in ctx.calls] == ["adb", "shell"]


def test_no_root_is_a_clear_error():
    ctx = Ctx("adbd cannot run as root in production builds\n", [])
    with pytest.raises(RuntimeError, match="не дала права root"):
        launcher_cache.clear(ctx)


def test_no_launcher_found_is_not_an_error():
    ctx = Ctx("adbd is already running as root\n", ["msqd_done\n"])
    assert launcher_cache.clear(ctx) == []
    assert any("не найден" in m for m in ctx.logs)


def test_script_cleans_home_launcher_and_iflytek_then_restarts_them():
    script = launcher_cache._SCRIPT
    assert "resolve-activity --brief" in script and "android.intent.category.HOME" in script
    assert "com.iflytek.autofly.launcher" in script and 'rm -rf "/data/data/$p/cache/"*' in script
    assert 'am force-stop "$p"' in script and script.endswith("echo msqd_done")


class FlakyBridge(FakeBridge):
    """Мост Android: после adb root первая команда не проходит (связь рвётся), потом магнитола на связи."""

    def __init__(self):
        super().__init__({"root:": ("restarting adbd as root\n", 0)})
        self.waits = 0
        self.shells = 0

    def shell(self, command, timeout_ms):
        self.shells += 1
        if self.shells == 1:
            self.commands.append(command)
            return "\x00fail:связь с магнитолой потеряна"
        self.replies = {command[: -len("\necho __MSQD_RC=$?")]: ("msqd_cleared:com.android.launcherWT\nmsqd_done", 0)}
        return super().shell(command, timeout_ms)

    def waitForDevice(self, timeout_ms):  # noqa: N802
        self.waits += 1
        return True


def test_same_code_on_android(android, tmp_path):
    shared = tmp_path / "_shared"
    put(shared, "launcher_cache.py", (ROOT / "cars/_shared/launcher_cache.py").read_text(encoding="utf-8"))
    bridge = FlakyBridge()
    result = android.call(bridge, shared, "launcher_cache", "clear")
    assert result == {"ok": True, "result": ["com.android.launcherWT"]}
    assert bridge.commands[0] == "root:" and bridge.waits == 2 and bridge.shells == 2
    assert any("Кэш лаунчера очищен" in m for m in bridge.logs)
