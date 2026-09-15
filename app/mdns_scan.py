"""Мини mDNS-клиент (RFC 6762/6763) для поиска магнитолы в локальной сети —
без сторонних зависимостей, только stdlib socket. Тот же протокол и та же
логика разбора пакетов, что и в родственной Android-версии программы (см.
android/app/src/main/java/ru/magicsqd/mobile/usb/MdnsResolve.kt) — это
Python-порт под desktop (Windows/macOS), сделан отдельно, а не через
NsdManager/аналог, потому что тут его просто нет.

Используется из app/adb_utils.py (кнопка «Подключить Wi-Fi» под логом
главного окна, не привязанная к конкретной модели). Независимая копия
cars/_shared/mdns_scan.py — тот модуль подгружается отдельно из
cars/_shared при установке конкретной модели, без доступа к app/ (см.
app/adb_utils.py:find_powershell_path — та же idea для дублирования)."""
from __future__ import annotations
import socket
import struct
import time

_MDNS_PORT = 5353
_MDNS_ADDR_V4 = "224.0.0.251"
_MDNS_ADDR_V6 = "ff02::fb"
_TYPE_A = 1
_TYPE_PTR = 12
_TYPE_AAAA = 28
_TYPE_SRV = 33
ANDROID_LOCAL = "android.local"
# Стандартное DNS-SD имя службы "Беспроводной отладки" (Android 11+,
# frameworks/base AdbDebuggingManager) — см. resolve_adb_tls_connect_endpoints.
ADB_TLS_CONNECT_SERVICE = "_adb-tls-connect._tcp.local"


def _encode_name(name: str) -> bytes:
    out = bytearray()
    for label in name.split("."):
        if not label:
            continue
        out.append(len(label))
        out += label.encode("ascii")
    out.append(0)
    return bytes(out)


def _build_query(name: str, qtype: int, qu_bit: bool) -> bytes:
    """qu_bit — unicast-response (RFC 6762 §5.4): просим ответчика прислать
    ответ НАМ напрямую, без вступления в multicast-группу ради приёма —
    годится для одноразового резолва (android.local), но не для сервисного
    PTR-запроса, где отвечать может несколько устройств (adb-tls-connect)."""
    header = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0)
    question = _encode_name(name) + struct.pack(">HH", qtype, 0x8001 if qu_bit else 0x0001)
    return header + question


def _u16(buf: bytes, offset: int) -> int:
    return struct.unpack_from(">H", buf, offset)[0]


def _skip_name(buf: bytes, pos: int) -> int:
    """Имя может быть меткам или DNS-компрессией (указатель 0xC0xx) — для
    одноразового ответа хватает поддержать оба варианта."""
    while pos < len(buf):
        length = buf[pos]
        if length == 0:
            return pos + 1
        if length & 0xC0 == 0xC0:
            return pos + 2  # указатель — сам по себе 2 байта
        pos += 1 + length
    return pos


def _parse_address_record(buf: bytes, qtype: int) -> str | None:
    """Первая A/AAAA-запись из ANSWER-секции — для одноразового unicast-
    ответа на наш же запрос полная валидация имени не нужна."""
    if len(buf) < 12:
        return None
    qdcount, ancount = _u16(buf, 4), _u16(buf, 6)
    if ancount <= 0:
        return None
    pos = 12
    for _ in range(qdcount):
        pos = _skip_name(buf, pos) + 4  # +QTYPE(2)+QCLASS(2)
    expected_len = 4 if qtype == _TYPE_A else 16
    family = socket.AF_INET if qtype == _TYPE_A else socket.AF_INET6
    for _ in range(ancount):
        pos = _skip_name(buf, pos)
        if pos + 10 > len(buf):
            return None
        rtype = _u16(buf, pos)
        rdlength = _u16(buf, pos + 8)
        rdata_start = pos + 10
        if rtype == qtype and rdlength == expected_len and rdata_start + rdlength <= len(buf):
            try:
                return socket.inet_ntop(family, buf[rdata_start:rdata_start + rdlength])
            except OSError:
                return None
        pos = rdata_start + rdlength
    return None


