"""«Работа в движении» (владелец, 2026-09-30: на Haval Dargo 2026 не работает видео в движении; рецепт с форума —
вписать в окно приложения <meta-data android:name="distractionOptimized" android:value="true"/>).

Магнитолы на Android Automotive в движении закрывают окна приложений, не помеченных как безопасные для водителя:
служба машины (CarPackageManagerService) при ограничениях UX пускает на экран только окна с этой пометкой — её читает
CarAppMetadataReader (ActivityInfo.metaData.getBoolean("distractionOptimized")). Здесь пометка вписывается во ВСЕ окна
приложения (activity и activity-alias: у видео-приложений плеер часто — отдельное окно) прямо в двоичный
AndroidManifest.xml (формат AXML), и APK собирается заново. Подпись после этого недействительна — APK переподписывают
(ПК: apk_signer.py, Android: ApkResign.kt), поэтому поверх того же приложения с родной подписью он не встанет.

Как собирается APK: все записи архива остаются на своих местах байт в байт (выравнивание несжатых файлов не
сбивается), новый манифест дописывается в конец, центральный каталог ссылается на него; старый блок подписи
отрезается. Переподпись (apksig) переписывает архив начисто и сохраняет выравнивание.

Файл ОДИНАКОВЫЙ на ПК (app/motion_patch.py) и Android (android/app/src/main/python/motion_patch.py) — правьте оба
сразу (tests/test_motion_patch.py сверяет копии)."""
from __future__ import annotations

import re
import struct
import zlib
from pathlib import Path

RES_STRING_POOL = 0x0001
RES_XML = 0x0003
RES_XML_START_NAMESPACE = 0x0100
RES_XML_START_ELEMENT = 0x0102
RES_XML_END_ELEMENT = 0x0103
RES_XML_RESOURCE_MAP = 0x0180
UTF8_FLAG = 0x100
SORTED_FLAG = 0x1
NO_ENTRY = 0xFFFFFFFF
TYPE_STRING = 0x03
TYPE_INT_BOOLEAN = 0x12
ATTR_NAME = 0x01010003   # android:name
ATTR_VALUE = 0x01010024  # android:value
ANDROID_NS = "http://schemas.android.com/apk/res/android"
META_NAME = "distractionOptimized"
WINDOWS = ("activity", "activity-alias")

_LFH = 0x04034B50
_CDH = 0x02014B50
_EOCD = b"PK\x05\x06"
_SIG_BLOCK_MAGIC = b"APK Sig Block 42"


class MotionPatchError(Exception):
    """APK не удалось пометить (нестандартный или защищённый манифест, битый архив) — ставить как есть."""


def _len8(n: int) -> bytes:
    if n > 0x7FFF:
        raise MotionPatchError("слишком длинная строка")
    return bytes([n]) if n < 0x80 else bytes([0x80 | (n >> 8), n & 0xFF])


def _len16(n: int) -> bytes:
    return struct.pack("<H", n) if n < 0x8000 else struct.pack("<HH", 0x8000 | (n >> 16), n & 0xFFFF)


