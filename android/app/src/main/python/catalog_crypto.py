"""AEAD-шифрование файлов каталога на устройстве техника (ChaCha20-Poly1305, RFC 8439).

Зачем: каталог моделей закрыт, но программа с открытым кодом. Скачанные файлы модели шифруются
на диске ключом, выводимым из секрета официальной сборки (его нет в исходниках) и случайного
секрета этой установки. Скопировать папку cars/ в пересобранную из исходников программу или на
другое устройство бесполезно — расшифровать нечем. См. app/content_sync.py (шифрует при
скачивании) и читателей файлов модели.

Файл ОДИНАКОВЫЙ на ПК (app/catalog_crypto.py) и на Android
(android/app/src/main/python/catalog_crypto.py) — побайтно, как ed25519.py/code_signing.py
(tests/test_catalog_crypto.py сверяет копии и гоняет тест-векторы RFC 8439).

Чистый Python без нативных зависимостей: Chaquopy сторонние пакеты не ставит, а в stdlib нет
AEAD. Файлы модели мелкие (инструкции, спеки) — скорость не важна; крупные payload (APK,
прошивки) не шифруются.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import struct

MAGIC = b"MSQD1"
_P1305 = (1 << 130) - 5
_CONST = b"expand 32-byte k"


def _rotl32(v: int, c: int) -> int:
    v &= 0xFFFFFFFF
    return ((v << c) | (v >> (32 - c))) & 0xFFFFFFFF


def _quarter(s: list, a: int, b: int, c: int, d: int) -> None:
    s[a] = (s[a] + s[b]) & 0xFFFFFFFF; s[d] = _rotl32(s[d] ^ s[a], 16)
    s[c] = (s[c] + s[d]) & 0xFFFFFFFF; s[b] = _rotl32(s[b] ^ s[c], 12)
    s[a] = (s[a] + s[b]) & 0xFFFFFFFF; s[d] = _rotl32(s[d] ^ s[a], 8)
    s[c] = (s[c] + s[d]) & 0xFFFFFFFF; s[b] = _rotl32(s[b] ^ s[c], 7)


def _chacha20_block(key: bytes, counter: int, nonce: bytes) -> bytes:
    state = list(struct.unpack("<4I", _CONST))
    state += list(struct.unpack("<8I", key))
    state.append(counter & 0xFFFFFFFF)
    state += list(struct.unpack("<3I", nonce))
    work = state[:]
    for _ in range(10):  # 20 раундов = 10 двойных
        _quarter(work, 0, 4, 8, 12); _quarter(work, 1, 5, 9, 13)
        _quarter(work, 2, 6, 10, 14); _quarter(work, 3, 7, 11, 15)
        _quarter(work, 0, 5, 10, 15); _quarter(work, 1, 6, 11, 12)
        _quarter(work, 2, 7, 8, 13); _quarter(work, 3, 4, 9, 14)
    out = [(work[i] + state[i]) & 0xFFFFFFFF for i in range(16)]
    return struct.pack("<16I", *out)


def _chacha20(key: bytes, counter: int, nonce: bytes, data: bytes) -> bytes:
    # XOR целыми блоками (int), а не побайтно: на крупных файлах (фото инструкций) это в разы
    # быстрее — чистый Python и так не быстрый, побайтный цикл был главным тормозом.
    out = bytearray()
    n = len(data)
    blocks = (n + 63) // 64
    for i in range(blocks):
        chunk = data[i * 64:(i + 1) * 64]
        ks = _chacha20_block(key, counter + i, nonce)[:len(chunk)]
        x = int.from_bytes(chunk, "little") ^ int.from_bytes(ks, "little")
        out += x.to_bytes(len(chunk), "little")
    return bytes(out)


def _poly1305(msg: bytes, key: bytes) -> bytes:
    r = int.from_bytes(key[:16], "little") & 0x0ffffffc0ffffffc0ffffffc0fffffff
    s = int.from_bytes(key[16:32], "little")
    acc = 0
    for i in range(0, len(msg), 16):
        block = msg[i:i + 16]
        acc = ((acc + int.from_bytes(block + b"\x01", "little")) * r) % _P1305
    acc = (acc + s) & ((1 << 128) - 1)
    return acc.to_bytes(16, "little")


def _pad16(data: bytes) -> bytes:
    return b"" if len(data) % 16 == 0 else b"\x00" * (16 - len(data) % 16)


def _mac_data(aad: bytes, ciphertext: bytes) -> bytes:
    return (aad + _pad16(aad) + ciphertext + _pad16(ciphertext)
            + struct.pack("<Q", len(aad)) + struct.pack("<Q", len(ciphertext)))


def _aead_encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes):
    otk = _chacha20_block(key, 0, nonce)[:32]
    ciphertext = _chacha20(key, 1, nonce, plaintext)
    return ciphertext, _poly1305(_mac_data(aad, ciphertext), otk)


def _aead_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, tag: bytes, aad: bytes) -> bytes:
    otk = _chacha20_block(key, 0, nonce)[:32]
    expected = _poly1305(_mac_data(aad, ciphertext), otk)
    if not hmac.compare_digest(expected, tag):
        raise ValueError("catalog_crypto: не прошла проверка подлинности (неверный ключ или повреждён файл)")
    return _chacha20(key, 1, nonce, ciphertext)


def encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
    """Зашифровать содержимое файла модели: MAGIC + nonce(12) + ciphertext + tag(16)."""
    if len(key) != 32:
        raise ValueError("ключ должен быть 32 байта")
    nonce = os.urandom(12)
    ciphertext, tag = _aead_encrypt(key, nonce, plaintext, aad)
    return MAGIC + nonce + ciphertext + tag


def is_encrypted(blob: bytes) -> bool:
    """True, если байты начинаются с MAGIC — файл зашифрован (читатели различают так плейнтекст и шифртекст)."""
    return blob[:len(MAGIC)] == MAGIC


def decrypt(key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
    if blob[:len(MAGIC)] != MAGIC:
        raise ValueError("не формат MSQD1")
    if len(blob) < len(MAGIC) + 12 + 16:
        raise ValueError("слишком короткий файл MSQD1")
    nonce = blob[len(MAGIC):len(MAGIC) + 12]
    tag = blob[-16:]
    ciphertext = blob[len(MAGIC) + 12:-16]
    return _aead_decrypt(key, nonce, ciphertext, tag, aad)


def hkdf_sha256(ikm: bytes, info: bytes, length: int = 32, salt: bytes = b"") -> bytes:
    if not salt:
        salt = b"\x00" * hashlib.sha256().digest_size
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm, t, counter = b"", b"", 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha256).digest()
        okm += t
        counter += 1
    return okm[:length]


def derive_key(build_secret: bytes, device_random: bytes, info: bytes = b"msqd-catalog-v1") -> bytes:
    """Ключ шифрования файлов: из секрета официальной сборки и случайного секрета установки."""
    return hkdf_sha256(build_secret + device_random, info, 32)
