"""Клиентская часть токена официальной сборки (app/app_token.py): доказательство, кэш, обновление.
Проверка доказательства/выдача токена на сервере — server/tests (формат строк должен совпадать)."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app import app_token

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_and_android_copies_are_byte_identical():
    assert (ROOT / "app/app_token.py").read_bytes() == \
        (ROOT / "android/app/src/main/python/app_token.py").read_bytes()


def test_proof_is_deterministic_and_depends_on_inputs():
    s = b"build-secret"
    p = app_token.build_proof(s, "client-1", 1000)
    assert p == app_token.build_proof(s, "client-1", 1000)
    assert p != app_token.build_proof(b"other", "client-1", 1000)
    assert p != app_token.build_proof(s, "client-2", 1000)
    assert p != app_token.build_proof(s, "client-1", 1001)
    assert len(p) == 64  # hex sha256


def test_proof_freshness_window():
    now = 10_000
    assert app_token.proof_fresh(now, now)
    assert app_token.proof_fresh(now - app_token.PROOF_WINDOW, now)
    assert not app_token.proof_fresh(now - app_token.PROOF_WINDOW - 1, now)
    assert not app_token.proof_fresh(now + app_token.PROOF_WINDOW + 1, now)


def test_request_body_shape():
    body = json.loads(app_token.request_body(b"s", "cid", now=1234))
    assert body == {"client_id": "cid", "ts": 1234,
                    "proof": app_token.build_proof(b"s", "cid", 1234)}


def _fake_opener(responses, calls):
    def opener(req, timeout=0):
        calls.append(json.loads(req.data))

        class R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return json.dumps(responses.pop(0)).encode()
        return R()
    return opener


def test_token_fetched_once_then_cached():
    calls = []
    t = app_token.AppToken("https://x/content", b"secret", "cid")
    t._opener = _fake_opener([{"token": "TOK", "exp": time.time() + 3600}], calls)
    assert t.get() == "TOK"
    assert t.get() == "TOK"  # второй раз из кэша
    assert len(calls) == 1
    assert t.header() == {"X-App-Token": "TOK"}
    # сервер получил доказательство под нашим client_id
    assert calls[0]["client_id"] == "cid"
    assert calls[0]["proof"] == app_token.build_proof(b"secret", "cid", calls[0]["ts"])


def test_token_refreshed_when_near_expiry():
    calls = []
    t = app_token.AppToken("https://x/content", b"secret", "cid", refresh_margin=120)
    t._opener = _fake_opener(
        [{"token": "T1", "exp": time.time() + 60},     # истекает скоро → обновят
         {"token": "T2", "exp": time.time() + 3600}], calls)
    assert t.get() == "T1"
    assert t.get() == "T2"
    assert len(calls) == 2


def test_no_secret_means_no_token():
    t = app_token.AppToken("https://x/content", b"", "cid")
    assert t.get() == ""
    assert t.header() == {}


def test_network_error_returns_empty_not_raises():
    def boom(req, timeout=0):
        raise OSError("нет сети")
    t = app_token.AppToken("https://x/content", b"secret", "cid")
    t._opener = boom
    assert t.get() == ""  # старый сервер/нет сети — запрос пойдёт без токена, не падаем
    assert t.header() == {}


def test_token_url_derived_from_content_base():
    t = app_token.AppToken("https://magicsqd.ru/content", b"s", "c")
    assert t._token_url == "https://magicsqd.ru/auth/app-token"
