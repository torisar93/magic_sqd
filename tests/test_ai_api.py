"""ИИ-мастер на ПК: app/web/api/ai_api.py — запросы к серверу (ключ, cookie входа, токен сборки, кто прислал) и
команды ИИ на магнитоле (политика ai_policy.py ещё раз, во время установки — отказ). JS-сторона —
tests/js/ai_master.test.js и tests/js/ai_panel.test.js."""
from __future__ import annotations

import types

import pytest

from app import ai_client
from app.web.api import ai_api
from app.web.api.ai_api import AiApi


class Install:
    def __init__(self, busy=False):
        self._busy = busy

    def busy(self):
        return self._busy


@pytest.fixture
def api(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(ai_api, "get_submit_config", lambda base: types.SimpleNamespace(
        chat_url="https://example.test/chat", submit_key="KEY"))
    monkeypatch.setattr(ai_api, "get_or_create_client_id", lambda base: "CID")
    monkeypatch.setattr(ai_api.content_sync, "app_token_header", lambda: {"X-App-Token": "TOK"})

    def fake_call(chat_url, path, submit_key, cookie="", headers=None, payload=None, timeout=50):
        calls.append({"url": chat_url, "path": path, "key": submit_key, "cookie": cookie, "headers": headers,
                      "payload": payload})
        if payload and payload.get("session") == "offline":
            raise ai_client.AiNetworkError("нет сети")
        return {"status": "ok"}

    monkeypatch.setattr(ai_api.ai_client, "call", fake_call)
    auth = types.SimpleNamespace(user_cookie="sess=1")
    return AiApi(tmp_path, "adb", auth, Install(), "macos"), calls


def test_status_start_turn_carry_credentials(api):
    a, calls = api
    assert a.call("status") == {"status": "ok"}
    assert calls[0]["path"] == "status" and calls[0]["payload"] is None
    assert calls[0]["cookie"] == "sess=1" and calls[0]["headers"] == {"X-App-Token": "TOK"} and calls[0]["key"] == "KEY"
    a.call("start", {"model_key": "Haval/H3", "outline": []})
    assert calls[1]["payload"] == {"model_key": "Haval/H3", "outline": [], "client_id": "CID", "platform": "macos",
                                   "app_version": ai_api.APP_VERSION}
    a.call("turn", {"session": "S", "req": "1", "input": [], "state": {}})
    assert calls[2]["payload"]["session"] == "S" and "client_id" not in calls[2]["payload"]


def test_offline_unknown_and_unconfigured(api, monkeypatch):
    a, calls = api
    assert a.call("turn", {"session": "offline"}) == {"network_error": "нет сети"}
    assert a.call("admin")["status"] == "error" and len(calls) == 1
    monkeypatch.setattr(ai_api, "get_submit_config", lambda base: None)
    assert a.call("status") == {"status": "unavailable"}


class FakeAdb:
    runs = []

    def __init__(self, adb_path, serial):
        self.serial = serial

    def shell(self, command, check=True, timeout=120):
        FakeAdb.runs.append((self.serial, command, timeout))
        if command.startswith("getprop"):
            return types.SimpleNamespace(stdout="28\n", stderr="", returncode=0)
        return types.SimpleNamespace(stdout="x" * (ai_api.OUTPUT_LIMIT + 10), stderr="", returncode=0)


@pytest.fixture
def adb(monkeypatch):
    FakeAdb.runs = []
    monkeypatch.setattr(ai_api, "Adb", FakeAdb)
    monkeypatch.setattr(ai_api, "list_devices", lambda path: [{"serial": "usb:1", "state": "device"},
                                                              {"serial": "usb:2", "state": "unauthorized"}])
    return FakeAdb.runs


def test_readonly_runs_by_itself_on_the_only_device(api, adb):
    a, _ = api
    assert a.shell(None, "shell  getprop ro.build.version.sdk", "auto") == {"ok": True, "output": "28"}
    assert adb == [("usb:1", "getprop ro.build.version.sdk", ai_api.SHELL_TIMEOUT_SECONDS)]
    out = a.shell("serial-x", "ls /sdcard", "auto")
    assert adb[-1][0] == "serial-x" and out["output"].endswith(f"всего {ai_api.OUTPUT_LIMIT + 10} символов]")


def test_policy_refuses_before_adb(api, adb):
    a, _ = api
    assert a.shell(None, "settings put global x 1", "auto")["ok"] is False  # меняет — только после «Выполнить»
    assert a.shell(None, "kill-server", "confirm")["ok"] is False
    assert a.shell(None, "getprop", "auto", busy=True)["ok"] is False
    a._install = Install(busy=True)
    assert a.shell(None, "getprop", "auto")["ok"] is False
    assert all(cmd != "kill-server" and not cmd.startswith("settings") for _, cmd, _ in adb)


def test_no_device(api, monkeypatch):
    a, _ = api
    monkeypatch.setattr(ai_api, "list_devices", lambda path: [])
    out = a.shell(None, "getprop", "auto")
    assert out["ok"] is False and "не подключена" in out["output"]
