"""Доступ к каталогу по региону — сторона программы (app/access_gate.py, с 1.1.4; правило — server/access_policy.py):
спросить сервер, запомнить решение для работы без интернета, не закрывать каталог, если ни разу не проверяли."""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

from app import access_gate

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://magicsqd.test/content"


def test_desktop_and_android_copies_are_byte_identical():
    assert (ROOT / "app/access_gate.py").read_bytes() == (ROOT / "android/app/src/main/python/access_gate.py").read_bytes()


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _fresh():
    access_gate.invalidate()
    yield
    access_gate.invalidate()


def _server(monkeypatch, answers):
    calls = []

    def fake_open(url, timeout):
        calls.append(url)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return _Resp(json.dumps(answer).encode("utf-8"))

    monkeypatch.setattr(access_gate.content_sync, "open_url", fake_open)
    return calls


def test_server_decision_is_returned_and_remembered(monkeypatch, tmp_path):
    calls = _server(monkeypatch, [{"ok": True, "allowed": False, "access": "subscription_required",
                                   "boosty": "https://boosty.to/magic_sqd"}])
    state = access_gate.check(tmp_path, BASE)
    assert calls == ["https://magicsqd.test/auth/access"]
    assert state["allowed"] is False and state["access"] == "subscription_required" and not state["offline"]
    assert access_gate.load(tmp_path)["allowed"] is False


def test_without_network_the_last_decision_applies(monkeypatch, tmp_path):
    _server(monkeypatch, [{"ok": True, "allowed": False, "access": "subscription_required", "boosty": ""},
                          urllib.error.URLError("нет сети")])
    access_gate.check(tmp_path, BASE)
    access_gate.invalidate()
    state = access_gate.check(tmp_path, BASE)
    assert state["allowed"] is False and state["offline"] is True


def test_never_checked_and_offline_is_open(monkeypatch, tmp_path):
    _server(monkeypatch, [urllib.error.URLError("нет сети")])
    state = access_gate.check(tmp_path, BASE)
    assert state["allowed"] is True and state["access"] == "unknown" and state["offline"]


def test_server_refusal_without_build_token_does_not_close(monkeypatch, tmp_path):
    _server(monkeypatch, [urllib.error.HTTPError(BASE, 403, "Forbidden", {}, None)])
    assert access_gate.check(tmp_path, BASE)["allowed"] is True


def test_recent_answer_is_reused_until_invalidated(monkeypatch, tmp_path):
    calls = _server(monkeypatch, [{"ok": True, "allowed": True, "access": "free"},
                                  {"ok": True, "allowed": True, "access": "subscriber"}])
    assert access_gate.check(tmp_path, BASE)["access"] == "free"
    assert access_gate.check(tmp_path, BASE)["access"] == "free" and len(calls) == 1
    access_gate.invalidate()
    assert access_gate.check(tmp_path, BASE)["access"] == "subscriber" and len(calls) == 2


def test_startup_sync_skips_catalog_when_subscription_is_needed(monkeypatch, tmp_path):
    from app.web.api import sync_api
    closed = {"allowed": False, "access": "subscription_required", "boosty": "", "offline": False}
    monkeypatch.setattr(sync_api.access_gate, "check", lambda base_dir, base_url: closed)
    monkeypatch.setattr(sync_api, "get_base_url", lambda base_dir: BASE)
    fetched = []
    monkeypatch.setattr(sync_api, "fetch_manifest", lambda base_url: fetched.append(base_url))
    api = sync_api.SyncApi.__new__(sync_api.SyncApi)
    api.base_dir = tmp_path
    api._remove_demo_car = lambda: None
    api._remove_stale_merged_models = lambda: None
    api._start_heartbeat = lambda: None
    pushed = []
    monkeypatch.setattr(sync_api.event_bridge, "push", pushed.append)
    result = api.startup_sync()
    assert result == {"changes": [], "access": closed} and fetched == []
    assert pushed == [{"kind": "access_state", "state": closed}]


def test_android_passes_system_signals_and_gates_sync():
    kotlin = (ROOT / "android/app/src/main/java/ru/magicsqd/mobile/WebBridge.kt").read_text(encoding="utf-8")
    assert "ClientEnv.collect(context).toString())" in kotlin  # признаки системы — в catalog_setup.configure
    sync = kotlin[kotlin.index("private fun startSync()"):]
    sync = sync[:sync.index("private fun startUpdateCheck")]
    assert sync.index("accessState(false)") < sync.index('"sync_cars"')  # сначала доступ, потом каталог
    assert '"access_check" -> { accessCheckAsync(); "{}" }' in kotlin
    setup = (ROOT / "android/app/src/main/python/catalog_setup.py").read_text(encoding="utf-8")
    assert "env_json: str = \"\"" in setup and "env_provider=" in setup
    gate_js = (ROOT / "android/app/src/main/assets/js/access_gate.js").read_text(encoding="utf-8")
    assert 'source !== "sync"' in gate_js  # после входа каталог не синхронизируется дважды
