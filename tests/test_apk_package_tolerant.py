"""Имя пакета из защищённых APK (разбор 09.11.2026, №10697/11055: JDWP-способ Jolion 2026 не ставил strelka.apk, microG,
Car Launcher Pro техника — «не удалось прочитать имя пакета»). Как Android: строка по rawValue при сбитом типе значения;
ложный флаг шифрования записи; последняя попытка — по строкам манифеста; причина отказа — в журнал."""
from __future__ import annotations

import struct
import zipfile
import zlib

from app.apk_package import read_package_name, why_unreadable


def _axml(package: str, value_type: int = 0x03, extra_strings=(), file_type: int = 0x0003, with_attr: bool = True) -> bytes:
    strings = ["manifest", "package", package, *extra_strings]
    body = b""
    offsets = []
    for s in strings:
        offsets.append(len(body))
        enc = s.encode("utf-16-le")
        body += struct.pack("<H", len(s)) + enc + b"\x00\x00"
    while len(body) % 4:
        body += b"\x00"
    header_size = 28
    strings_start = header_size + 4 * len(strings)
    pool = struct.pack("<HHI", 0x0001, header_size, strings_start + len(body)) + struct.pack("<IIIII", len(strings), 0, 0, strings_start, 0)
    pool += b"".join(struct.pack("<I", o) for o in offsets) + body
    attrs = struct.pack("<IIIHBBI", 0xFFFFFFFF, 1, 2, 8, 0, value_type, 2 if value_type == 0x03 else 0) if with_attr else b""
    elem_body = struct.pack("<IIHHHHHH", 0xFFFFFFFF, 0, 20, 20, 1 if with_attr else 0, 0, 0, 0) + attrs
    elem = struct.pack("<HHI", 0x0102, 16, 16 + len(elem_body)) + struct.pack("<II", 1, 0xFFFFFFFF) + elem_body
    data = pool + elem
    return struct.pack("<HHI", file_type, 8, 8 + len(data)) + data


def _apk(path, manifest: bytes, encrypted_flag: bool = False):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("AndroidManifest.xml", manifest)
        z.writestr("classes.dex", b"dex\n035\x00")
    if encrypted_flag:  # протектор: ложный бит шифрования (Android его не замечает, Python отказывается читать)
        raw = bytearray(path.read_bytes())
        for sig in (b"PK\x03\x04", b"PK\x01\x02"):
            pos = raw.find(sig)
            while pos >= 0:
                flag_at = pos + (6 if sig == b"PK\x03\x04" else 8)
                raw[flag_at] |= 0x01
                pos = raw.find(sig, pos + 4)
        path.write_bytes(bytes(raw))
    return path


def test_normal_manifest(tmp_path):
    assert read_package_name(_apk(tmp_path / "a.apk", _axml("com.example.app"))) == "com.example.app"


def test_raw_value_wins_over_broken_value_type(tmp_path):
    assert read_package_name(_apk(tmp_path / "a.apk", _axml("com.ivolk.StrelkaGPS", value_type=0x00))) == "com.ivolk.StrelkaGPS"


def test_false_encryption_flag_is_ignored(tmp_path):
    apk = _apk(tmp_path / "a.apk", _axml("app.revanced.android.gms"), encrypted_flag=True)
    try:
        zipfile.ZipFile(apk).read("AndroidManifest.xml")
        raise AssertionError("ожидали, что Python откажется читать")
    except RuntimeError:
        pass
    assert read_package_name(apk) == "app.revanced.android.gms"


def test_guess_from_class_names_when_tag_is_missing(tmp_path):
    data = _axml("x", with_attr=False, extra_strings=("com.appsoft.carlauncher", "com.appsoft.carlauncher.MainActivity",
                                                        "com.appsoft.carlauncher.Service", "android.permission.INTERNET"))
    assert read_package_name(_apk(tmp_path / "a.apk", data)) == "com.appsoft.carlauncher"


def test_reason_for_the_log(tmp_path):
    xapk = tmp_path / "bundle.apk"
    with zipfile.ZipFile(xapk, "w") as z:
        z.writestr("base.apk", b"x")
        z.writestr("manifest.json", b"{}")
    assert read_package_name(xapk) is None and "набор APK" in why_unreadable(xapk)
    (tmp_path / "text.apk").write_bytes(b"hello")
    assert why_unreadable(tmp_path / "text.apk") == "это не zip-архив APK"
    junk = _apk(tmp_path / "junk.apk", b"\x07\x00\x08\x00garbage-garbage")
    assert read_package_name(junk) is None and why_unreadable(junk).startswith("манифест не разобран (начало: 0700")
