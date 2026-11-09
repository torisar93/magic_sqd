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
import zlib
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
    """Атрибут package первого тега <manifest>. Как сам Android (ResXMLParser::getAttributeStringValue): строка берётся по
    rawValue, даже если тип значения сбит, — защищённые сборки (протекторы APK) так делают, и программа не читала имя
    пакета своих APK техника (Jolion 2026, JDWP-способ: strelka.apk, Car Launcher Pro, microG — №10697, №11055).
    Тип заголовка файла тоже не обязателен: важен пул строк сразу за ним."""
    file_type, _header_size, _size = struct.unpack_from("<HHI", data, 0)
    if file_type not in (_RES_XML_TYPE, 0):
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
                    at = first_attr + i * (attr_size or 20)
                    _ans, attr_name, raw_value, _vsize, _res0, value_type, value_data = struct.unpack_from("<IIIHBBI", data, at)
                    if attr_name < len(strings) and strings[attr_name] == "package":
                        if raw_value != _NO_INDEX and raw_value < len(strings):
                            return strings[raw_value] or None
                        if value_type == 0x03 and value_data < len(strings):
                            return strings[value_data] or None
                        return None
                return None  # первый же тег — <manifest>; атрибута package нет
        offset += chunk_size
    return None


_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")


def _guess_from_strings(data: bytes) -> str | None:
    """Последняя попытка, когда разбор тегов не прошёл: имя пакета — та строка пула, которая служит началом имён классов
    манифеста (activity/service «пакет.Класс»), а сама классом не является. Неверная догадка безвредна: способ
    установки с ней просто не сработает, как и без имени."""
    try:
        strings, _ = _read_string_pool(data, 8)
    except (struct.error, ValueError, IndexError):
        return None
    names = [s for s in strings if _PACKAGE_RE.match(s) and not s.startswith(("android.", "androidx."))]
    best, best_score = None, 0
    for candidate in names:
        if candidate[candidate.rfind(".") + 1:][:1].isupper():
            continue  # «пакет.Класс» — это класс
        score = sum(1 for other in names if other.startswith(candidate + "."))
        if score > best_score or (score == best_score and best and len(candidate) < len(best)):
            best, best_score = candidate, score
    return best if best_score else None


def _manifest_bytes(path: Path) -> bytes:
    """AndroidManifest.xml из архива. Протекторы APK ставят записи ложный флаг шифрования или нестандартный метод сжатия
    — Python отказывается читать («File is encrypted»), а Android их не замечает. Тогда читаем запись сами: локальный
    заголовок, затем stored или deflate."""
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("AndroidManifest.xml")
        try:
            return archive.read(info)
        except (RuntimeError, NotImplementedError, zipfile.BadZipFile, zlib.error):
            pass
    with open(path, "rb") as f:
        f.seek(info.header_offset)
        header = f.read(30)
        if header[:4] != b"PK\x03\x04":
            raise zipfile.BadZipFile("нет локального заголовка записи")
        name_len, extra_len = struct.unpack_from("<HH", header, 26)
        f.seek(info.header_offset + 30 + name_len + extra_len)
        raw = f.read(info.compress_size)
    if info.compress_type == zipfile.ZIP_STORED:
        return raw
    return zlib.decompressobj(-15).decompress(raw)


def read_package_name(apk_path) -> str | None:
    """Имя пакета из .apk или None, если прочитать не удалось."""
    try:
        data = _manifest_bytes(Path(apk_path))
    except (OSError, KeyError, zipfile.BadZipFile, zlib.error, struct.error):
        return None
    try:
        name = _package_from_binary_manifest(data)
    except (struct.error, ValueError, IndexError):
        name = None
    if name:
        return name
    # Не бинарный (редкая сборка с открытым XML) — обычный поиск в тексте.
    match = re.search(rb'package\s*=\s*"([A-Za-z0-9_.]+)"', data)
    if match:
        return match.group(1).decode("ascii")
    return _guess_from_strings(data)


def why_unreadable(apk_path) -> str:
    """Коротко, почему имя пакета не прочиталось — в журнал рядом с отказом (чтобы по логу было видно причину, а не
    только «не удалось прочитать»): нет файла, не zip, это набор APK (XAPK/APKS), нет манифеста, что в его начале."""
    path = Path(apk_path)
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except OSError as exc:
        return f"файл не открылся ({type(exc).__name__})"
    except zipfile.BadZipFile:
        return "это не zip-архив APK"
    if "AndroidManifest.xml" not in names:
        apks = [n for n in names if n.lower().endswith(".apk")]
        if apks:
            return f"это набор APK (XAPK/APKS: {', '.join(apks[:3])}), а не один APK"
        return f"в архиве нет AndroidManifest.xml ({len(names)} файлов)"
    try:
        head = _manifest_bytes(path)[:16]
    except Exception as exc:  # noqa: BLE001 — только для журнала
        return f"AndroidManifest.xml не читается ({type(exc).__name__}: {exc})"
    return f"манифест не разобран (начало: {head.hex()})"