class _StringPool:
    """Пул строк AXML. Старые строки не перекодируются — их байты остаются как были, новые дописываются в конец."""

    def __init__(self, chunk: bytes):
        if len(chunk) < 28:
            raise MotionPatchError("битый пул строк манифеста")
        _, header_size, size, count, style_count, flags, strings_start, styles_start = struct.unpack_from(
            "<HHIIIIII", chunk)
        if (size != len(chunk) or header_size < 28 or not count or header_size + 4 * (count + style_count) > size
                or not strings_start <= size or (style_count and not strings_start <= styles_start <= size)):
            raise MotionPatchError("битый пул строк манифеста")
        self.flags = flags
        self.utf8 = bool(flags & UTF8_FLAG)
        self.offsets = list(struct.unpack_from(f"<{count}I", chunk, header_size))
        self.style_offsets = list(struct.unpack_from(f"<{style_count}I", chunk, header_size + 4 * count))
        self.data = bytes(chunk[strings_start:styles_start if style_count else size])
        self.style_data = bytes(chunk[styles_start:size]) if style_count else b""
        self.added: list[str] = []
        self._decoded: dict[int, str] = {}

    def __len__(self) -> int:
        return len(self.offsets) + len(self.added)

    def get(self, index: int) -> str | None:
        if index < len(self.offsets):
            if index not in self._decoded:
                self._decoded[index] = self._decode(self.offsets[index])
            return self._decoded[index]
        index -= len(self.offsets)
        return self.added[index] if 0 <= index < len(self.added) else None

    def _decode(self, offset: int) -> str:
        data = self.data
        try:
            if self.utf8:
                pos = offset + (2 if data[offset] & 0x80 else 1)  # длина в символах UTF-16 — не нужна
                n = data[pos]
                if n & 0x80:
                    n, pos = ((n & 0x7F) << 8) | data[pos + 1], pos + 2
                else:
                    pos += 1
                return data[pos:pos + n].decode("utf-8", "replace")
            n, pos = struct.unpack_from("<H", data, offset)[0], offset + 2
            if n & 0x8000:
                n, pos = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, pos)[0], pos + 2
            return data[pos:pos + 2 * n].decode("utf-16-le", "replace")
        except (IndexError, struct.error) as exc:
            raise MotionPatchError("битая строка в манифесте") from exc

    def find(self, text: str) -> int | None:
        return next((i for i in range(len(self)) if self.get(i) == text), None)

    def add(self, text: str) -> int:
        self.added.append(text)
        return len(self) - 1

    def _encode(self, text: str) -> bytes:
        if self.utf8:
            raw = text.encode("utf-8")
            return _len8(len(text.encode("utf-16-le")) // 2) + _len8(len(raw)) + raw + b"\0"
        units = text.encode("utf-16-le")
        return _len16(len(units) // 2) + units + b"\0\0"

    def build(self) -> bytes:
        data = bytearray(self.data)
        offsets = list(self.offsets)
        for text in self.added:
            offsets.append(len(data))
            data += self._encode(text)
        data += b"\0" * (-len(data) % 4)
        count, style_count = len(offsets), len(self.style_offsets)
        strings_start = 28 + 4 * (count + style_count)
        styles_start = strings_start + len(data) if style_count else 0
        size = strings_start + len(data) + len(self.style_data)
        return (struct.pack("<HHIIIIII", RES_STRING_POOL, 28, size, count, style_count, self.flags & ~SORTED_FLAG,
                            strings_start, styles_start)
                + struct.pack(f"<{count}I", *offsets) + struct.pack(f"<{style_count}I", *self.style_offsets)
                + bytes(data) + self.style_data)


def _attr_index(pool: _StringPool, resmap: list[int], res_id: int, name: str) -> int:
    """Индекс строки-имени атрибута с этим ресурсным id. Нет — строка дописывается в конец пула, а карта ресурсов
    дополняется нулями до неё (0 — «у строки нет id», как у строк за концом карты)."""
    if res_id in resmap:
        return resmap.index(res_id)
    index = pool.add(name)
    resmap.extend([0] * (index - len(resmap)))
    resmap.append(res_id)
    return index


def _attributes(chunk: bytes):
    header_size = struct.unpack_from("<H", chunk, 2)[0]
    attr_start, attr_size, attr_count = struct.unpack_from("<HHH", chunk, header_size + 8)
    base = header_size + attr_start
    if attr_size < 20 or base + attr_size * attr_count > len(chunk):
        raise MotionPatchError("битый элемент манифеста")
    for k in range(attr_count):
        yield struct.unpack_from("<IIIHBBI", chunk, base + k * attr_size)


def _meta_state(chunk: bytes, pool: _StringPool, resmap: list[int]) -> bool | None:
    """<meta-data> окна: True — наша пометка уже стоит, False — пометка есть, но не «true» (заменим), None — чужая."""
    name, value = None, None
    for _ns, attr, raw, _size, _res0, dtype, data in _attributes(chunk):
        res_id = resmap[attr] if attr < len(resmap) else 0
        if res_id == ATTR_NAME:
            name = pool.get(data if dtype == TYPE_STRING else raw)
        elif res_id == ATTR_VALUE:
            value = (dtype, data)
    if name != META_NAME:
        return None
    return value is not None and value[0] == TYPE_INT_BOOLEAN and value[1] != 0


def _start_element(line: int, tag: int, attrs) -> bytes:
    body = b"".join(struct.pack("<IIIHBBI", ns, name, raw, 8, 0, dtype, data) for ns, name, raw, dtype, data in attrs)
    return (struct.pack("<HHIII", RES_XML_START_ELEMENT, 16, 36 + len(body), line, NO_ENTRY)
            + struct.pack("<IIHHHHHH", NO_ENTRY, tag, 20, 20, len(attrs), 0, 0, 0) + body)


def _end_element(line: int, tag: int) -> bytes:
    return struct.pack("<HHIIIII", RES_XML_END_ELEMENT, 16, 24, line, NO_ENTRY, NO_ENTRY, tag)


def count_optimized(axml: bytes) -> tuple[int, int]:
    """(окон приложения, из них помеченных distractionOptimized=true) — для проверки уже собранного манифеста."""
    _pool, _resmap, (_nodes, _ns, windows) = _walk(axml)
    return len(windows), sum(1 for element, _end in windows if element["marked"])


def _walk(axml: bytes):
    """Разбирает манифест → (pool, resmap, (android_ns, stack_ok, windows)). windows — [(окно, индекс END)]."""
    if len(axml) < 8:
        raise MotionPatchError("это не двоичный манифест")
    ctype, header_size, total = struct.unpack_from("<HHI", axml)
    if ctype != RES_XML or header_size < 8 or total > len(axml):
        raise MotionPatchError("это не двоичный манифест")
    pool = resmap = None
    nodes = []
    pos = header_size
    while pos + 8 <= total:
        ctype, chunk_header, size = struct.unpack_from("<HHI", axml, pos)
        if size < 8 or pos + size > total:
            raise MotionPatchError("битый манифест")
        chunk = axml[pos:pos + size]
        if ctype == RES_STRING_POOL and pool is None and not nodes:
            pool = _StringPool(chunk)
        elif ctype == RES_XML_RESOURCE_MAP and resmap is None and not nodes:
            resmap = list(struct.unpack_from(f"<{(size - chunk_header) // 4}I", chunk, chunk_header))
        else:
            nodes.append(chunk)
        pos += size
    if pool is None:
        raise MotionPatchError("в манифесте нет пула строк")
    resmap = resmap or []

    android_ns = None
    stack: list[dict] = []
    windows: list[tuple[dict, int]] = []
    for i, chunk in enumerate(nodes):
        ctype, chunk_header = struct.unpack_from("<HH", chunk)
        if ctype == RES_XML_START_NAMESPACE:
            uri = struct.unpack_from("<I", chunk, chunk_header + 4)[0]
            if pool.get(uri) == ANDROID_NS:
                android_ns = uri
        elif ctype == RES_XML_START_ELEMENT:
            element = {"tag": pool.get(struct.unpack_from("<I", chunk, chunk_header + 4)[0]), "start": i,
                       "marked": False, "stale": False, "drop": []}
            stack.append(element)
            if (element["tag"] == "meta-data" and len(stack) == 4 and stack[2]["tag"] in WINDOWS
                    and stack[1]["tag"] == "application" and stack[0]["tag"] == "manifest"):
                state = _meta_state(chunk, pool, resmap)
                if state:
                    stack[2]["marked"] = True
                elif state is False:
                    element["stale"] = True
        elif ctype == RES_XML_END_ELEMENT:
            if not stack:
                raise MotionPatchError("битое дерево манифеста")
            element = stack.pop()
            if element["stale"] and stack:
                stack[-1]["drop"].append((element["start"], i))
            if (element["tag"] in WINDOWS and len(stack) == 2 and stack[1]["tag"] == "application"
                    and stack[0]["tag"] == "manifest"):
                windows.append((element, i))
    if stack:
        raise MotionPatchError("битое дерево манифеста")
    return pool, resmap, (nodes, android_ns, windows)


def patch_manifest(axml: bytes) -> tuple[bytes, int]:
    """Двоичный AndroidManifest.xml → (новый, скольким окнам вписали пометку). 0 — менять нечего (окон нет или все
    уже помечены), тогда возвращается исходный."""
    pool, resmap, (nodes, android_ns, windows) = _walk(axml)
    todo = [(element, end) for element, end in windows if not element["marked"]]
    if not todo:
        return axml, 0
    if android_ns is None:
        raise MotionPatchError("в манифесте нет пространства имён android")
    name_attr = _attr_index(pool, resmap, ATTR_NAME, "name")
    value_attr = _attr_index(pool, resmap, ATTR_VALUE, "value")
    tag = pool.find("meta-data")
    tag = pool.add("meta-data") if tag is None else tag
    meta_name = pool.find(META_NAME)
    meta_name = pool.add(META_NAME) if meta_name is None else meta_name
    attrs = [(android_ns, name_attr, meta_name, TYPE_STRING, meta_name),
             (android_ns, value_attr, NO_ENTRY, TYPE_INT_BOOLEAN, 0xFFFFFFFF)]  # по возрастанию id, как у aapt

    drop, inserts = set(), {}
    for element, end in todo:
        for first, last in element["drop"]:
            drop.update(range(first, last + 1))
        line = struct.unpack_from("<I", nodes[end], 8)[0]
        inserts[end] = _start_element(line, tag, attrs) + _end_element(line, tag)
    body = bytearray(pool.build())
    body += struct.pack("<HHI", RES_XML_RESOURCE_MAP, 8, 8 + 4 * len(resmap)) + struct.pack(f"<{len(resmap)}I", *resmap)
    for i, chunk in enumerate(nodes):
        body += inserts.get(i, b"")
        if i not in drop:
            body += chunk
    return struct.pack("<HHI", RES_XML, 8, 8 + len(body)) + bytes(body), len(todo)


def _copy(src, dst, length: int) -> None:
    while length > 0:
        block = src.read(min(length, 1 << 20))
        if not block:
            raise MotionPatchError("APK обрезан")
        dst.write(block)
        length -= len(block)


def patch_apk(src, dst) -> int:
    """Пишет в dst APK с пометкой во всех окнах; возвращает, скольким окнам её вписали. 0 — менять нечего, dst не
    создаётся. Подпись dst недействительна — его нужно переподписать."""
    src, dst = Path(src), Path(dst)
    with open(src, "rb") as f:
        size = f.seek(0, 2)
        tail_len = min(size, 0xFFFF + 22)
        f.seek(size - tail_len)
        tail = f.read(tail_len)
        at = tail.rfind(_EOCD)
        if at < 0 or at + 22 > len(tail):
            raise MotionPatchError("APK — не zip-архив")
        _, disk, cd_disk, disk_entries, entries, cd_size, cd_offset, comment_len = struct.unpack_from(
            "<IHHHHIIH", tail, at)
        if disk or cd_disk or disk_entries != entries or cd_offset + cd_size > size - tail_len + at:
            raise MotionPatchError("многотомный или ZIP64-архив")
        comment = tail[at + 22:at + 22 + comment_len]
        f.seek(cd_offset)
        cd = f.read(cd_size)
        pos, found = 0, None
        for _ in range(entries):
            if pos + 46 > len(cd) or struct.unpack_from("<I", cd, pos)[0] != _CDH:
                raise MotionPatchError("битый каталог архива")
            name_len, extra_len, comment_len = struct.unpack_from("<HHH", cd, pos + 28)
            end = pos + 46 + name_len + extra_len + comment_len
            if cd[pos + 46:pos + 46 + name_len] == b"AndroidManifest.xml":
                found = (pos, end)
            pos = end
        if found is None:
            raise MotionPatchError("в APK нет AndroidManifest.xml")
        record = bytearray(cd[found[0]:found[1]])
        flags, method, mtime, mdate, crc, packed_size, plain_size = struct.unpack_from("<HHHHIII", record, 8)
        local = struct.unpack_from("<I", record, 42)[0]
        if flags & 0x1 or method not in (0, 8):
            raise MotionPatchError("манифест зашифрован или сжат неизвестным способом")
        f.seek(local)
        head = f.read(30)
        if len(head) < 30 or struct.unpack_from("<I", head)[0] != _LFH:
            raise MotionPatchError("битый архив")
        f.seek(local + 30 + sum(struct.unpack_from("<HH", head, 26)))
        raw = f.read(packed_size)
        try:
            manifest = zlib.decompress(raw, -15) if method == 8 else raw
        except zlib.error as exc:
            raise MotionPatchError("манифест не распаковывается") from exc
        if len(manifest) != plain_size or zlib.crc32(manifest) & 0xFFFFFFFF != crc:
            raise MotionPatchError("манифест повреждён")
        new_manifest, marked = patch_manifest(manifest)
        if not marked:
            return 0

        # Блок подписи APK (схемы v2+) лежит прямо перед центральным каталогом: [размер][пары][размер][магия].
        cut = cd_offset
        if cd_offset >= 32:
            f.seek(cd_offset - 24)
            probe = f.read(24)
            block = struct.unpack_from("<Q", probe)[0]
            if probe[8:] == _SIG_BLOCK_MAGIC and 32 <= block + 8 <= cd_offset:
                f.seek(cd_offset - block - 8)
                if struct.unpack_from("<Q", f.read(8))[0] == block:
                    cut = cd_offset - block - 8
        packer = zlib.compressobj(9, zlib.DEFLATED, -15)
        packed = packer.compress(new_manifest) + packer.flush()
        new_crc = zlib.crc32(new_manifest) & 0xFFFFFFFF
        name = b"AndroidManifest.xml"
        header = struct.pack("<IHHHHHIIIHH", _LFH, 20, 0, 8, mtime, mdate, new_crc, len(packed), len(new_manifest),
                             len(name), 0) + name
        struct.pack_into("<HH", record, 8, flags & ~0x0008, 8)  # без дескриптора данных — размеры в заголовке
        struct.pack_into("<III", record, 16, new_crc, len(packed), len(new_manifest))
        struct.pack_into("<I", record, 42, cut)
        new_cd = cd[:found[0]] + bytes(record) + cd[found[1]:]
        eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, entries, entries, len(new_cd),
                           cut + len(header) + len(packed), len(comment)) + comment
        dst.parent.mkdir(parents=True, exist_ok=True)
        part = dst.with_name(dst.name + ".part")
        with open(part, "wb") as out:
            f.seek(0)
            _copy(f, out, cut)
            out.write(header)
            out.write(packed)
            out.write(new_cd)
            out.write(eocd)
    part.replace(dst)
    return marked


# --------------------------------------------------------------------------------------------------------------------
# Приняла ли магнитола пометку — по «dumpsys car_service»: пакеты с разрешёнными в движении окнами служба машины
# показывает в разделе «**System white list**» (Android 9 — с пробелом; лог №2084, Haval Dargo: проверка его не узнавала),
# «**System whitelist**» (Android 10–11) или «**System allowlist**» (Android 12+).
# --------------------------------------------------------------------------------------------------------------------
_ALLOWLIST_HEADER = re.compile(r"^\s*\*\*\s*System (?:white|allow) ?list\s*\*\*", re.I | re.M)
_SECTION_HEADER = re.compile(r"^\s*(\*\*[^*\n]{1,60}\*\*)\s*$", re.M)


def car_service_verdict(dump: str, package: str) -> str:
    """"accepted" — пакет в списке разрешённых в движении, "rejected" — список есть, пакета в нём нет,
    "no_car_service" — Android Automotive на магнитоле нет, "unknown" — вывод не разобрать."""
    text = dump or ""
    if not text.strip() or re.search(r"can't find service|service not found", text, re.I):
        return "no_car_service"
    header = _ALLOWLIST_HEADER.search(text)
    if header is None:
        return "unknown"
    rest = text[header.end():]
    following = re.search(r"^\s*\*\*", rest, re.M)
    section = rest[:following.start()] if following else rest
    found = re.search(r"(?<![\w.])" + re.escape(package) + r"(?![\w.])", section)
    return "accepted" if found else "rejected"


def verdict_line(verdict: str, package: str, dump: str = "") -> str:
    """Строка журнала — одинаковая на ПК и Android (её знают правила разбора логов на сервере). Если вывод службы
    не разобрать — коротко, что она ответила (первая строка и заголовки разделов), чтобы узнать формат по логу."""
    line = {
        "accepted": f"Работа в движении: магнитола приняла пометку — окна {package} разрешены в движении.",
        "rejected": f"Работа в движении: магнитола не приняла пометку {package} — его нет в списке разрешённых "
                    "в движении (прошивка пускает только приложения из своего магазина или системные).",
        "no_car_service": "Работа в движении: службы машины Android Automotive на магнитоле нет — пометка ни на что "
                          "не влияет.",
    }.get(verdict)
    if line is not None:
        return line
    line = f"Работа в движении: не удалось проверить, приняла ли магнитола пометку {package}."
    first = next((s.strip() for s in (dump or "").splitlines() if s.strip()), "")
    if first:
        headers = _SECTION_HEADER.findall(dump)[:6]
        line += f" Служба машины ответила: «{first[:100]}»"
        line += f"; разделы: {', '.join(headers)}." if headers else "; разделов со списками нет."
    return line
