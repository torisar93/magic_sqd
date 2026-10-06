"""«Вернуть Wi-Fi / ДХО / Arkamys» (владелец, 2026-10-06; разобрано по прошивкам ГУ Desay SV NV8020/18 — SemiDrive X9H,
Android 10, сборки YFVE/Chery: Omoda C5, Chery Tiggo 4 New, Tenet T4, XCITE X-Cross 8 и т.п.; тема 4PDA 1117181).

На части прошивок производитель программно прячет в интерфейсе Wi-Fi (пункт меню и плитку шторки), управление ДХО и
переключатель звука Arkamys. Сам стек Wi-Fi/функции в прошивке целы — скрыт только UI. Решают это в приложениях
`com.chery.settings` (Настройки) и SystemUI, в классе
`com.chery.caradapter.carapi.client.CarConfigInfoClient` — методы-гейты, которые на «урезанных» прошивках
захардкожены возвращать «нет»:
  - hasWifi()Z              — `return false`  → переключатель/плитка Wi-Fi скрыты;
  - getDrlVisibility()Z     — `return false`  → пункт ДХО скрыт;
  - hasArkamysAdvanced()Z   — проверка по partnumber/проекту → скрыт переключатель Arkamys;
а в Настройках ещё `WifiReposity.setWifiEnabled(Z)V` молча игнорирует ВКЛючение
(`if (!on) manager.setWifiEnabled(false); else return;`).

Патч точечный, прямо в dex (Dalvik-байткод), БЕЗ пересборки архива и БЕЗ androguard (как motion_patch.py — только
stdlib):
  - force-true: тело метода переписывается на `const/4 v0,1 / return v0`, хвост добивается NOP (`00 00`). Длина кода
    не меняется;
  - setWifiEnabled: блок `if-eqz vX, +N / return-void` затирается NOP — метод всегда зовёт WifiManager.setWifiEnabled.
ВСЕ правки одинаковой длины, поэтому dex правится «на месте» в архиве (см. patch_apk_inplace): смещения записей не
двигаются, 4-байтовое ВЫРАВНИВАНИЕ несжатых classes*.dex сохраняется (критично для Android 10), меняются только
контрольные суммы dex (adler32+SHA-1) и CRC32 записи в zip. Подпись после правки недействительна — APK переподписывают
ПУБЛИЧНЫМ платформенным ключом AOSP (эти ГУ — userdebug/test-keys, см. files/platform_cert/; ПК: apk_signer.py,
Android: ApkResign.kt), иначе системное приложение с sharedUserId=android.uid.system не примется.

Заливка пропатченного — в /system поверх штатного (root/remount, бэкап, `rm -rf <app>/oat`, chmod 644, chown
root:root, chcon u:object_r:system_file:s0, reboot) — делает вызывающий код (install_context.py / stage).

Файл ОДИНАКОВЫЙ на ПК (app/wifi_patch.py) и Android (android/app/src/main/python/wifi_patch.py) — правьте оба сразу
(tests/test_wifi_patch.py сверяет копии)."""
from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

CONFIG_CLIENT = "Lcom/chery/caradapter/carapi/client/CarConfigInfoClient;"
WIFI_REPOSITY = "Lcom/chery/settings/model/repository/WifiReposity;"

# Доступные правки. Ключ — имя функции владельца; значение — что делаем.
FEATURE_FORCE_TRUE = {
    "wifi": (CONFIG_CLIENT, "hasWifi"),
    "drl": (CONFIG_CLIENT, "getDrlVisibility"),
    "arkamys": (CONFIG_CLIENT, "hasArkamysAdvanced"),
}
# Снятие блокировки включения — только для Wi-Fi, только в Настройках (в SystemUI класса WifiReposity нет).
WIFI_ENABLE_GUARD = (WIFI_REPOSITY, "setWifiEnabled")


class WifiPatchError(Exception):
    """dex/APK не удалось пропатчить (неожиданный байткод, другой формат, битый архив) — не трогать файл."""


