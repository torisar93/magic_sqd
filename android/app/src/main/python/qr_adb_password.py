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
import zlib
from ast import literal_eval

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

_SALT_RE = re.compile(r"salt\s*=\s*\[([^\]]*)\]")
_PASSWORD_RE = re.compile(r"password\s*=\s*\[([^\]]*)\]")
# Запасной вариант без квадратных скобок — см. app/qr_adb_password.py
# (desktop) за полным объяснением; 1:1 с эталонным скриптом поставщика
# (deploy/QR.py из release-бандла MonGuard).
_SALT_FALLBACK_RE = re.compile(r"salt\s*=\s*([^\n]+)")
_PASSWORD_FALLBACK_RE = re.compile(r"password\s*=\s*([^\n]+)")
# См. app/qr_adb_password.py (desktop) — пробел перед "sn" (не \b-граница)
# плюс последнее найденное совпадение, 1:1 с эталонным скриптом поставщика
# (deploy/QR.py из release-бандла MonGuard) — иначе в реальном bugreport-*.txt
# "sn=" (без пробела, через TAB — DrFusionService) встречается тысячи раз в
# несвязанных строках логов и даёт случайный неверный код.
_SN_RE = re.compile(r" sn\s*=\s*([A-Za-z0-9_.-]+)")

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
    """См. app/qr_adb_password.py (desktop) — raw может уже включать
    квадратные скобки целиком, если сработал запасной _SALT_FALLBACK_RE/
    _PASSWORD_FALLBACK_RE, не оборачиваем повторно в этом случае."""
    raw = raw.strip()
    literal = raw if raw.startswith("[") and raw.endswith("]") else f"[{raw}]"
    values = literal_eval(literal)
    if not isinstance(values, list) or not all(isinstance(v, int) for v in values):
        raise ValueError("salt/password должны быть списком чисел")
    return bytes(b & 0xFF for b in values)


# Как на ПК (app/qr_adb_password.py: _last_fields): отчёт кусками, а не целиком — logcat в сотни МБ
# при чтении целиком падал MemoryError (лог #1182).
_CHUNK_CHARS = 4 * 1024 * 1024
_OVERLAP_CHARS = 64 * 1024


def _last_fields(stream) -> tuple[str | None, str | None, str | None]:
    text = io.TextIOWrapper(stream, encoding="utf-8", errors="ignore", newline="")
    patterns = {"salt": _SALT_RE, "salt_fallback": _SALT_FALLBACK_RE, "password": _PASSWORD_RE,
                "password_fallback": _PASSWORD_FALLBACK_RE, "sn": _SN_RE}
    last: dict[str, str] = {}
    tail = ""
    chunk = text.read(_CHUNK_CHARS)
    while chunk:
        window = tail + chunk
        for key, pattern in patterns.items():
            for match in pattern.finditer(window):
                last[key] = match.group(1)
        tail = window[-_OVERLAP_CHARS:]
        chunk = text.read(_CHUNK_CHARS)
    salt = last["salt"] if "salt" in last else last.get("salt_fallback")
    password = last["password"] if "password" in last else last.get("password_fallback")
    return salt, password, last.get("sn")


def get_password_from_zip_b64(zip_b64: str) -> str:
    """Возвращает JSON-строку (Chaquopy отдаёт объекты в Kotlin неудобно —
    строка проще и однозначнее): {"ok": true, "code": ..., "sn": ...} или
    {"ok": false, "error": "..."}. Копий zip больше не сохраняем — см.
    app/qr_adb_password.py:get_adb_password (desktop, 2026-09-24)."""
    try:
        zip_bytes = base64.b64decode(zip_b64)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            txt_names = [name for name in zf.namelist() if name.lower().endswith(".txt")]
            if not txt_names:
                return json.dumps({"ok": False, "error": "Внутри bugreport-zip нет .txt файлов"})
            for name in txt_names:
                with zf.open(name) as stream:
                    salt_raw, password_raw, sn_raw = _last_fields(stream)
                if salt_raw is not None and password_raw is not None and sn_raw is not None:
                    salt = _parse_int_list(salt_raw)
                    password = _parse_int_list(password_raw)
                    sn = sn_raw.strip()
                    prk = _hkdf_extract(salt, password)
                    six_bytes = _hkdf_expand(prk, sn.encode("utf-8"), 6)
                    code = _encode_alphanumeric(six_bytes)
                    return json.dumps({"ok": True, "code": code, "sn": sn})
            return json.dumps({
                "ok": False,
                "error": "Поля salt/password/sn не найдены ни в одном .txt внутри bugreport-zip",
            })
    except (zipfile.BadZipFile, zlib.error, EOFError) as exc:
        # Недописанный отчёт: флешку вынули раньше, чем магнитола закончила запись. Раньше технику шло сырое
        # «Bad magic number for central directory» / «Corrupt extra field …» без окна (лог #1553); теперь —
        # окно «Магнитола не успела записать отчёт» (user_errors.js: qr_no_bugreport), как на ПК.
        return json.dumps({"ok": False, "error": f"Отчёт bugreport-zip на флешке повреждён или недописан ({exc})"})
    except Exception as exc:  # noqa: BLE001 - показать техническую причину как есть, дальше некому её разобрать
        return json.dumps({"ok": False, "error": str(exc)})
