"""Две синхронизации одной папки (окно инструкции открылось, пока шла проверка обновлений) качают один и тот же файл.
Раньше обе писали в «картинка.png.part»: первая переименовывала его в итоговый, вторая — «[WinError 2] Не удается
найти указанный файл: '….png.part'» и «Не удалось проверить обновления инструкции» (№17583, ПК 1.1.25). Теперь у
каждого скачивания свой .part. ПК (app/content_sync.py) и Android (android/.../python/content_sync.py)."""
from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"
BODY = b"PNG-bytes"


class _Resp:
    """Ответ сервера: первое чтение ждёт, пока второе скачивание тоже откроет свой файл — гонка гарантирована."""

    def __init__(self, barrier):
        self.barrier, self.sent = barrier, False
        self.headers = {"Content-Length": str(len(BODY))}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        if self.sent:
            return b""
        self.barrier.wait(timeout=5)
        self.sent = True
        return BODY


def _race(module, monkeypatch, dest: Path):
    barrier = threading.Barrier(2)
    monkeypatch.setattr(module, "open_url", lambda url, timeout=60: _Resp(barrier))
    errors = []

    def one():
        try:
            module.download_file("https://example.invalid/content", "cars/A/B/files/instruction_2/images/x.png", dest)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=one) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return errors


def test_pc_parallel_downloads_of_one_file_both_succeed(tmp_path, monkeypatch):
    from app import content_sync
    dest = tmp_path / "images" / "x.png"
    assert _race(content_sync, monkeypatch, dest) == []
    assert dest.read_bytes() == BODY
    assert not list(dest.parent.glob("*.part"))


@pytest.fixture
def android_content_sync(monkeypatch):
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    sys.modules.pop("content_sync", None)
    module = importlib.import_module("content_sync")
    yield module
    sys.modules.pop("content_sync", None)


def test_android_parallel_downloads_of_one_file_both_succeed(tmp_path, monkeypatch, android_content_sync):
    dest = tmp_path / "images" / "x.png"
    assert _race(android_content_sync, monkeypatch, dest) == []
    assert dest.read_bytes() == BODY
    assert not list(dest.parent.glob("*.part"))
