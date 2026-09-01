"""Порт app/qr_adb_password.py (desktop) — тот же алгоритм (HKDF по
salt/password/sn из bugreport-*.zip платформы Geely без Wi-Fi, см. докстринг
desktop-версии за полным разбором механизма), но принимает уже прочитанные
байты zip-файла (base64), а не путь на диске: сама флешка на Android
читается через libaums (см. android/.../usb/UsbFlashQrAdb.kt), у контента
смонтированной через USB Host флешки нет обычного файлового пути — Kotlin
передаёт сюда уже готовые байты."""
from __future__ import annotations
import base64
import hashlib
import hmac
import io
import json
import re
import zipfile
from ast import literal_eval

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

_SALT_RE = re.compile(r"salt\s*=\s*\[([^\]]*)\]")
_PASSWORD_RE = re.compile(r"password\s*=\s*\[([^\]]*)\]")
_SN_RE = re.compile(r"\bsn\s*=\s*([A-Za-z0-9_.-]+)")


def _hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def _encode_alphanumeric(data: bytes) -> str:
    return "".join(_ALPHABET[b % len(_ALPHABET)] for b in data)


def _parse_int_list(raw: str) -> bytes:
    values = literal_eval(f"[{raw}]")
    if not isinstance(values, list) or not all(isinstance(v, int) for v in values):
        raise ValueError("salt/password должны быть списком чисел")
    return bytes(b & 0xFF for b in values)


def get_password_from_zip_b64(zip_b64: str) -> str:
    """Возвращает JSON-строку (Chaquopy отдаёт объекты в Kotlin неудобно —
    строка проще и однозначнее): {"ok": true, "code": ..., "sn": ...} или
    {"ok": false, "error": "..."}."""
    try:
        zip_bytes = base64.b64decode(zip_b64)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            txt_names = [name for name in zf.namelist() if name.lower().endswith(".txt")]
            if not txt_names:
                return json.dumps({"ok": False, "error": "Внутри bugreport-zip нет .txt файлов"})
            for name in txt_names:
                content = zf.read(name).decode("utf-8", errors="ignore")
                salt_match = _SALT_RE.search(content)
                password_match = _PASSWORD_RE.search(content)
                sn_match = _SN_RE.search(content)
                if salt_match and password_match and sn_match:
                    salt = _parse_int_list(salt_match.group(1))
                    password = _parse_int_list(password_match.group(1))
                    sn = sn_match.group(1).strip()
                    prk = _hkdf_extract(salt, password)
                    six_bytes = _hkdf_expand(prk, sn.encode("utf-8"), 6)
                    code = _encode_alphanumeric(six_bytes)
                    return json.dumps({"ok": True, "code": code, "sn": sn})
            return json.dumps({
                "ok": False,
                "error": "Поля salt/password/sn не найдены ни в одном .txt внутри bugreport-zip",
            })
    except Exception as exc:  # noqa: BLE001 - показать техническую причину как есть, дальше некому её разобрать
        return json.dumps({"ok": False, "error": str(exc)})
