"""Подпись Ed25519 (RFC 8032) на чистом Python — для проверки бандлов интерфейса с сервера (ui_bundle.py).

Ключ подписи хранится только на компьютерах разработчика (scripts/publish_ui.py), на сервере его нет: даже с
доступом к серверу нельзя подсунуть программе свой интерфейс. Проверка идёт один раз на скачанный бандл, поэтому
скорость эталонной реализации из RFC 8032 (раздел 6) не важна, а сторонние пакеты (cryptography) не нужны ни в
сборке ПК, ни в Chaquopy на Android.

Файл ОДИНАКОВЫЙ на ПК (app/ed25519.py) и Android (android/app/src/main/python/ed25519.py) — tests/test_ui_bundle.py
сверяет копии и гоняет тестовые векторы RFC 8032."""
from __future__ import annotations

import hashlib

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _add(a, b):
    """Сложение точек в расширенных координатах (X, Y, Z, T)."""
    x1, y1, z1, t1 = a
    x2, y2, z2, t2 = b
    m = (y1 - x1) * (y2 - x2) % _P
    n = (y1 + x1) * (y2 + x2) % _P
    c = 2 * t1 * t2 * _D % _P
    dd = 2 * z1 * z2 % _P
    e, f, g, h = n - m, dd - c, dd + c, n + m
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(scalar: int, point):
    result = (0, 1, 1, 0)
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _equal(a, b) -> bool:
    x1, y1, z1, _ = a
    x2, y2, z2, _ = b
    return (x1 * z2 - x2 * z1) % _P == 0 and (y1 * z2 - y2 * z1) % _P == 0


def _recover_x(y: int, sign: int):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _compress(point) -> bytes:
    zinv = pow(point[2], _P - 2, _P)
    x, y = point[0] * zinv % _P, point[1] * zinv % _P
    return int.to_bytes(y | ((x & 1) << 255), 32, "little")


def _decompress(data: bytes):
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % _P)


def _expand(secret: bytes):
    if len(secret) != 32:
        raise ValueError("секретный ключ Ed25519 — 32 байта")
    digest = _sha512(secret)
    a = int.from_bytes(digest[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, digest[32:]


def public_key(secret: bytes) -> bytes:
    a, _ = _expand(secret)
    return _compress(_mul(a, _G))


def sign(secret: bytes, message: bytes) -> bytes:
    a, prefix = _expand(secret)
    public = _compress(_mul(a, _G))
    r = int.from_bytes(_sha512(prefix + message), "little") % _L
    r_point = _compress(_mul(r, _G))
    h = int.from_bytes(_sha512(r_point + public + message), "little") % _L
    s = (r + h * a) % _L
    return r_point + int.to_bytes(s, 32, "little")


def verify(public: bytes, message: bytes, signature: bytes) -> bool:
    """True — подпись верна. Любые неверные данные (длина, точка не на кривой, s вне диапазона) — False."""
    if not isinstance(public, (bytes, bytearray)) or not isinstance(signature, (bytes, bytearray)):
        return False
    if len(public) != 32 or len(signature) != 64:
        return False
    a_point = _decompress(bytes(public))
    r_point = _decompress(bytes(signature[:32]))
    if a_point is None or r_point is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(_sha512(bytes(signature[:32]) + bytes(public) + message), "little") % _L
    return _equal(_mul(s, _G), _add(r_point, _mul(h, a_point)))
