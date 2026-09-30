"""Ожидание готовности флешки после форматирования (лог №1865, Windows: первая запись сразу «[WinError 2] не
найден», вторая через минуту прошла — том не успел примонтироваться). _await_drive_ready ждёт, пока в корень
можно писать, но не срывает запись, если не дождалось."""
from __future__ import annotations
from pathlib import Path

from app.web.api import usb_api


def test_waits_until_writable_then_returns_true():
    ready_after = 3
    calls = {"probe": 0, "slept": 0.0}

    def is_ready(root):
        calls["probe"] += 1
        return calls["probe"] > ready_after

    def sleep(sec):
        calls["slept"] += sec

    logs = []
    assert usb_api._await_drive_ready(Path("E:/"), log=logs.append, delay=0.5, is_ready=is_ready, sleep=sleep) is True
    assert calls["probe"] == ready_after + 1  # пробовали, пока не получилось
    assert calls["slept"] == ready_after * 0.5
    assert logs and "готова к записи" in logs[0]


def test_ready_immediately_is_silent():
    logs = []
    assert usb_api._await_drive_ready(Path("E:/"), log=logs.append, is_ready=lambda r: True, sleep=lambda s: None) is True
    assert logs == []  # ждать не пришлось — журнал не засоряем


def test_never_ready_gives_up_but_does_not_block_write():
    logs = []
    slept = []
    ok = usb_api._await_drive_ready(Path("E:/"), log=logs.append, attempts=5, delay=0.5,
                                    is_ready=lambda r: False, sleep=slept.append)
    assert ok is False
    assert len(slept) == 5  # выждало все попытки
    assert logs and "не отвечает" in logs[-1]


def test_real_probe_writes_and_cleans_up(tmp_path):
    assert usb_api._drive_writable(tmp_path) is True
    assert not (tmp_path / ".magicsqd_ready").exists()  # проба за собой убрана
    missing = tmp_path / "нет-такой-папки"
    assert usb_api._drive_writable(missing) is False  # писать некуда — не готова
