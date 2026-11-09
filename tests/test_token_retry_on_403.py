"""Сервер не принял токен (403) — один повтор с новым (ПК и Android, content_sync.open_url + AppToken.invalidate).
Разбор 09.11.2026, №10419 (Android 1.1.13): часы телефона после выдачи токена ушли назад, программа ещё считала его
годным — файл модели получил 403, повтора не было, техник увидел «Нет интернета». Сервер с 09.11 даёт час запаса."""
from __future__ import annotations

import importlib.util
import io
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Token:
    def __init__(self):
        self.value, self.invalidated = "old", 0

    def header(self):
        return {"X-App-Token": self.value}

    def invalidate(self):
        self.invalidated += 1
        self.value = "new"


@pytest.mark.parametrize("path", ["app/content_sync.py", "android/app/src/main/python/content_sync.py"])
def test_403_retries_once_with_a_fresh_token(path, monkeypatch):
    if path.startswith("android"):
        monkeypatch.syspath_prepend(str(ROOT / "android/app/src/main/python"))
        cs = _load("android_content_sync_t", ROOT / path)
    else:
        from app import content_sync as cs
    seen = []

    def urlopen(request, timeout):
        seen.append(request.get_header("X-app-token"))
        if request.get_header("X-app-token") == "old":
            raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(b""))
        return io.BytesIO(b"ok")

    token = Token()
    monkeypatch.setattr(cs, "_app_token", token)
    monkeypatch.setattr(cs.urllib.request, "urlopen", urlopen)
    assert cs.open_url("https://magicsqd.ru/content/x", 5).read() == b"ok"
    assert seen == ["old", "new"] and token.invalidated == 1

    # 403 и с новым токеном (например, закрытый каталог) — ошибка, без бесконечных повторов
    token.value = "old"
    token.invalidate = lambda: setattr(token, "value", "old")
    with pytest.raises(urllib.error.HTTPError):
        cs.open_url("https://magicsqd.ru/content/x", 5)


def test_app_token_invalidate_forces_a_new_token():
    from app import app_token
    token = app_token.AppToken("https://magicsqd.ru/content", b"secret", "cid")
    token._token, token._exp = "stale", 9e12
    assert token._valid()
    token.invalidate()
    assert not token._valid() and token._token == ""
