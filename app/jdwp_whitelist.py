"""JDWP-патч белого списка установки для магнитол Desay Semidrive (x9h): Haval Jolion 2026 (TR01025),
GWM Poer 2026 (TR4314) и родственные. Прошивка запущена с ro.debuggable=1, поэтому system_server
отлаживается по JDWP; в PackageManagerService есть поле mInstallWhiteList (ArrayList<String>) — пока имя
пакета в нём нет, любой сторонний APK получает INSTALL_FAILED_ABORTED / -115 (см. лог #364). Способ,
опубликованный проектом DesayInstall (JDWP + JDI), здесь переписан своим МИНИМАЛЬНЫМ JDWP-клиентом: только
чтение/запись полей и массивов, без вызова методов и точек останова — поэтому не нужен полноценный JDK с
модулем jdk.jdi, хватает обычного сокета. Патч живёт в памяти и сбрасывается при перезагрузке; сам
установленный APK остаётся.

Последовательность (та же, что WHITELIST_ONLY в DesayInstallJdwp.java):
  handshake → IDSizes → VM.Suspend → найти PackageManagerService и его mInstallWhiteList → живой экземпляр
  PMS → ArrayList (elementData: Object[], size: int) → дописать строку с именем пакета прямо в массив и
  увеличить size → VM.Resume. Всю VM держим приостановленной, чтобы GC ART не сдвинул массив между нашими
  чтением и записью (иначе ObjectCollectedException на живом system_server).

Транспорт (socket) передаётся снаружи: на десктопе — TCP к `adb forward tcp:PORT jdwp:PID`. Ошибки —
JdwpError с понятным текстом."""
from __future__ import annotations
import socket
import struct

HANDSHAKE = b"JDWP-Handshake"

# Наборы команд и команды JDWP (см. спецификацию Java Debug Wire Protocol).
_VM = 1
_VM_IDSIZES = 7
_VM_SUSPEND = 8
_VM_RESUME = 9
_VM_DISPOSE = 6
_VM_CREATE_STRING = 11
_VM_CLASSES_BY_SIG = 2
_REF = 2
_REF_FIELDS = 4
_REF_INSTANCES = 16
_OBJ = 9
_OBJ_REFERENCE_TYPE = 1
_OBJ_GET_VALUES = 2
_OBJ_SET_VALUES = 3
_STR = 10
_STR_VALUE = 1
_ARR = 13
_ARR_LENGTH = 1
_ARR_GET_VALUES = 2
_ARR_SET_VALUES = 3
_ARRTYPE = 4
_ARRTYPE_NEW_INSTANCE = 1

# Теги значений JDWP (первый байт value / arrayregion).
_TAG_OBJECT = ord("L")
_TAG_ARRAY = ord("[")
_TAG_STRING = ord("s")
_TAG_INT = ord("I")
_OBJECT_TAGS = frozenset((_TAG_OBJECT, _TAG_ARRAY, _TAG_STRING, ord("t"), ord("g"), ord("l"), ord("c")))

PMS_SIGNATURE = "Lcom/android/server/pm/PackageManagerService;"
WHITELIST_FIELD = "mInstallWhiteList"


class JdwpError(RuntimeError):
    pass


