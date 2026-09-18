"""Имя пакета (applicationId) прямо из .apk — без aapt и без обращения к
устройству. aapt на Windows в поставке программы нет (см. apk_icons._find_aapt
— только tools/aapt.exe при наличии, tools_mac/aapt на macOS), а сравнение
`pm list packages` до/после установки не работает при переустановке (пакет
уже был в списке). Поэтому читаем AndroidManifest.xml из zip и разбираем
бинарный XML Android (AXML): пул строк → первый START_TAG "manifest" → его
атрибут "package". Любая неожиданность → None (вызывающий сам решает, чем
подстраховаться)."""
from __future__ import annotations
import re
import struct
import zipfile
from pathlib import Path

_RES_XML_TYPE = 0x0003
_RES_STRING_POOL_TYPE = 0x0001
_RES_XML_START_ELEMENT_TYPE = 0x0102
_UTF8_FLAG = 0x100
_NO_INDEX = 0xFFFFFFFF


def _read_string_pool(data: bytes, offset: int) -> tuple[list[str], int]:
    """Возвращает (строки, смещение сразу за пулом)."""
    chunk_type, header_size, chunk_size = struct.unpack_from("<HHI", data, offset)
    if chunk_type != _RES_STRING_POOL_TYPE:
        raise ValueError("нет пула строк")
    string_count, _style_count, flags, strings_start = struct.unpack_from("<IIII", data, offset + 8)
    offsets = struct.unpack_from(f"<{string_count}I", data, offset + header_size)
    base = offset + strings_start
    utf8 = bool(flags & _UTF8_FLAG)
    strings: list[str] = []
    for item in offsets:
        pos = base + item
        if utf8:
            # два префикса длины (символы, потом байты); каждый — 1 или 2 байта
            first = data[pos]
            pos += 2 if first & 0x80 else 1
            length = data[pos]
            pos += 1
            if length & 0x80:
                length = ((length & 0x7F) << 8) | data[pos]
                pos += 1
            strings.append(data[pos:pos + length].decode("utf-8", "replace"))
        else:
            length = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            if length & 0x8000:
                length = ((length & 0x7FFF) << 16) | struct.unpack_from("<H", data, pos)[0]
                pos += 2
            strings.append(data[pos:pos + length * 2].decode("utf-16-le", "replace"))
    return strings, offset + chunk_size


def _package_from_binary_manifest(data: bytes) -> str | None:
    file_type, _header_size, _size = struct.unpack_from("<HHI", data, 0)
    if file_type != _RES_XML_TYPE:
        return None
    strings, offset = _read_string_pool(data, 8)
    while offset + 8 <= len(data):
        chunk_type, header_size, chunk_size = struct.unpack_from("<HHI", data, offset)
        if chunk_size < 8:
            return None
        if chunk_type == _RES_XML_START_ELEMENT_TYPE:
            body = offset + header_size
            _ns, name_idx, attr_start, attr_size, attr_count = struct.unpack_from("<IIHHH", data, body)
            if name_idx < len(strings) and strings[name_idx] == "manifest":
                first_attr = body + attr_start
                for i in range(attr_count):
                    at = first_attr + i * attr_size
                    _ans, attr_name, raw_value, _vsize, _res0, value_type, value_data = struct.unpack_from("<IIIHBBI", data, at)
                    if attr_name < len(strings) and strings[attr_name] == "package":
                        index = raw_value if raw_value != _NO_INDEX else value_data
                        if value_type == 0x03 and index < len(strings):
                            return strings[index] or None
                        return None
                return None  # первый же тег — <manifest>; атрибута package нет
        offset += chunk_size
    return None


def read_package_name(apk_path) -> str | None:
    """Имя пакета из .apk или None, если прочитать не удалось."""
    try:
        with zipfile.ZipFile(Path(apk_path)) as archive:
            data = archive.read("AndroidManifest.xml")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    try:
        name = _package_from_binary_manifest(data)
    except (struct.error, ValueError, IndexError):
        name = None
    if name:
        return name
    # Не бинарный (редкая сборка с открытым XML) — обычный поиск в тексте.
    match = re.search(rb'package\s*=\s*"([A-Za-z0-9_.]+)"', data)
    return match.group(1).decode("ascii") if match else None
