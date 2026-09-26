"""QR ADB: отчёт магнитолы читается кусками, а не целиком (лог #1182, ПК 1.0.41 — «QR ADB: не удалось
получить пароль — MemoryError», затем «Unable to allocate output buffer»: bugreport-*.txt — полный
logcat в сотни МБ). Результат должен совпадать с прежним разбором всего текста (findall(...)[-1],
запасной вариант без скобок — только если со скобками поля нет нигде), где бы ни прошла граница кусков.
Обе копии: app/qr_adb_password.py (ПК) и android/app/src/main/python/qr_adb_password.py."""
from __future__ import annotations
import base64
import importlib.util
import io
import json
import random
import zipfile
from pathlib import Path

import pytest

from app import qr_adb_password as desktop

ROOT = Path(__file__).resolve().parents[1]


def _android():
    spec = importlib.util.spec_from_file_location(
        "android_qr_adb_password_streaming", ROOT / "android/app/src/main/python/qr_adb_password.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULES = [desktop, _android()]


def _whole_text(module, content: str):
    """Прежний разбор — весь текст целиком."""
    salt = module._SALT_RE.findall(content) or module._SALT_FALLBACK_RE.findall(content)
    password = module._PASSWORD_RE.findall(content) or module._PASSWORD_FALLBACK_RE.findall(content)
    sn = module._SN_RE.findall(content)
    return (salt[-1] if salt else None, password[-1] if password else None, sn[-1] if sn else None)


def _logcat(rng: random.Random, bracketed: bool) -> str:
    lines = []
    for i in range(400):
        roll = rng.random()
        if roll < 0.03:
            numbers = [rng.randrange(-128, 128) for _ in range(16)]
            value = str(numbers) if bracketed else ", ".join(map(str, numbers))
            lines.append(f"09-26 11:{i % 60:02d}:00.000 1 1 I QRCodeDialog: salt = {value}")
        elif roll < 0.06:
            numbers = [rng.randrange(-128, 128) for _ in range(16)]
            value = str(numbers) if bracketed else ", ".join(map(str, numbers))
            lines.append(f"09-26 11:{i % 60:02d}:00.000 1 1 I QRCodeDialog: password = {value}")
        elif roll < 0.09:
            lines.append(f"09-26 11:{i % 60:02d}:00.000 1 1 I QRCodeDialog: sn=DHU{rng.randrange(10**9)}")
        elif roll < 0.2:
            lines.append(f"09-26 11:00:00.000 1 1 I DrFusionService: [DR]\tsn={i},time={i * 7}")
        else:
            lines.append("09-26 11:00:00.000 1 1 D шум логката " + "ё" * rng.randrange(0, 40))
    return "\r\n".join(lines) if rng.random() < 0.5 else "\n".join(lines)


@pytest.mark.parametrize("module", MODULES, ids=["desktop", "android"])
@pytest.mark.parametrize("chunk", [1, 7, 64, 333, 100_000])
def test_chunked_scan_matches_whole_text(module, chunk, monkeypatch):
    monkeypatch.setattr(module, "_CHUNK_CHARS", chunk)
    monkeypatch.setattr(module, "_OVERLAP_CHARS", 512)
    rng = random.Random(chunk)
    for case in range(30):
        content = _logcat(rng, bracketed=case % 3 != 0)
        if case % 5 == 0:  # и скобки, и без скобок — побеждают поля со скобками
            content += "\nI QRCodeDialog: salt = 1, 2, 3\nI QRCodeDialog: salt = [9, 8, 7]\n"
        stream = io.BytesIO(content.encode("utf-8"))
        assert module._last_fields(stream) == _whole_text(module, content), (chunk, case)


@pytest.mark.parametrize("module", MODULES, ids=["desktop", "android"])
def test_no_whole_member_read(module):
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "zf.read(" not in source and "zf.open(name)" in source


def test_desktop_password_from_zip_with_small_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(desktop, "_CHUNK_CHARS", 5)
    zip_path = tmp_path / "bugreport-1.zip"
    content = ("шум " * 50 + "salt = " + str(list(range(16))) + " шум password = " + str(list(range(16, 32)))
               + "\n09-21 12:00:00.000 1 1 I QRCodeDialog: sn=ABC123\n")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bugreport-dump.txt", content)
    salt, password, sn = desktop._extract_fields(zip_path)
    assert (salt, password, sn) == (bytes(range(16)), bytes(range(16, 32)), "ABC123")


def test_android_password_from_zip_with_small_chunks(monkeypatch):
    module = MODULES[1]
    monkeypatch.setattr(module, "_CHUNK_CHARS", 5)
    buffer = io.BytesIO()
    content = ("шум " * 50 + "salt = " + str(list(range(16))) + " шум password = " + str(list(range(16, 32)))
               + "\n09-21 12:00:00.000 1 1 I QRCodeDialog: sn=ABC123\n")
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bugreport-dump.txt", content)
    result = json.loads(module.get_password_from_zip_b64(base64.b64encode(buffer.getvalue()).decode()))
    assert result["ok"] and result["sn"] == "ABC123"
    assert result["code"] == desktop.compute_auth_code(bytes(range(16)), bytes(range(16, 32)), "ABC123")