# --------------------------------------------------------------------------------------------------------------------
# Минимальный разбор dex: найти смещение кода метода по дескриптору класса и имени метода. Формат dex — см.
# https://source.android.com/docs/core/runtime/dex-format . Нужны только таблицы строк/типов/методов и class_data.
# --------------------------------------------------------------------------------------------------------------------
def _uleb128(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7
        if shift > 35:
            raise WifiPatchError("битый uleb128 в dex")


class _Dex:
    def __init__(self, data: bytes):
        if len(data) < 112 or data[:4] != b"dex\n":
            raise WifiPatchError("это не dex")
        self.data = data
        (self.string_ids_size, self.string_ids_off, self.type_ids_size, self.type_ids_off,
         self.proto_ids_size, self.proto_ids_off, self.field_ids_size, self.field_ids_off,
         self.method_ids_size, self.method_ids_off, self.class_defs_size, self.class_defs_off) = struct.unpack_from(
            "<IIIIIIIIIIII", data, 56)
        self._str_cache: dict[int, str] = {}

    def string(self, idx: int) -> str:
        if idx in self._str_cache:
            return self._str_cache[idx]
        off = struct.unpack_from("<I", self.data, self.string_ids_off + 4 * idx)[0]
        _, pos = _uleb128(self.data, off)          # длина в utf16-единицах — не нужна, читаем до \0 (MUTF-8)
        end = self.data.index(0, pos)
        s = self.data[pos:end].decode("utf-8", "replace")
        self._str_cache[idx] = s
        return s

    def type_descriptor(self, type_idx: int) -> str:
        return self.string(struct.unpack_from("<I", self.data, self.type_ids_off + 4 * type_idx)[0])

    def method_name_idx(self, method_idx: int) -> int:
        # method_id_item: class_idx(ushort), proto_idx(ushort), name_idx(uint)
        return struct.unpack_from("<I", self.data, self.method_ids_off + 8 * method_idx + 4)[0]

    def _find_type_idx(self, descriptor: str) -> int | None:
        for i in range(self.type_ids_size):
            if self.type_descriptor(i) == descriptor:
                return i
        return None

    def find_code_off(self, class_desc: str, method_name: str) -> int | None:
        """Смещение code_item метода (0/нет — абстрактный/не найден)."""
        type_idx = self._find_type_idx(class_desc)
        if type_idx is None:
            return None
        # найти class_def с этим class_idx
        class_data_off = None
        for i in range(self.class_defs_size):
            base = self.class_defs_off + 32 * i
            if struct.unpack_from("<I", self.data, base)[0] == type_idx:
                class_data_off = struct.unpack_from("<I", self.data, base + 24)[0]
                break
        if not class_data_off:
            return None
        pos = class_data_off
        sf, pos = _uleb128(self.data, pos)
        inf, pos = _uleb128(self.data, pos)
        dm, pos = _uleb128(self.data, pos)
        vm, pos = _uleb128(self.data, pos)
        for _ in range(sf + inf):              # пропустить поля (encoded_field: idx_diff, access)
            _, pos = _uleb128(self.data, pos)
            _, pos = _uleb128(self.data, pos)
        for count in (dm, vm):                 # direct, затем virtual — method_idx считается с нуля в каждой группе
            midx = 0
            for _ in range(count):
                diff, pos = _uleb128(self.data, pos)
                _access, pos = _uleb128(self.data, pos)
                code_off, pos = _uleb128(self.data, pos)
                midx += diff
                if self.string(self.method_name_idx(midx)) == method_name:
                    return code_off
        return None


# --------------------------------------------------------------------------------------------------------------------
# Правки байткода (все одной длины — размер insns не меняется).
# code_item: registers(ushort), ins(ushort), outs(ushort), tries(ushort), debug_off(uint), insns_size(uint, в
# 16-битных словах), insns[...]. Код начинается на code_off+16.
# --------------------------------------------------------------------------------------------------------------------
def _force_true(ba: bytearray, code_off: int) -> None:
    base = code_off + 16
    insns_words = struct.unpack_from("<I", ba, code_off + 12)[0]
    end = base + insns_words * 2
    ba[base:base + 4] = bytes((0x12, 0x10, 0x0F, 0x00))   # const/4 v0,1 ; return v0
    for k in range(base + 4, end):                        # хвост — NOP (0x0000)
        ba[k] = 0x00


def _nop_wifi_guard(ba: bytearray, code_off: int) -> bool:
    """Затереть `if-eqz vX,+N / return-void` (0x38 … / 0x0E 0x00) на NOP. Код dex выровнен по 16-битным словам, поэтому
    ищем паттерн по словам: первый `38 ?? ?? ?? 0E 00` внутри метода — это и есть «если выключаем — делаем, иначе
    сразу выходим». Возвращает True, если нашли и сняли."""
    base = code_off + 16
    insns_words = struct.unpack_from("<I", ba, code_off + 12)[0]
    end = base + insns_words * 2
    pos = base
    while pos + 6 <= end:
        if ba[pos] == 0x38 and ba[pos + 4] == 0x0E and ba[pos + 5] == 0x00:  # if-eqz(4б) + return-void(2б)
            ba[pos:pos + 6] = b"\x00\x00\x00\x00\x00\x00"
            return True
        pos += 2
    return False


def _fix_dex_hashes(ba: bytearray) -> None:
    ba[12:32] = hashlib.sha1(bytes(ba[32:])).digest()
    ba[8:12] = struct.pack("<I", zlib.adler32(bytes(ba[12:])) & 0xFFFFFFFF)


def patch_dex(data: bytes, features: list[str]) -> tuple[bytes | None, list[str]]:
    """Пропатчить ОДИН dex. features — из FEATURE_FORCE_TRUE (wifi/drl/arkamys); «wifi» также снимает guard у
    setWifiEnabled, если класс есть в этом dex. Возвращает (новые_байты|None, список_сделанного). None — в этом dex
    патчить нечего (нужных классов нет)."""
    dex = _Dex(data)
    ba = bytearray(data)
    done: list[str] = []
    changed = False
    for feat in features:
        cls, meth = FEATURE_FORCE_TRUE[feat]
        off = dex.find_code_off(cls, meth)
        if not off:
            continue
        if bytes(ba[off + 16:off + 20]) == bytes((0x12, 0x10, 0x0F, 0x00)):
            continue                       # уже пропатчен
        _force_true(ba, off)
        done.append(f"{feat} ({meth}→true)")
        changed = True
    if "wifi" in features:
        off = dex.find_code_off(*WIFI_ENABLE_GUARD)
        if off and _nop_wifi_guard(ba, off):
            done.append("wifi (setWifiEnabled разблокирован)")
            changed = True
    if not changed:
        return None, done
    _fix_dex_hashes(ba)
    return bytes(ba), done


# --------------------------------------------------------------------------------------------------------------------
# Правка dex «на месте» в APK: смещения записей не двигаются → выравнивание сохраняется. Меняем только байты dex
# (та же длина), CRC32 записи в центральном каталоге и в локальном заголовке, плюс контрольные суммы самого dex.
# --------------------------------------------------------------------------------------------------------------------
_EOCD = b"PK\x05\x06"


def patch_apk_inplace(src, dst, features: list[str]) -> list[str]:
    """Копирует src в dst и патчит на месте все несжатые classes*.dex. Возвращает список сделанных правок (пустой —
    патчить было нечего, dst всё равно создаётся как копия). Подпись dst после этого недействительна — переподписать."""
    src, dst = Path(src), Path(dst)
    data = bytearray(src.read_bytes())
    at = data.rfind(_EOCD)
    if at < 0:
        raise WifiPatchError("APK — не zip-архив")
    entries, cd_size, cd_off = struct.unpack_from("<HII", data, at + 10)
    done_all: list[str] = []
    pos = cd_off
    for _ in range(entries):
        if struct.unpack_from("<I", data, pos)[0] != 0x02014B50:
            raise WifiPatchError("битый каталог архива")
        method, = struct.unpack_from("<H", data, pos + 10)
        comp_size, = struct.unpack_from("<I", data, pos + 20)
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", data, pos + 28)
        lho, = struct.unpack_from("<I", data, pos + 42)
        name = data[pos + 46:pos + 46 + name_len].decode("latin1")
        nxt = pos + 46 + name_len + extra_len + comment_len
        if name.endswith(".dex") and "/" not in name and method == 0:
            l_name, l_extra = struct.unpack_from("<HH", data, lho + 26)
            data_off = lho + 30 + l_name + l_extra
            dex_bytes = bytes(data[data_off:data_off + comp_size])
            new_dex, done = patch_dex(dex_bytes, features)
            if new_dex is not None:
                if len(new_dex) != len(dex_bytes):
                    raise WifiPatchError("длина dex изменилась — правка неверна")
                new_crc = zlib.crc32(new_dex) & 0xFFFFFFFF
                data[data_off:data_off + comp_size] = new_dex
                struct.pack_into("<I", data, pos + 16, new_crc)   # CRC в центральном каталоге
                struct.pack_into("<I", data, lho + 14, new_crc)   # CRC в локальном заголовке
                done_all.extend(f"{name}: {d}" for d in done if not d.endswith("уже стоит"))
        pos = nxt
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    part.write_bytes(bytes(data))
    part.replace(dst)
    return done_all
