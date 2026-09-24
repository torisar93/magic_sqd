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


def test_hung_jdwp_is_a_method_failure_and_cleanup_does_not_hide_it(tmp_path, monkeypatch):
    """Лог #799 (Haval Jolion 2026, v1.0.38): JDWP к system_server не ответил, а «adb forward --remove» в
    уборке висел 120 с — и его таймаут подменил настоящую причину («Команда не ответила за 120 сек»).
    Теперь: уборка коротко и без исключения, причина — сбой JDWP; таймаут сокета — отказ способа, а не
    падение всего этапа."""
    import socket
    import subprocess
    import threading

    import app.install_context as install_context
    import app.jdwp_whitelist as jdwp_whitelist
    from app.adb_utils import AdbError
    from app.install_context import InstallContext

    apk = tmp_path / "com.shere.assistivetouch.apk"
    apk.write_bytes(b"")
    log, adb_calls = [], []
    ctx = InstallContext("fake-adb", "1708353347534311", tmp_path, [apk], log.append, threading.Event(),
                         device_confirmed=True)
    ctx.shell = lambda command, **kwargs: subprocess.CompletedProcess(["adb"], 0, "797\n", "")

    class FakeAdb:
        def run(self, *args, **kwargs):
            adb_calls.append((args, kwargs.get("timeout")))
            if args[:2] == ("forward", "--remove"):
                raise AdbError("Команда не ответила за 15 сек: adb forward --remove tcp:56061")
            return subprocess.CompletedProcess(["adb"], 0, "56061\n", "")

    class FakeSocket:
        def settimeout(self, value):
            pass

        def close(self):
            pass

    def hang(client, packages, log=None):
        raise socket.timeout("timed out")

    ctx._adb = FakeAdb()
    monkeypatch.setattr(install_context, "read_package_name", lambda path: "com.shere.assistivetouch")
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: FakeSocket())
    monkeypatch.setattr(jdwp_whitelist, "patch_whitelist", hang)

    with pytest.raises(AdbError, match="JDWP-патч белого списка не удался: timed out"):
        ctx.install_apk_jdwp_whitelist(apk)
    remove = [call for call in adb_calls if call[0][:2] == ("forward", "--remove")]
    assert remove and remove[0][1] == 15  # не 120 с по умолчанию
    assert any("не удалось снять проброс порта 56061" in line for line in log)
