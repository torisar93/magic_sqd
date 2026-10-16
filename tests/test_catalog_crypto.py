"""Крипто-ядро шифрования каталога (app/catalog_crypto.py). Проверяется тест-векторами RFC 8439,
round-trip, и побайтной идентичностью копии ПК/Android (как ed25519.py)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app import catalog_crypto as cc

ROOT = Path(__file__).resolve().parents[1]
ANDROID_COPY = ROOT / "android/app/src/main/python/catalog_crypto.py"


def test_desktop_and_android_copies_are_byte_identical():
    assert (ROOT / "app/catalog_crypto.py").read_bytes() == ANDROID_COPY.read_bytes()


def test_rfc8439_chacha20_block_vector():
    # RFC 8439 §2.3.2
    key = bytes(range(0x00, 0x20))
    nonce = bytes.fromhex("000000090000004a00000000")
    block = cc._chacha20_block(key, 1, nonce)
    assert block[:16].hex() == "10f1e7e4d13b5915500fdd1fa32071c4"


def test_rfc8439_aead_vector():
    # RFC 8439 §2.8.2 — проверяет и шифртекст, и тег целиком.
    key = bytes(range(0x80, 0xa0))
    nonce = bytes.fromhex("070000004041424344454647")
    aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
    plaintext = (b"Ladies and Gentlemen of the class of '99: If I could offer you only "
                 b"one tip for the future, sunscreen would be it.")
    ciphertext, tag = cc._aead_encrypt(key, nonce, plaintext, aad)
    assert ciphertext[:16].hex() == "d31a8d34648e60db7b86afbc53ef7ec2"
    assert tag.hex() == "1ae10b594f09e26a7e902ecbd0600691"
    assert cc._aead_decrypt(key, nonce, ciphertext, tag, aad) == plaintext


def test_rfc5869_hkdf_vector():
    # RFC 5869 Test Case 1 (SHA-256).
    ikm = bytes.fromhex("0b" * 22)
    salt = bytes.fromhex("000102030405060708090a0b0c")
    info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
    okm = cc.hkdf_sha256(ikm, info, 42, salt)
    assert okm.hex() == ("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
                         "34007208d5b887185865")


@pytest.mark.parametrize("size", [0, 1, 15, 16, 17, 63, 64, 65, 1000, 70000])
def test_encrypt_decrypt_round_trip(size):
    key = cc.derive_key(b"build-secret-xyz", bytes(range(32)))
    data = bytes((i * 7) % 256 for i in range(size))
    blob = cc.encrypt(key, data)
    assert cc.is_encrypted(blob)
    assert blob[:5] == b"MSQD1"
    assert cc.decrypt(key, blob) == data


def test_wrong_key_is_rejected():
    blob = cc.encrypt(cc.derive_key(b"s", b"a" * 32), b"instruction")
    with pytest.raises(ValueError, match="подлинност"):
        cc.decrypt(cc.derive_key(b"s", b"b" * 32), blob)


def test_tamper_is_rejected():
    key = cc.derive_key(b"s", b"a" * 32)
    blob = bytearray(cc.encrypt(key, b"instruction html here"))
    blob[20] ^= 1
    with pytest.raises(ValueError, match="подлинност"):
        cc.decrypt(key, bytes(blob))


def test_plaintext_is_not_flagged_as_encrypted():
    assert not cc.is_encrypted(b"<html>instruction</html>")
    assert not cc.is_encrypted(b"{}")
    assert not cc.is_encrypted(b"")


def test_derive_key_depends_on_both_inputs():
    base = cc.derive_key(b"secret", b"d" * 32)
    assert base != cc.derive_key(b"other", b"d" * 32)   # другой секрет сборки
    assert base != cc.derive_key(b"secret", b"e" * 32)  # другое устройство
    assert len(base) == 32


def test_nonce_is_random_each_time():
    key = cc.derive_key(b"s", b"a" * 32)
    assert cc.encrypt(key, b"x") != cc.encrypt(key, b"x")


# --- нативный шифр (владелец 16.10.2026: «на ПК долго открываются модели» — чистый Python ~0,8 с/МБ на M4,
# картинки инструкции расшифровываются при каждом открытии модели; у FreeTUGA это 7 МБ) ---

RFC_KEY = bytes(range(0x80, 0xa0))
RFC_NONCE = bytes.fromhex("070000004041424344454647")
RFC_AAD = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
RFC_TEXT = (b"Ladies and Gentlemen of the class of '99: If I could offer you only "
            b"one tip for the future, sunscreen would be it.")


@pytest.fixture
def reference_only(monkeypatch):
    """Как без нативного шифра (Android 8, запуск без пакета cryptography)."""
    monkeypatch.setattr(cc, "_native_backend", None)


def test_native_backend_on_desktop_matches_rfc8439():
    pytest.importorskip("cryptography")
    native = cc._native()
    assert native is not None
    sealed = native[0](RFC_KEY, RFC_NONCE, RFC_TEXT, RFC_AAD)
    assert sealed[:16].hex() == "d31a8d34648e60db7b86afbc53ef7ec2"
    assert sealed[-16:].hex() == "1ae10b594f09e26a7e902ecbd0600691"
    assert native[1](RFC_KEY, RFC_NONCE, sealed, RFC_AAD) == RFC_TEXT


def test_files_from_reference_and_native_are_interchangeable(monkeypatch):
    pytest.importorskip("cryptography")
    key = cc.derive_key(b"build-secret-xyz", bytes(range(32)))
    data = bytes((i * 13) % 256 for i in range(70001))
    native_blob = cc.encrypt(key, data)
    monkeypatch.setattr(cc, "_native_backend", None)
    reference_blob = cc.encrypt(key, data)
    assert cc.decrypt(key, native_blob) == data  # уже скачанное новой версией читает и запасной путь
    monkeypatch.undo()
    assert cc.decrypt(key, reference_blob) == data  # скачанное до исправления читается нативным


@pytest.mark.parametrize("size", [0, 1, 17, 70000])
def test_reference_only_round_trip(reference_only, size):
    key = cc.derive_key(b"s", b"a" * 32)
    data = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
    assert cc.decrypt(key, cc.encrypt(key, data)) == data
    with pytest.raises(ValueError, match="подлинност"):
        cc.decrypt(cc.derive_key(b"s", b"b" * 32), cc.encrypt(key, data))


def test_failing_native_library_falls_back_to_reference(monkeypatch):
    def broken(*_args):
        raise RuntimeError("сбой библиотеки")
    monkeypatch.setattr(cc, "_native_backend", (broken, broken))
    key = cc.derive_key(b"s", b"a" * 32)
    blob = cc.encrypt(key, b"instruction html")
    assert cc.decrypt(key, blob) == b"instruction html"
    with pytest.raises(ValueError, match="подлинност"):
        cc.decrypt(cc.derive_key(b"s", b"b" * 32), blob)


def test_native_backend_that_disagrees_with_reference_is_not_used(monkeypatch):
    garbage = (lambda key, nonce, data, aad: bytes(len(data) + 16), lambda key, nonce, data, aad: data[:-16])
    assert not cc._agrees_with_reference(garbage)

    def missing():
        raise ImportError("нет пакета")
    monkeypatch.setattr(cc, "_native_backend", cc._UNSET)
    monkeypatch.setattr(cc, "_cryptography_backend", lambda: garbage)
    monkeypatch.setattr(cc, "_java_backend", missing)
    assert cc._native() is None
    key = cc.derive_key(b"s", b"a" * 32)
    assert cc.decrypt(key, cc.encrypt(key, b"x" * 100)) == b"x" * 100


def test_instruction_photos_decrypt_fast():
    """Регресс: 7 МБ фото инструкции FreeTUGA расшифровывались ~5 с на M4 (на ПК техника — дольше)."""
    pytest.importorskip("cryptography")
    import time
    key = cc.derive_key(b"s", b"a" * 32)
    blob = cc.encrypt(key, bytes(7_000_000))
    started = time.perf_counter()
    assert len(cc.decrypt(key, blob)) == 7_000_000
    assert time.perf_counter() - started < 1.0
