"""Мок JDWP-сервера, имитирующий system_server с PackageManagerService.mInstallWhiteList (ArrayList<String>).
Отвечает ровно тем набором команд, которым пользуется app/jdwp_whitelist.py, в точном wire-формате JDWP —
проверяет кодирование/декодирование клиента без реальной магнитолы. Используется в test_jdwp_whitelist.py."""
from __future__ import annotations
import socket
import struct
import threading

HANDSHAKE = b"JDWP-Handshake"
_TAG_OBJECT = ord("L")
_TAG_STRING = ord("s")
_TAG_INT = ord("I")


class _Obj:
    def __init__(self, ref_type):
        self.ref_type = ref_type
        self.fields: dict[int, object] = {}


class _Str:
    def __init__(self, value):
        self.value = value


class _Arr:
    def __init__(self, elements):
        self.elements = list(elements)  # список objectID (int), 0 = null


class MockJdwp:
    def __init__(self, initial_whitelist: list[str] | None = None, capacity: int | None = None):
        self.ids = 0x100
        self.objects: dict[int, object] = {}
        self.strings: dict[int, str] = {}
        self.ref_types: dict[int, str] = {}   # refTypeID -> signature/name
        self.ref_fields: dict[int, dict[str, int]] = {}
        self.next_field = 0x10

        self.pms_type = self._new_ref("Lcom/android/server/pm/PackageManagerService;")
        self.arraylist_type = self._new_ref("Ljava/util/ArrayList;")
        self.objarr_type = self._new_ref("[Ljava/lang/Object;")

        self.wl_field = self._field(self.pms_type, "mInstallWhiteList")
        self.ed_field = self._field(self.arraylist_type, "elementData")
        self.sz_field = self._field(self.arraylist_type, "size")

        names = list(initial_whitelist or ["com.geely.oem.one", "com.desay.two"])
        elems = [self._new_string(n) for n in names]
        cap = capacity if capacity is not None else len(elems)
        while len(elems) < cap:
            elems.append(0)
        self.array = self._new_obj(self.objarr_type, is_array=elems)
        self.arraylist = self._new_obj(self.arraylist_type)
        self.objects[self.arraylist].fields[self.ed_field] = self.array
        self.objects[self.arraylist].fields[self.sz_field] = len(names)
        self.pms = self._new_obj(self.pms_type)
        self.objects[self.pms].fields[self.wl_field] = self.arraylist

    # -- построение графа --
    def _alloc(self):
        self.ids += 1
        return self.ids

    def _new_ref(self, signature):
        rid = self._alloc()
        self.ref_types[rid] = signature
        self.ref_fields[rid] = {}
        return rid

    def _field(self, ref_type, name):
        self.next_field += 1
        self.ref_fields[ref_type][name] = self.next_field
        return self.next_field

    def _new_obj(self, ref_type, is_array=None):
        oid = self._alloc()
        if is_array is not None:
            self.objects[oid] = _Arr(is_array)
            self.objects[oid].ref_type = ref_type
        else:
            self.objects[oid] = _Obj(ref_type)
        return oid

    def _new_string(self, value):
        oid = self._alloc()
        s = _Str(value)
        s.ref_type = None
        self.strings[oid] = value
        self.objects[oid] = s
        return oid

    def current_whitelist(self):
        arr = self.objects[self.array]
        size = self.objects[self.arraylist].fields[self.sz_field]
        ed = self.objects[self.arraylist].fields[self.ed_field]
        arr = self.objects[ed]
        return [self.strings.get(oid) for oid in arr.elements[:size]]

    # -- сервер --
    def serve(self, sock: socket.socket):
        if sock.recv(len(HANDSHAKE)) != HANDSHAKE:
            return
        sock.sendall(HANDSHAKE)
        buf = b""
        while True:
            while len(buf) < 11:
                chunk = sock.recv(4096)
                if not chunk:
                    return
                buf += chunk
            length = struct.unpack(">I", buf[:4])[0]
            while len(buf) < length:
                chunk = sock.recv(4096)
                if not chunk:
                    return
                buf += chunk
            packet, buf = buf[:length], buf[length:]
            pid, flags, cmd_set, cmd = struct.unpack(">IBBB", packet[4:11])
            data = packet[11:]
            reply = self._dispatch(cmd_set, cmd, data)
            out = struct.pack(">IIBH", 11 + len(reply), pid, 0x80, 0) + reply
            sock.sendall(out)
            if (cmd_set, cmd) == (1, 6):  # Dispose
                return

    def _dispatch(self, cmd_set, cmd, data) -> bytes:
        if (cmd_set, cmd) == (1, 7):   # IDSizes
            return struct.pack(">iiiii", 8, 8, 8, 8, 8)
        if (cmd_set, cmd) in ((1, 8), (1, 9), (1, 6)):  # Suspend/Resume/Dispose
            return b""
        if (cmd_set, cmd) == (1, 2):   # ClassesBySignature
            (slen,) = struct.unpack_from(">I", data, 0)
            sig = data[4:4 + slen].decode()
            for rid, rsig in self.ref_types.items():
                if rsig == sig:
                    return struct.pack(">I", 1) + bytes([_TAG_OBJECT]) + rid.to_bytes(8, "big") + struct.pack(">i", 7)
            return struct.pack(">I", 0)
        if (cmd_set, cmd) == (1, 11):  # CreateString
            (slen,) = struct.unpack_from(">I", data, 0)
            text = data[4:4 + slen].decode()
            return self._new_string(text).to_bytes(8, "big")
        if (cmd_set, cmd) == (2, 4):   # ReferenceType.Fields
            rid = int.from_bytes(data[:8], "big")
            fields = self.ref_fields.get(rid, {})
            out = struct.pack(">I", len(fields))
            for name, fid in fields.items():
                nb = name.encode()
                sig = b"x"
                out += fid.to_bytes(8, "big") + struct.pack(">I", len(nb)) + nb + struct.pack(">I", len(sig)) + sig + struct.pack(">i", 0)
            return out
        if (cmd_set, cmd) == (2, 16):  # ReferenceType.Instances
            rid = int.from_bytes(data[:8], "big")
            insts = [oid for oid, o in self.objects.items() if getattr(o, "ref_type", None) == rid and rid == self.pms_type]
            out = struct.pack(">I", len(insts))
            for oid in insts:
                out += bytes([_TAG_OBJECT]) + oid.to_bytes(8, "big")
            return out
        if (cmd_set, cmd) == (9, 1):   # ObjectReference.ReferenceType
            oid = int.from_bytes(data[:8], "big")
            rid = self.objects[oid].ref_type
            return bytes([_TAG_OBJECT]) + rid.to_bytes(8, "big")
        if (cmd_set, cmd) == (9, 2):   # ObjectReference.GetValues
            oid = int.from_bytes(data[:8], "big")
            (count,) = struct.unpack_from(">I", data, 8)
            off = 12
            out = struct.pack(">I", count)
            for _ in range(count):
                fid = int.from_bytes(data[off:off + 8], "big"); off += 8
                val = self.objects[oid].fields.get(fid, 0)
                if isinstance(val, int) and fid == self.sz_field:
                    out += bytes([_TAG_INT]) + struct.pack(">i", val)
                else:
                    out += bytes([_TAG_OBJECT]) + int(val).to_bytes(8, "big")
            return out
        if (cmd_set, cmd) == (9, 3):   # ObjectReference.SetValues
            oid = int.from_bytes(data[:8], "big")
            (count,) = struct.unpack_from(">I", data, 8)
            off = 12
            for _ in range(count):
                fid = int.from_bytes(data[off:off + 8], "big"); off += 8
                if fid == self.sz_field:
                    self.objects[oid].fields[fid] = struct.unpack_from(">i", data, off)[0]; off += 4
                else:
                    self.objects[oid].fields[fid] = int.from_bytes(data[off:off + 8], "big"); off += 8
            return b""
        if (cmd_set, cmd) == (13, 1):  # ArrayReference.Length
            oid = int.from_bytes(data[:8], "big")
            return struct.pack(">i", len(self.objects[oid].elements))
        if (cmd_set, cmd) == (13, 2):  # ArrayReference.GetValues
            oid = int.from_bytes(data[:8], "big")
            first, length = struct.unpack_from(">ii", data, 8)
            elems = self.objects[oid].elements[first:first + length]
            out = bytes([_TAG_OBJECT]) + struct.pack(">I", len(elems))
            for e in elems:
                out += bytes([_TAG_OBJECT]) + int(e).to_bytes(8, "big")
            return out
        if (cmd_set, cmd) == (13, 3):  # ArrayReference.SetValues
            oid = int.from_bytes(data[:8], "big")
            first, length = struct.unpack_from(">ii", data, 8)
            off = 16
            arr = self.objects[oid].elements
            while len(arr) < first + length:
                arr.append(0)
            for i in range(length):
                arr[first + i] = int.from_bytes(data[off:off + 8], "big"); off += 8
            return b""
        if (cmd_set, cmd) == (4, 1):   # ArrayType.NewInstance
            length = struct.unpack_from(">i", data, 8)[0]
            new_id = self._new_obj(self.objarr_type, is_array=[0] * length)
            return bytes([_TAG_OBJECT]) + new_id.to_bytes(8, "big")
        if (cmd_set, cmd) == (10, 1):  # StringReference.Value
            oid = int.from_bytes(data[:8], "big")
            sb = self.strings.get(oid, "").encode()
            return struct.pack(">I", len(sb)) + sb
        raise AssertionError(f"мок не знает команду {cmd_set}/{cmd}")


def start_mock(mock: MockJdwp) -> tuple[str, int, threading.Thread]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    host, port = listener.getsockname()

    def run():
        conn, _ = listener.accept()
        listener.close()
        try:
            mock.serve(conn)
        finally:
            conn.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return host, port, thread
