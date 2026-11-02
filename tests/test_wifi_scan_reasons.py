"""Скан сети для «Подключить Wi-Fi» на ПК (adb_utils.scan_network_for_wifi_adb): кроме mDNS теперь отмечает, у кого открыт
порт ADB и какой адрес — шлюз сети (компьютер подключён к точке доступа магнитолы). Окно (LabUI.rankHosts) ставит
такой адрес первым с подписью «Скорее всего магнитола» — владелец 02.11.2026: «просто показывает все что в сети и не
подписывает что вот этот самый наиболее вероятный»."""
from __future__ import annotations

import ipaddress

from app import adb_utils


def _scan(monkeypatch, *, ping, open_hosts, gateway, endpoints=(), android_local=None):
    monkeypatch.setattr(adb_utils, "_local_ipv4_and_subnet",
                        lambda: ("192.168.43.100", ipaddress.ip_network("192.168.43.0/24")))
    monkeypatch.setattr(adb_utils, "_ping_sweep", lambda hosts: list(ping))
    monkeypatch.setattr(adb_utils, "scan_for_adb_hosts", lambda port: list(open_hosts))
    monkeypatch.setattr(adb_utils, "get_default_gateway_ip", lambda: gateway)
    monkeypatch.setattr(adb_utils.mdns_scan, "resolve_adb_tls_connect_endpoints", lambda: list(endpoints))
    monkeypatch.setattr(adb_utils.mdns_scan, "resolve_android_local", lambda: android_local)
    return adb_utils.scan_network_for_wifi_adb(5555)


def test_open_port_and_gateway_are_marked(monkeypatch):
    found = _scan(monkeypatch, ping=["192.168.43.1", "192.168.43.20", "192.168.43.30"],
                  open_hosts=["192.168.43.30"], gateway="192.168.43.1")
    assert found == [{"host": "192.168.43.1", "reasons": ["gateway"]}, "192.168.43.20",
                     {"host": "192.168.43.30", "reasons": ["open"]}]


def test_gateway_that_did_not_answer_ping_is_still_listed(monkeypatch):
    found = _scan(monkeypatch, ping=[], open_hosts=[], gateway="192.168.43.1")
    assert found == [{"host": "192.168.43.1", "reasons": ["gateway"]}]


def test_gateway_outside_the_scanned_subnet_and_own_address_are_ignored(monkeypatch):
    assert _scan(monkeypatch, ping=["192.168.43.5"], open_hosts=[], gateway="10.0.0.1") == ["192.168.43.5"]
    assert _scan(monkeypatch, ping=["192.168.43.5"], open_hosts=[], gateway="192.168.43.100") == ["192.168.43.5"]
    assert _scan(monkeypatch, ping=["192.168.43.5"], open_hosts=[], gateway=None) == ["192.168.43.5"]


def test_mdns_results_keep_their_place_and_collect_reasons(monkeypatch):
    found = _scan(monkeypatch, ping=["192.168.43.7"], open_hosts=["192.168.43.7"], gateway=None,
                  endpoints=[("192.168.43.9", 41234)], android_local="192.168.43.7")
    assert found == [{"host": "192.168.43.9", "port": 41234, "recommended": True, "reason": "adb"},
                     {"host": "192.168.43.7", "recommended": True, "reasons": ["mdns", "open"]}]
