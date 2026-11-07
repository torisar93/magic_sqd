"""Политика shell-команд ИИ-мастера в программе (app/ai_policy.py = Android-копия) — та же, что на сервере
(server/ai_shell_policy.json, server/ai_master.classify_command): программа — вторая линия защиты."""
import importlib.util
import json

import pytest

from conftest import ROOT

ANDROID = ROOT / "android/app/src/main/python/ai_policy.py"
SERVER = ROOT / "server"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def client():
    return _load(ROOT / "app/ai_policy.py", "ai_policy_client")


@pytest.fixture(scope="module")
def server_policy():
    if not (SERVER / "ai_shell_policy.json").exists():
        pytest.skip("нет server/ (вложенный репозиторий сервера)")
    return json.loads((SERVER / "ai_shell_policy.json").read_text(encoding="utf-8"))


def test_android_copy_is_identical():
    assert ANDROID.read_bytes() == (ROOT / "app/ai_policy.py").read_bytes()


def test_policy_matches_server(client, server_policy):
    expected = dict(server_policy)
    expected.pop("about", None)
    assert client.POLICY == expected


COMMANDS = ["getprop ro.build.version.sdk", "pm list packages -3", "dumpsys package com.x.y", "ls -l /sdcard | grep apk",
            "settings get global adb_enabled", "settings put global adb_enabled 1", "pm uninstall com.x",
            "echo 1 > /sdcard/x", "logcat -c", "find /sdcard -delete", "kill-server", "shell remount", "rm -rf /",
            "reboot recovery", "dd if=/dev/zero of=/dev/block/sda", "ping -c 1 ya.ru", "", "shell getprop"]


def test_same_verdicts_as_server(client, server_policy, monkeypatch):
    monkeypatch.syspath_prepend(str(SERVER))
    import ai_master  # noqa: PLC0415 — серверный модуль, путь добавлен выше
    for cmd in COMMANDS:
        assert client.classify_command(cmd)[0] == ai_master.classify_command(cmd)[0], cmd


def test_may_run(client):
    assert client.may_run("getprop", "auto", False) == (True, "")
    assert client.may_run("svc wifi disable", "auto", False)[0] is False  # меняющую сама не выполняет
    assert client.may_run("svc wifi disable", "confirm", False) == (True, "")  # после «Выполнить» — можно
    assert client.may_run("kill-server", "confirm", False)[0] is False  # adb уровня компьютера — никогда
    assert client.may_run("getprop", "auto", True)[0] is False  # во время установки — нет
    assert client.normalize("  shell   pm   list packages ") == "pm list packages"




def _function(source: str, name: str) -> str:
    import re
    return re.search(rf"\ndef {name}\(.*?(?=\n\n\n|\Z)", source, flags=re.S).group(0)


def test_split_parts_is_the_same_as_on_server():
    # С 1.1.22 программа делит команду с учётом кавычек — сервер для неё делает так же (ai_master.QUOTED_PIPES_SINCE).
    if not (SERVER / "ai_master.py").exists():
        pytest.skip("нет server/")
    server = (SERVER / "ai_master.py").read_text(encoding="utf-8")
    client_src = (ROOT / "app/ai_policy.py").read_text(encoding="utf-8")
    body = lambda src: _function(src, "split_parts").split('"""', 2)[2]  # без докстринга
    assert body(client_src) == body(server)
    assert "QUOTED_PIPES_SINCE = (1, 1, 22)" in server


@pytest.mark.parametrize("cmd, kind", [
    ('logcat -d | grep -E "gbinder|accessibility"', "readonly"), ("pm list packages | grep -iE 'yandex|navi'", "readonly"),
    ("echo 'x", "confirm"), ('ls "a|b" | sh', "confirm"), ("cat x && settings put global a 1", "confirm"),
    ("getprop ro.build.version.sdk", "readonly"), ("kill-server", "forbidden"),
])
def test_pipes_inside_quotes(client, cmd, kind):
    assert client.classify_command(cmd)[0] == kind
