"""Вычисление пароля ADB по QR-коду для моделей платформы Geely без Wi-Fi
(Cityray, Atlas/Preface без значка Wi-Fi и т.п. — см. блок редактора
инструкций "QR-код ADB (флешка)" в app/instruction_html.py).

Механизм (разобран из служебного инструмента поставщика платформы,
реализация здесь написана с нуля, без копирования кода): магнитола при
обнаружении файла-триггера svlog.flag в корне вставленной флешки выгружает
на неё диагностический дамп — папку logs_<таймстемп>/bugreport-*.zip,
внутри которого лежит .txt с тремя полями: salt, password (байтовые
массивы) и sn (серийный номер устройства). Итоговый код для экрана ADB —
HKDF (RFC 5869): HMAC-SHA256(salt, password) как extract-шаг, затем
expand с sn в качестве контекста ("info") — код привязан к конкретному
устройству. Первые 6 байт результата кодируются в alphanumeric-алфавит
(0-9, A-Z, a-z) по остатку от деления на 62 — это и есть итоговый код."""
from __future__ import annotations
import hashlib
import hmac
import re
import zipfile
from ast import literal_eval
from pathlib import Path


class QrAdbError(RuntimeError):
    """Понятная причина, почему код не удалось вычислить — показывается
    технику в диалоге как есть, без дополнительной обработки."""


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


def find_latest_logs_folder(drive_root: Path) -> Path | None:
    """Самая свежая по имени папка logs_* в корне флешки — имя содержит
    таймстемп, поэтому обычная сортировка строк даёт хронологический
    порядок без разбора формата даты."""
    candidates = [p for p in drive_root.glob("logs_*") if p.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.name)


def find_bugreport_zip(logs_folder: Path) -> Path | None:
    candidates = list(logs_folder.glob("bugreport-*.zip"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.name)


def _parse_int_list(raw: str) -> list[int]:
    try:
        values = literal_eval(f"[{raw}]")
    except (ValueError, SyntaxError) as exc:
        raise QrAdbError(f"Не удалось разобрать числовой список: {exc}") from exc
    if not isinstance(values, list) or not all(isinstance(v, int) for v in values):
        raise QrAdbError("salt/password должны быть списком чисел")
    return values


def _extract_fields(zip_path: Path) -> tuple[bytes, bytes, str]:
    """Внутри bugreport-*.zip несколько .txt — поля могут быть не в первом
    из них, поэтому проверяем все, пока не найдём все три сразу."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise QrAdbError(f"Не удалось открыть {zip_path.name}: {exc}") from exc
    with zf:
        txt_names = [name for name in zf.namelist() if name.lower().endswith(".txt")]
        if not txt_names:
            raise QrAdbError(f"Внутри {zip_path.name} нет .txt файлов")
        for name in txt_names:
            content = zf.read(name).decode("utf-8", errors="ignore")
            salt_match = _SALT_RE.search(content)
            password_match = _PASSWORD_RE.search(content)
            sn_match = _SN_RE.search(content)
            if salt_match and password_match and sn_match:
                salt = bytes(b & 0xFF for b in _parse_int_list(salt_match.group(1)))
                password = bytes(b & 0xFF for b in _parse_int_list(password_match.group(1)))
                return salt, password, sn_match.group(1).strip()
    raise QrAdbError(f"Поля salt/password/sn не найдены ни в одном .txt внутри {zip_path.name}")


def compute_auth_code(salt: bytes, password: bytes, sn: str) -> str:
    prk = _hkdf_extract(salt, password)
    six_bytes = _hkdf_expand(prk, sn.encode("utf-8"), 6)
    return _encode_alphanumeric(six_bytes)


def get_adb_password(drive_root: Path) -> dict:
    """Полный проход: флешка → папка logs_* → bugreport-*.zip → код.
    Бросает QrAdbError с понятной причиной на первом шаге, где не найдено
    ожидаемое (тексту исключения можно доверять как готовому сообщению
    для техника — см. app/web/api/qr_adb_api.py)."""
    if not drive_root.is_dir():
        raise QrAdbError(f"Диск {drive_root} недоступен")
    logs_folder = find_latest_logs_folder(drive_root)
    if logs_folder is None:
        raise QrAdbError(
            "На флешке не найдена папка logs_*. Проверьте: файл svlog.flag был "
            "в корне флешки ДО того, как её вставили в магнитолу, и на экране "
            "магнитолы появилась надпись «QNX OK»."
        )
    zip_path = find_bugreport_zip(logs_folder)
    if zip_path is None:
        raise QrAdbError(f"В папке {logs_folder.name} не найден файл bugreport-*.zip")
    salt, password, sn = _extract_fields(zip_path)
    code = compute_auth_code(salt, password, sn)
    return {"code": code, "sn": sn, "logs_folder": logs_folder.name, "zip_name": zip_path.name}
