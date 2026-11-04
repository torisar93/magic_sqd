"""Android: data_check заголовка ADB — сумма байтов данных, как у adb на ПК, а не CRC32 (разбор логов 04.11.2026).

Haval Jolion до 2026 (Harman, Android 8.1, SDK 27): разрешение на USB дано, «CNXN отправлен, жду ответ...» — и
тишина (лог №12975 и ещё 14 логов этой магнитолы на Android; с ПК та же магнитола подключалась). adbd Android 8.x
сверяет data_check у первого CNXN (версия протокола ещё 0x01000000) и молча выбрасывает пакет с неверной суммой;
Android 9+ поле не проверяет, поэтому остальные магнитолы подключались. Kotlin-тестов в проекте нет — проверяем
исходник и сам расчёт на эталоне."""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRANSPORT = ROOT / "android/app/src/main/java/ru/magicsqd/mobile/usb/UsbAdbTransport.kt"


def _code() -> str:
    text = TRANSPORT.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def _adb_data_check(payload: bytes) -> int:
    """Эталон — calculate_apacket_checksum из adb: сумма беззнаковых байтов."""
    return sum(payload) & 0xFFFFFFFF


def test_header_uses_byte_sum_not_crc32():
    code = _code()
    assert "CRC32" not in code
    build = re.search(r"private fun buildMessage\(.*?\n}", code, flags=re.S).group(0)
    assert "header.putInt(adbDataCheck(payload))" in build
    check = re.search(r"internal fun adbDataCheck\(.*?\n}", code, flags=re.S).group(0)
    assert "b.toInt() and 0xff" in check  # байт Kotlin знаковый — без маски сумма выйдет неверной


def test_reference_sum_for_cnxn_banner():
    # Ровно то, что шлёт performCnxnHandshake: "host:: " — и байт >127 (подпись AUTH), где знак важен.
    assert _adb_data_check(b"host:: ") == 594
    assert _adb_data_check(bytes([0xFF, 0x80, 0x01])) == 384
