"""Telnet по IPv6 на ПК (cars/_shared/telnet_adb.py: scan_ipv6_neighbors) — магнитола находится сама (владелец 02.11.2026:
«телнет ipv6 тоже ищется сам»). В таблицу соседей попадают только те, с кем компьютер уже общался, поэтому сначала —
пинг всех узлов сети ff02::1 (на домашней сети: 1 запись до, 6 после). Свой адрес компьютера (запись permanent) раньше
был единственным «соседом» в списке — теперь его нет."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cars/_shared"))
telnet_adb = importlib.import_module("telnet_adb")

NDP = """Neighbor                                Linklayer Address  Netif Expire    St Flgs Prbs
fe80::1%lo0                             (incomplete)         lo0 permanent R
fe80::%en0                              (incomplete)         en0 expired   N
fe80::4bf:fd76:6c11:93d4%en0            5a:1e:c3:36:d6:a7    en0 11h24m45s S
fe80::18b1:15b4:54ea:ba6f%en0           c6:9d:23:9a:37:66    en0 permanent R
fe80::34a4:e9ff:fe1e:e27b%en0           36:a4:e9:1e:e2:7b    en0 23h59m53s S  R
fe80::aaaa%awdl0                        2:0:0:0:0:0        awdl0 1s        S
2a02:2168:9601:c500::1                  d4:d:ab:51:f4:a0     en0 22h23m13s S  R
"""


def test_mac_pings_all_nodes_first_and_skips_own_address(monkeypatch):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(stdout=NDP if cmd[0] == "ndp" else "", returncode=0)

    monkeypatch.setattr(telnet_adb.sys, "platform", "darwin")
    monkeypatch.setattr(telnet_adb, "_active_interface_name_mac", lambda: "en0")
    monkeypatch.setattr(telnet_adb.subprocess, "run", run)
    found = telnet_adb.scan_ipv6_neighbors()
    assert calls[0] == ["ping6", "-c", "2", "-i", "1", "ff02::1%en0"] and calls[1] == ["ndp", "-an"]
    assert found == [("fe80::4bf:fd76:6c11:93d4%en0", "5a:1e:c3:36:d6:a7"),
                     ("fe80::34a4:e9ff:fe1e:e27b%en0", "36:a4:e9:1e:e2:7b")]


def test_windows_pings_all_nodes_in_the_same_powershell_call():
    src = (ROOT / "cars/_shared/telnet_adb.py").read_text(encoding="utf-8")
    script = src[src.index("def scan_ipv6_neighbors("):]
    assert script.index('ping.exe -6 -n 2 -w 700 \\"ff02::1%$i\\"') < script.index("Get-NetNeighbor -AddressFamily IPv6")
