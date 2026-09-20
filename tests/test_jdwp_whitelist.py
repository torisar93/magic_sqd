"""app/jdwp_whitelist.py против мок-JDWP-сервера (tests/jdwp_mock.py) — проверяет точность wire-формата
JDWP без реальной магнитолы: клиент реально патчит mInstallWhiteList в модели system_server."""
from __future__ import annotations
import socket

import pytest

from app.jdwp_whitelist import JdwpClient, JdwpError, patch_whitelist
from jdwp_mock import MockJdwp, start_mock


def _run(mock: MockJdwp, packages, log=lambda m: None):
    host, port, thread = start_mock(mock)
    sock = socket.create_connection((host, port), timeout=5)
    try:
        patch_whitelist(JdwpClient(sock), packages, log=log)
    finally:
        sock.close()
    thread.join(5)


def test_appends_new_package_within_capacity():
    mock = MockJdwp(initial_whitelist=["com.oem.a"], capacity=4)  # есть место в массиве
    _run(mock, ["ru.magicsqd.target"])
    assert mock.current_whitelist() == ["com.oem.a", "ru.magicsqd.target"]


def test_grows_array_when_full():
    mock = MockJdwp(initial_whitelist=["com.oem.a", "com.oem.b"], capacity=2)  # массив забит
    _run(mock, ["ru.magicsqd.target"])
    assert mock.current_whitelist() == ["com.oem.a", "com.oem.b", "ru.magicsqd.target"]


def test_skips_already_present_package():
    mock = MockJdwp(initial_whitelist=["com.oem.a", "ru.magicsqd.target"], capacity=4)
    logs = []
    _run(mock, ["ru.magicsqd.target"], log=logs.append)
    assert mock.current_whitelist() == ["com.oem.a", "ru.magicsqd.target"]  # без дубля
    assert any("уже в белом списке" in line for line in logs)


def test_multiple_packages_at_once():
    mock = MockJdwp(initial_whitelist=["com.oem.a"], capacity=1)
    _run(mock, ["ru.magicsqd.one", "ru.magicsqd.two"])
    assert mock.current_whitelist() == ["com.oem.a", "ru.magicsqd.one", "ru.magicsqd.two"]


def test_reports_missing_pms_field():
    mock = MockJdwp()
    # переименуем поле — прошивка «не поддерживается»
    del mock.ref_fields[mock.pms_type]["mInstallWhiteList"]
    with pytest.raises(JdwpError, match="mInstallWhiteList"):
        _run(mock, ["ru.magicsqd.target"])
