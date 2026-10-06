"""scripts/publish_shared.py — подпись и выкладка cars/_shared/*.py для Android (py_runner.py): выкладывается только
изменённое, подпись детерминированная (повторный запуск ничего не меняет), --keep-server-code подписывает версию с
сервера, а не из рабочей папки."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

import pytest

from app import code_signing, ed25519

ROOT = Path(__file__).resolve().parents[1]
TEST_SECRET = bytes(range(32))


@pytest.fixture(scope="module")
def publish_shared():
    spec = importlib.util.spec_from_file_location("publish_shared", ROOT / "scripts/publish_shared.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_plan_uploads_only_what_differs(publish_shared):
    local = {"a.py": b"A = 1\n", "b.py": b"B = 2\n", "c.py": b"C = 3\n"}
    sig_b = code_signing.sign(TEST_SECRET, "b.py", local["b.py"]).encode("ascii")
    remote = {"a.py": sha(b"A = 0\n"), "b.py": sha(local["b.py"]), "b.py.sig": sha(sig_b)}
    actions = publish_shared.plan(local, remote, TEST_SECRET, keep_server_code=False)
    names = [(name, why) for name, _, why in actions]
    assert names == [("a.py", "модуль изменён"), ("a.py.sig", "подпись"), ("c.py", "новый модуль"),
                     ("c.py.sig", "подпись")]
    signatures = {name: data for name, data, _ in actions if name.endswith(".sig")}
    public = ed25519.public_key(TEST_SECRET)
    assert code_signing.verify("a.py", local["a.py"], signatures["a.py.sig"].decode(), public)
    # Всё уже выложено — повторный запуск ничего не делает.
    remote.update({"a.py": sha(local["a.py"]), "c.py": sha(local["c.py"]),
                   "a.py.sig": sha(signatures["a.py.sig"]), "c.py.sig": sha(signatures["c.py.sig"])})
    assert publish_shared.plan(local, remote, TEST_SECRET, keep_server_code=False) == []


def test_keep_server_code_signs_server_version(publish_shared):
    local = {"a.py": b"A = 1\n", "b.py": b"B = 2\n"}
    server_a = b"A = 'server'\n"
    remote = {"a.py": sha(server_a)}
    actions = publish_shared.plan(local, remote, TEST_SECRET, keep_server_code=True, fetch=lambda name: server_a)
    assert [name for name, _, _ in actions] == ["a.py.sig"]  # модуль не трогаем, b.py на сервере нет — пропуск
    public = ed25519.public_key(TEST_SECRET)
    assert code_signing.verify("a.py", server_a, actions[0][1].decode(), public)
