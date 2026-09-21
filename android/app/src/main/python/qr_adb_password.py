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
import time
import zipfile
from ast import literal_eval
from pathlib import Path

_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

_SALT_RE = re.compile(r"salt\s*=\s*\[([^\]]*)\]")
_PASSWORD_RE = re.compile(r"password\s*=\s*\[([^\]]*)\]")
# См. app/qr_adb_password.py (desktop) — пробел перед "sn" (не \b-граница)
# плюс последнее найденное совпадение, 1:1 с эталонным скриптом поставщика
# (deploy/QR.py из release-бандла MonGuard) — иначе в реальном bugreport-*.txt
# "sn=" (без пробела, через TAB — DrFusionService) встречается тысячи раз в
# несвязанных строках логов и даёт случайный неверный код.
_SN_RE = re.compile(r" sn\s*=\s*([A-Za-z0-9_.-]+)")

# См. app/qr_adb_password.py (desktop) — то же временное решение (жалобы
# клиентов на неверный пароль, 2026-09-21) и то же ограничение числа копий.
_DEBUG_KEEP = 40


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


def _save_debug_copy(debug_dir_str: str, zip_bytes: bytes) -> str | None:
    """Сохраняет исходные байты bugreport-*.zip ЦЕЛИКОМ — та же временная
    мера, что и на desktop (см. app/qr_adb_password.py:save_debug_copy) —
    без неё жалобу «пароль неверный» нельзя ни подтвердить, ни опровергнуть
    (текстовый лог даёт только код+SN, а не сырые данные, из которых код
    получен). debug_dir_str пустой — вызывающая сторона ещё не готова
    передать путь (не должно происходить в норме, но не бросаем исключение
    из-за этого). Не бросает исключений сама — сбой сохранения debug-копии
    не должен портить основной результат."""
    if not debug_dir_str:
        return None
    try:
        debug_dir = Path(debug_dir_str)
        debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        dest = debug_dir / f"{stamp}_bugreport.zip"
        dest.write_bytes(zip_bytes)
        files = sorted(debug_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime)
        for path in (files[:-_DEBUG_KEEP] if len(files) > _DEBUG_KEEP else []):
            try:
                path.unlink()
            except OSError:
                pass
        return str(dest)
    except OSError:
        return None


def get_password_from_zip_b64(zip_b64: str, debug_dir: str = "") -> str:
    """Возвращает JSON-строку (Chaquopy отдаёт объекты в Kotlin неудобно —
    строка проще и однозначнее): {"ok": true, "code": ..., "sn": ...,
    "debug_copy": ...} или {"ok": false, "error": "...", "debug_copy": ...}.
    debug_dir (если не пустой) — куда сохранить байты zip ЦЕЛИКОМ, ДО
    попытки разобрать поля (см. _save_debug_copy) — если разбор ниже
    бросит исключение, копия всё равно уже сохранена."""
    debug_copy = None
    try:
        zip_bytes = base64.b64decode(zip_b64)
        debug_copy = _save_debug_copy(debug_dir, zip_bytes)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            txt_names = [name for name in zf.namelist() if name.lower().endswith(".txt")]
            if not txt_names:
                return json.dumps({"ok": False, "error": "Внутри bugreport-zip нет .txt файлов", "debug_copy": debug_copy})
            for name in txt_names:
                content = zf.read(name).decode("utf-8", errors="ignore")
                salt_matches = _SALT_RE.findall(content)
                password_matches = _PASSWORD_RE.findall(content)
                sn_matches = _SN_RE.findall(content)
                if salt_matches and password_matches and sn_matches:
                    salt = _parse_int_list(salt_matches[-1])
                    password = _parse_int_list(password_matches[-1])
                    sn = sn_matches[-1].strip()
                    prk = _hkdf_extract(salt, password)
                    six_bytes = _hkdf_expand(prk, sn.encode("utf-8"), 6)
                    code = _encode_alphanumeric(six_bytes)
                    return json.dumps({"ok": True, "code": code, "sn": sn, "debug_copy": debug_copy})
            return json.dumps({
                "ok": False,
                "error": "Поля salt/password/sn не найдены ни в одном .txt внутри bugreport-zip",
                "debug_copy": debug_copy,
            })
    except Exception as exc:  # noqa: BLE001 - показать техническую причину как есть, дальше некому её разобрать
        return json.dumps({"ok": False, "error": str(exc), "debug_copy": debug_copy})