def _find_srv_ports(buf: bytes) -> list[int]:
    """Порт из ВСЕХ SRV-записей ответа — не только из ANSWER, но и из
    AUTHORITY/ADDITIONAL секций: респондеры на PTR обычно докладывают SRV
    именно в ADDITIONAL, стандартная mDNS-экономия лишнего round-trip
    (RFC 6763 §12, см. ту же логику в MdnsResolve.kt:findSrvPorts)."""
    if len(buf) < 12:
        return []
    qdcount = _u16(buf, 4)
    total = _u16(buf, 6) + _u16(buf, 8) + _u16(buf, 10)  # AN+NS+AR count
    ports = []
    pos = 12
    for _ in range(qdcount):
        pos = _skip_name(buf, pos) + 4
    for _ in range(total):
        if pos >= len(buf):
            return ports
        pos = _skip_name(buf, pos)
        if pos + 10 > len(buf):
            return ports
        rtype = _u16(buf, pos)
        rdlength = _u16(buf, pos + 8)
        rdata_start = pos + 10
        if rdata_start + rdlength > len(buf):
            return ports
        if rtype == _TYPE_SRV and rdlength >= 6:  # priority(2)+weight(2)+port(2)+target
            ports.append(_u16(buf, rdata_start + 4))
        pos = rdata_start + rdlength
    return ports


def resolve_android_local(timeout: float = 1.2) -> str | None:
    """A-запись 'android.local' — устойчивое системное mDNS-имя на
    магнитолах с открытым Android (например Geely CityRay), которое техник
    иногда набирает руками через Termux ('telnet android.local') — см.
    MdnsResolve.kt:resolveAndroidLocal (тот же запрос)."""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(_build_query(ANDROID_LOCAL, _TYPE_A, qu_bit=True), (_MDNS_ADDR_V4, _MDNS_PORT))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            try:
                data, _ = sock.recvfrom(2048)
            except (socket.timeout, OSError):
                return None
            ip = _parse_address_record(data, _TYPE_A)
            if ip:
                return ip
    except OSError:
        return None
    finally:
        if sock is not None:
            sock.close()


def resolve_android_local_ipv6(iface_name: str | None = None, timeout: float = 1.2) -> str | None:
    """AAAA-запись 'android.local' (IPv6) — для telnet ADB (см.
    telnet_adb.py:enable_adb_via_telnet), где адрес магнитолы обычно
    link-local (fe80::...) — см. MdnsResolve.kt:resolveAndroidLocal (там же
    берётся ipv6). iface_name (например "en0") нужен для двух вещей: чтобы
    отправить multicast-запрос через нужный сетевой интерфейс (на macOS с
    несколькими активными интерфейсами иначе может уйти не туда) и чтобы
    сразу подставить zone id в link-local результат — без него сокет на
    конкретный интерфейс не подключить (см. get_active_interface_index)."""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        if iface_name:
            try:
                ifindex = socket.if_nametoindex(iface_name)
                sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_MULTICAST_IF, struct.pack("I", ifindex))
            except (OSError, AttributeError):
                pass
        sock.sendto(_build_query(ANDROID_LOCAL, _TYPE_AAAA, qu_bit=True), (_MDNS_ADDR_V6, _MDNS_PORT, 0, 0))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            sock.settimeout(remaining)
            try:
                data, _ = sock.recvfrom(2048)
            except (socket.timeout, OSError):
                return None
            ip = _parse_address_record(data, _TYPE_AAAA)
            if ip:
                if ip.startswith("fe80") and iface_name and "%" not in ip:
                    ip = f"{ip}%{iface_name}"
                return ip
    except OSError:
        return None
    finally:
        if sock is not None:
            sock.close()


def resolve_adb_tls_connect_endpoints(timeout: float = 1.5) -> list[tuple[str, int]]:
    """Находит актуальный порт "Беспроводной отладки" (Android 11+) через
    настоящий DNS-SD (RFC 6763), а не фиксированный/угаданный порт — этот
    порт СЛУЧАЙНЫЙ и переназначается заново при каждом включении тумблера
    "Беспроводная отладка" (в отличие от 'adb tcpip 5555', где порт
    фиксирован), поэтому старый порт может просто перестать быть актуальным
    между подключениями. Android сам анонсирует службу "_adb-tls-connect.
    _tcp" по mDNS — тем же способом ей пользуется официальный adb pair/
    Android Studio (см. MdnsResolve.kt:resolveAdbTlsConnectEndpoints, тот
    же протокол). Возвращает [(host, port), ...] — имя цели SRV-записи не
    разбираем, вместо этого берём IP-адрес ОТПРАВИТЕЛЯ UDP-пакета (тот, кто
    ответил на запрос именно этой службы — достаточный источник адреса в
    пределах одной локальной сети)."""
    found: dict[str, tuple[str, int]] = {}
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(_build_query(ADB_TLS_CONNECT_SERVICE, _TYPE_PTR, qu_bit=False), (_MDNS_ADDR_V4, _MDNS_PORT))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, addr = sock.recvfrom(4096)
            except (socket.timeout, OSError):
                break
            host = addr[0]
            for port in _find_srv_ports(data):
                found[f"{host}:{port}"] = (host, port)
    except OSError:
        pass
    finally:
        if sock is not None:
            sock.close()
    return list(found.values())