class JdwpClient:
    """Минимальный JDWP-клиент поверх готового сокета. Один поток, синхронные запрос-ответ."""

    def __init__(self, sock: socket.socket, timeout: float = 20.0):
        self._sock = sock
        self._sock.settimeout(timeout)
        self._next_id = 1
        # Размеры идентификаторов узнаём из VM.IDSizes (на ART обычно 8) — не хардкодим.
        self.field_id_size = 8
        self.object_id_size = 8
        self.ref_type_id_size = 8

    # -- транспорт ----------------------------------------------------------
    def _recv_exact(self, n: int) -> bytes:
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise JdwpError("соединение JDWP закрыто раньше времени")
            buf += chunk
        return bytes(buf)

    def handshake(self) -> None:
        self._sock.sendall(HANDSHAKE)
        reply = self._recv_exact(len(HANDSHAKE))
        if reply != HANDSHAKE:
            raise JdwpError("устройство не ответило JDWP-рукопожатием (system_server не отлаживается?)")

    def _command(self, cmd_set: int, cmd: int, data: bytes = b"") -> bytes:
        packet_id = self._next_id
        self._next_id += 1
        length = 11 + len(data)
        header = struct.pack(">IIBBB", length, packet_id, 0, cmd_set, cmd)
        self._sock.sendall(header + data)
        # Ответ: length(4) id(4) flags(1) errorCode(2) data. Пропускаем возможные командные пакеты от VM.
        while True:
            reply_len, reply_id, flags = struct.unpack(">IIB", self._recv_exact(9))
            rest = self._recv_exact(reply_len - 9)
            if not (flags & 0x80):
                continue  # это команда от VM (событие) — нам не нужна, читаем дальше
            if reply_id != packet_id:
                continue
            error_code = struct.unpack(">H", rest[:2])[0]
            if error_code != 0:
                raise JdwpError(f"JDWP-команда {cmd_set}/{cmd} вернула ошибку {error_code}")
            return rest[2:]

    # -- разбор идентификаторов переменной длины ----------------------------
    def _read_object_id(self, data: bytes, off: int) -> tuple[int, int]:
        return int.from_bytes(data[off:off + self.object_id_size], "big"), off + self.object_id_size

    def _read_ref_type_id(self, data: bytes, off: int) -> tuple[int, int]:
        return int.from_bytes(data[off:off + self.ref_type_id_size], "big"), off + self.ref_type_id_size

    def _read_field_id(self, data: bytes, off: int) -> tuple[int, int]:
        return int.from_bytes(data[off:off + self.field_id_size], "big"), off + self.field_id_size

    def _oid(self, value: int) -> bytes:
        return value.to_bytes(self.object_id_size, "big")

    def _rid(self, value: int) -> bytes:
        return value.to_bytes(self.ref_type_id_size, "big")

    def _fid(self, value: int) -> bytes:
        return value.to_bytes(self.field_id_size, "big")

    @staticmethod
    def _read_jdwp_string(data: bytes, off: int) -> tuple[str, int]:
        (length,) = struct.unpack_from(">I", data, off)
        off += 4
        return data[off:off + length].decode("utf-8", "replace"), off + length

    # -- команды ------------------------------------------------------------
    def id_sizes(self) -> None:
        data = self._command(_VM, _VM_IDSIZES)
        field, method, obj, ref, frame = struct.unpack(">iiiii", data[:20])
        self.field_id_size = field
        self.object_id_size = obj
        self.ref_type_id_size = ref

    def suspend(self) -> None:
        self._command(_VM, _VM_SUSPEND)

    def resume(self) -> None:
        self._command(_VM, _VM_RESUME)

    def dispose(self) -> None:
        try:
            self._command(_VM, _VM_DISPOSE)
        except (JdwpError, OSError):
            pass

    def classes_by_signature(self, signature: str) -> int | None:
        payload = signature.encode("utf-8")
        data = self._command(_VM, _VM_CLASSES_BY_SIG, struct.pack(">I", len(payload)) + payload)
        (count,) = struct.unpack_from(">I", data, 0)
        if count == 0:
            return None
        off = 4 + 1  # int count, затем byte refTypeTag
        ref_id, _ = self._read_ref_type_id(data, off)
        return ref_id

    def create_string(self, text: str) -> int:
        payload = text.encode("utf-8")
        data = self._command(_VM, _VM_CREATE_STRING, struct.pack(">I", len(payload)) + payload)
        obj, _ = self._read_object_id(data, 0)
        return obj

    def fields(self, ref_type_id: int) -> dict[str, int]:
        """Имя поля → fieldID (только объявленные в самом типе — как allFields для одного класса)."""
        data = self._command(_REF, _REF_FIELDS, self._rid(ref_type_id))
        (declared,) = struct.unpack_from(">I", data, 0)
        off = 4
        result: dict[str, int] = {}
        for _ in range(declared):
            field_id, off = self._read_field_id(data, off)
            name, off = self._read_jdwp_string(data, off)
            _sig, off = self._read_jdwp_string(data, off)
            off += 4  # modBits
            result[name] = field_id
        return result

    def instances(self, ref_type_id: int, max_instances: int = 0) -> list[int]:
        data = self._command(_REF, _REF_INSTANCES, self._rid(ref_type_id) + struct.pack(">i", max_instances))
        (count,) = struct.unpack_from(">I", data, 0)
        off = 4
        result = []
        for _ in range(count):
            off += 1  # tag
            obj, off = self._read_object_id(data, off)
            result.append(obj)
        return result

    def object_reference_type(self, object_id: int) -> int:
        data = self._command(_OBJ, _OBJ_REFERENCE_TYPE, self._oid(object_id))
        off = 1  # refTypeTag
        ref, _ = self._read_ref_type_id(data, off)
        return ref

    def get_object_field(self, object_id: int, field_id: int) -> tuple[int, int]:
        """Значение одного поля-объекта: (tag, objectID). Для int-поля используйте get_int_field."""
        data = self._command(_OBJ, _OBJ_GET_VALUES, self._oid(object_id) + struct.pack(">I", 1) + self._fid(field_id))
        # int values(=1), затем value: tag(1) + objectID
        tag = data[4]
        obj, _ = self._read_object_id(data, 5)
        return tag, obj

    def get_int_field(self, object_id: int, field_id: int) -> int:
        data = self._command(_OBJ, _OBJ_GET_VALUES, self._oid(object_id) + struct.pack(">I", 1) + self._fid(field_id))
        # value: tag(1)=I + int(4)
        return struct.unpack_from(">i", data, 5)[0]

    def set_object_field(self, object_id: int, field_id: int, value_object_id: int) -> None:
        # untagged-value для поля-объекта = objectID (без tag)
        payload = self._oid(object_id) + struct.pack(">I", 1) + self._fid(field_id) + self._oid(value_object_id)
        self._command(_OBJ, _OBJ_SET_VALUES, payload)

    def set_int_field(self, object_id: int, field_id: int, value: int) -> None:
        payload = self._oid(object_id) + struct.pack(">I", 1) + self._fid(field_id) + struct.pack(">i", value)
        self._command(_OBJ, _OBJ_SET_VALUES, payload)

    def array_length(self, array_id: int) -> int:
        data = self._command(_ARR, _ARR_LENGTH, self._oid(array_id))
        return struct.unpack_from(">i", data, 0)[0]

    def array_get_object_ids(self, array_id: int, first: int, length: int) -> list[int]:
        """Значения object-массива [first, first+length) как список objectID (0 = null)."""
        if length <= 0:
            return []
        data = self._command(_ARR, _ARR_GET_VALUES, self._oid(array_id) + struct.pack(">ii", first, length))
        tag = data[0]
        (count,) = struct.unpack_from(">I", data, 1)
        off = 5
        result = []
        if tag in _OBJECT_TAGS:
            for _ in range(count):
                off += 1  # каждый элемент — полноценный tagged value: tag + objectID
                obj, off = self._read_object_id(data, off)
                result.append(obj)
        else:
            raise JdwpError(f"ожидался object-массив, пришёл тег {tag}")
        return result

    def array_set_object_ids(self, array_id: int, first: int, object_ids: list[int]) -> None:
        # untagged-values для object-массива = подряд objectID
        payload = self._oid(array_id) + struct.pack(">ii", first, len(object_ids))
        for oid in object_ids:
            payload += self._oid(oid)
        self._command(_ARR, _ARR_SET_VALUES, payload)

    def new_object_array(self, array_type_id: int, length: int) -> int:
        data = self._command(_ARRTYPE, _ARRTYPE_NEW_INSTANCE, self._rid(array_type_id) + struct.pack(">i", length))
        off = 1  # tag
        obj, _ = self._read_object_id(data, off)
        return obj

    def string_value(self, string_id: int) -> str:
        if string_id == 0:
            return ""
        data = self._command(_STR, _STR_VALUE, self._oid(string_id))
        text, _ = self._read_jdwp_string(data, 0)
        return text


