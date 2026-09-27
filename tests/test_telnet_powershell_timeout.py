"""ПК, этап «Включить кнопку ADB (Telnet, IPv6)»: PowerShell (Get-NetNeighbor) не уложился в 15 с, и этап падал
с сырым «Ошибка установки: Command '[…powershell.exe …]' timed out after 15 seconds» (лог #1310, 1.0.43).
Теперь поиск соседей без ответа — ручной ввод адреса, как когда соседей не нашлось; адаптер не определился —
понятный текст. cars/_shared/telnet_adb.py — серверный контент, подгружается по прямому пути."""
from __future__ import annotations
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SHARED = Path(__file__).resolve().parents[1] / "cars/_shared"


@pytest.fixture()
def telnet(monkeypatch):
    monkeypatch.syspath_prepend(str(SHARED))
    spec = importlib.util.spec_from_file_location("telnet_adb_test", SHARED / "telnet_adb.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.sys, "platform", "win32")

    def hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs.get("timeout"))

    monkeypatch.setattr(module.subprocess, "run", hang)
    return module


def test_neighbor_scan_timeout_falls_back_to_manual_address(telnet, monkeypatch):
    assert telnet.scan_ipv6_neighbors() is None
    log, asked = [], []
    ctx = SimpleNamespace(log=log.append, ask_input=lambda prompt, title="": asked.append(prompt) or "",
                          ask_choice=lambda *a, **k: pytest.fail("списка нет — только ручной ввод"))
    with pytest.raises(RuntimeError, match="Не указан IPv6-адрес"):
        telnet.enable_adb_via_telnet(ctx)  # техник ничего не ввёл
    assert log == ["Поиск устройств в сети не ответил вовремя — введите IPv6-адрес магнитолы вручную."]
    assert asked and "вручную" in asked[0]


def test_interface_index_timeout_is_a_clear_message(telnet):
    with pytest.raises(RuntimeError, match="PowerShell не ответил вовремя") as err:
        telnet.get_active_interface_index()
    assert "timed out" not in str(err.value)
