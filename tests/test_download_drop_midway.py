"""Обрыв посреди скачивания файла (таймаут чтения, сброс соединения, IncompleteRead) — это «Не удалось скачать X»,
а не необработанное исключение: ПК 1.1.25, №17173 — «Скачиваю YK_MonjaroMOD…», 7 минут, «Ошибка установки: The read
operation timed out», упал весь этап. ПК (app/content_sync.py) и Android (android/.../python/content_sync.py)."""
from __future__ import annotations

import http.client
import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"


class _Dropping:
    def __init__(self, error):
        self.error, self.first = error, True
        self.headers = {"Content-Length": "100"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        if self.first:
            self.first = False
            return b"x" * 10
        raise self.error


ERRORS = [TimeoutError("The read operation timed out"), ConnectionResetError(54, "Connection reset by peer"),
          http.client.IncompleteRead(b"", 90)]


@pytest.fixture
def android_content_sync(monkeypatch):
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    sys.modules.pop("content_sync", None)
    module = importlib.import_module("content_sync")
    yield module
    sys.modules.pop("content_sync", None)


def _check(module, monkeypatch, tmp_path, error):
    monkeypatch.setattr(module, "open_url", lambda url, timeout=60: _Dropping(error))
    dest = tmp_path / "apk" / "Big.apk"
    with pytest.raises(module.ContentSyncError, match="связь оборвалась") as info:
        module.download_file("https://example.invalid/content", "apk/Big.apk", dest)
    assert str(error) in str(info.value)  # «The read operation timed out» — по нему окно «Нет интернета»
    assert not dest.exists() and not list(dest.parent.glob("*.part"))


@pytest.mark.parametrize("error", ERRORS, ids=["timeout", "reset", "incomplete"])
def test_pc_drop_midway_is_a_download_failure(tmp_path, monkeypatch, error):
    from app import content_sync
    _check(content_sync, monkeypatch, tmp_path, error)


@pytest.mark.parametrize("error", ERRORS, ids=["timeout", "reset", "incomplete"])
def test_android_drop_midway_is_a_download_failure(tmp_path, monkeypatch, error, android_content_sync):
    _check(android_content_sync, monkeypatch, tmp_path, error)


def test_cancel_still_stops_the_download(tmp_path, monkeypatch):
    from app import content_sync

    class Cancelled(Exception):
        pass

    def cancel():
        raise Cancelled()
    monkeypatch.setattr(content_sync, "open_url", lambda url, timeout=60: _Dropping(TimeoutError("x")))
    with pytest.raises(Cancelled):
        content_sync.download_file("https://example.invalid/content", "apk/Big.apk", tmp_path / "Big.apk",
                                   check_cancelled=cancel)
