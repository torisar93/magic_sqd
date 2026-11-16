"""Этап «Включить кнопку ADB (Telnet, IPv6)»: адрес, введённый техником, проверяется до подключения (ПК 1.1.25, №17286:
введён MAC «02:00:00:00:00:00», к нему дописалась зона, и было сырое «[Errno 11001] getaddrinfo failed»)."""
from __future__ import annotations
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

SHARED = Path(__file__).resolve().parents[1] / "cars/_shared"


@pytest.fixture()
def telnet(monkeypatch):
    monkeypatch.syspath_prepend(str(SHARED))
    spec = importlib.util.spec_from_file_location("telnet_adb_addr_test", SHARED / "telnet_adb.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "get_active_interface_index", lambda: "16")
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    connected = []

    class Sock:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def settimeout(self, t):
            pass

        def sendall(self, data):
            pass

        def recv(self, n):
            return b""

        def setblocking(self, flag):
            pass

    def connect(addr, timeout=None):
        connected.append(addr)
        return Sock()
    monkeypatch.setattr(module.socket, "create_connection", connect)
    monkeypatch.setattr(module, "_drain", lambda sock: None)
    module.connected = connected
    return module


def _ctx():
    return SimpleNamespace(log=lambda m: None)


@pytest.mark.parametrize("mac", ["02:00:00:00:00:00", "A4-5E-60-1B-2C-3D"])
def test_mac_address_is_named_not_getaddrinfo(telnet, mac):
    with pytest.raises(RuntimeError, match="Введён MAC-адрес .* нужен IPv6-адрес магнитолы"):
        telnet.enable_adb_via_telnet(_ctx(), mac)
    assert telnet.connected == []


def test_garbage_is_not_an_address(telnet):
    with pytest.raises(RuntimeError, match="не адрес. Нужен IPv6-адрес"):
        telnet.enable_adb_via_telnet(_ctx(), "android.local")
    assert telnet.connected == []


def test_ipv6_gets_the_zone_and_ipv4_does_not(telnet):
    telnet.enable_adb_via_telnet(_ctx(), "fe80::1a2b:3cff:fe4d:5e6f")
    telnet.enable_adb_via_telnet(_ctx(), "[fe80::1%en0]")
    telnet.enable_adb_via_telnet(_ctx(), "192.168.43.1")
    assert telnet.connected == [("fe80::1a2b:3cff:fe4d:5e6f%16", 23), ("fe80::1%en0", 23), ("192.168.43.1", 23)]