def patch_whitelist(client: JdwpClient, packages: list[str], log=lambda m: None) -> None:
    """Дописывает имена пакетов в PackageManagerService.mInstallWhiteList живого system_server. Бросает
    JdwpError, если поле/экземпляр не найдены (магнитола не из семейства Desay x9h или прошивка изменила
    поле — проверяйте перед тем, как полагаться на способ)."""
    client.handshake()
    client.id_sizes()
    client.suspend()
    try:
        pms_type = client.classes_by_signature(PMS_SIGNATURE)
        if pms_type is None:
            raise JdwpError("PackageManagerService не найден в system_server — это не Desay x9h / не тот процесс")
        pms_fields = client.fields(pms_type)
        whitelist_field = pms_fields.get(WHITELIST_FIELD)
        if whitelist_field is None:
            raise JdwpError(f"поле {WHITELIST_FIELD} отсутствует — прошивка не поддерживается этим способом")
        pms_instances = client.instances(pms_type, 0)
        if not pms_instances:
            raise JdwpError("живой экземпляр PackageManagerService не найден")
        pms = pms_instances[0]

        _tag, whitelist = client.get_object_field(pms, whitelist_field)
        if whitelist == 0:
            raise JdwpError("mInstallWhiteList пуст (null) — на этой прошивке способ не применим")
        list_type = client.object_reference_type(whitelist)
        list_fields = client.fields(list_type)
        element_data_field = list_fields.get("elementData")
        size_field = list_fields.get("size")
        if element_data_field is None or size_field is None:
            raise JdwpError("не найдены поля ArrayList (elementData/size)")

        for package_name in packages:
            _t, element_data = client.get_object_field(whitelist, element_data_field)
            size = client.get_int_field(whitelist, size_field)
            existing = client.array_get_object_ids(element_data, 0, size) if element_data else []
            if any(client.string_value(oid) == package_name for oid in existing if oid):
                log(f"{package_name}: уже в белом списке")
                continue
            capacity = client.array_length(element_data) if element_data else 0
            pkg_ref = client.create_string(package_name)
            if element_data and size < capacity:
                client.array_set_object_ids(element_data, size, [pkg_ref])
            else:
                array_type = client.object_reference_type(element_data)
                new_array = client.new_object_array(array_type, max(size + 1, 4))
                if size:
                    client.array_set_object_ids(new_array, 0, existing)
                client.array_set_object_ids(new_array, size, [pkg_ref])
                client.set_object_field(whitelist, element_data_field, new_array)
            client.set_int_field(whitelist, size_field, size + 1)
            log(f"{package_name}: добавлен в белый список (размер {size + 1})")
    finally:
        client.resume()
        client.dispose()
