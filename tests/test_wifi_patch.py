"""«Вернуть Wi-Fi / ДХО / Arkamys» (владелец, 2026-10-06; ГУ Desay SV NV8020/18 — Omoda C5 / Tiggo 4 New и т.п.).
wifi_patch правит dex точечно и одной длиной (force-true у методов-гейтов CarConfigInfoClient + снятие guard у
WifiReposity.setWifiEnabled) и переписывает dex «на месте» в APK, сохраняя 4-байтовое выравнивание несжатых
classes*.dex. Полная сверка (патч настоящего SystemUI/Settings совпал байт-в-байт с результатом androguard и после
подписи остался выровнен) делалась вручную на живых файлах прошивки — сюда настоящие dex не кладём (десятки МБ),
проверяем байт-примитивы на синтетике и сверяем копии ПК/Android."""
from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

from app import wifi_patch

ROOT = Path(__file__).resolve().parents[1]


def test_pc_and_android_copies_match():
    pc = (ROOT / "app/wifi_patch.py").read_bytes()
    android = (ROOT / "android/app/src/main/python/wifi_patch.py").read_bytes()
    assert pc == android, "копии wifi_patch.py на ПК и Android разошлись — правьте обе сразу"


def _code_item(insns: bytes) -> bytearray:
    """Минимальный code_item: заголовок 16 байт (registers=1), insns_size в словах, затем код."""
    assert len(insns) % 2 == 0
    return bytearray(struct.pack("<HHHHII", 1, 0, 0, 0, 0, len(insns) // 2) + insns)


def test_force_true_short_body():
    # const/4 v0,0 ; return v0  ->  const/4 v0,1 ; return v0
    ba = _code_item(bytes((0x12, 0x00, 0x0F, 0x00)))
    wifi_patch._force_true(ba, 0)
    assert bytes(ba[16:20]) == bytes((0x12, 0x10, 0x0F, 0x00))


def test_force_true_long_body_is_padded_with_nops():
    # «сложный» метод: 6 слов кода — первым делом return true, хвост обнуляется (NOP)
    body = bytes((0x54, 0x40, 0xAA, 0xBB)) + bytes(8)  # iget-object + ещё 4 слова-заглушки
    ba = _code_item(body)
    wifi_patch._force_true(ba, 0)
    assert bytes(ba[16:20]) == bytes((0x12, 0x10, 0x0F, 0x00))
    assert bytes(ba[20:16 + len(body)]) == b"\x00" * (len(body) - 4)


def test_nop_wifi_guard_removes_if_return():
    # лог-инструкция, затем if-eqz v4,+3 (38 04 03 00) и return-void (0E 00), затем реальный вызов
    insns = bytes((0x12, 0x00)) + bytes((0x38, 0x04, 0x03, 0x00, 0x0E, 0x00)) + bytes((0x0E, 0x00))
    ba = _code_item(insns)
    assert wifi_patch._nop_wifi_guard(ba, 0) is True
    assert bytes(ba[18:24]) == b"\x00" * 6     # guard затёрт
    assert bytes(ba[16:18]) == bytes((0x12, 0x00))  # код до него не тронут


def test_nop_wifi_guard_absent_returns_false():
    ba = _code_item(bytes((0x12, 0x00, 0x0F, 0x00)))
    assert wifi_patch._nop_wifi_guard(ba, 0) is False


def test_fix_dex_hashes_matches_spec():
    ba = bytearray(b"dex\n035\0" + b"\x00" * 56 + b"payload-bytes-to-cover" * 4)
    wifi_patch._fix_dex_hashes(ba)
    assert ba[12:32] == hashlib.sha1(bytes(ba[32:])).digest()
    assert ba[8:12] == struct.pack("<I", zlib.adler32(bytes(ba[12:])) & 0xFFFFFFFF)


def test_uleb128_reads_multibyte():
    assert wifi_patch._uleb128(b"\x00", 0) == (0, 1)
    assert wifi_patch._uleb128(b"\x7f", 0) == (0x7F, 1)
    assert wifi_patch._uleb128(b"\x80\x01", 0) == (0x80, 2)
    assert wifi_patch._uleb128(b"\xff\x7f", 0) == (0x3FFF, 2)
