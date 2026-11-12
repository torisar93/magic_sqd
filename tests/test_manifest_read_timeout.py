"""ПК и Android: таймаут или обрыв на чтении manifest.json — «нет манифеста» (None), а не необработанная ошибка
(«TimeoutError: The read operation timed out», ПК 1.1.21, №15424; разбор 12.11.2026)."""
from __future__ import annotations

import http.client
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _android(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "android/app/src/main/python"))
    spec = importlib.util.spec_from_file_location("android_cs_timeout", ROOT / "android/app/src/main/python/content_sync.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Resp:
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        raise self.exc


@pytest.mark.parametrize("platform", ["pc", "android"])
@pytest.mark.parametrize("exc", [TimeoutError("The read operation timed out"), ConnectionResetError(),
                                 http.client.IncompleteRead(b"")])
def test_read_failure_means_no_manifest(platform, exc, monkeypatch):
    if platform == "pc":
        from app import content_sync as cs
    else:
        cs = _android(monkeypatch)
    monkeypatch.setattr(cs, "open_url", lambda url, timeout: Resp(exc))
    assert cs.fetch_manifest("https://magicsqd.ru/content") is None
