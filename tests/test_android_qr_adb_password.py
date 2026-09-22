"""android/app/src/main/python/qr_adb_password.py — порт desktop-версии
(см. tests/test_qr_adb_password.py за полным разбором и золотым тестом
против эталонного скрипта поставщика, deploy/QR.py из release-бандла
MonGuard). Здесь — то же самое, но через реальный JSON-интерфейс
get_password_from_zip_b64, которым пользуется Kotlin (WebBridge.kt)."""
from __future__ import annotations
import base64
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULE_PATH = ROOT / "android/app/src/main/python/qr_adb_password.py"


@pytest.fixture(scope="module")
def qap_android():
    """См. tests/test_android_wizard_spec_usb_apks.py — тот же приём
    импорта по прямому пути (Chaquopy source root, не app/)."""
    spec = importlib.util.spec_from_file_location("android_qr_adb_password", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _make_zip_b64(content: str) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("bugreport-dump.txt", content)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def test_matches_monguard_reference_script(qap_android):
    """Тот же золотой вход/выход, что и на desktop (см.
    test_qr_adb_password.py:test_compute_auth_code_matches_monguard_reference_script)
    — оба порта должны совпадать друг с другом и с эталоном."""
    salt = list(range(16))
    password = list(range(16, 32))
    content = (
        f"...шум... salt = {salt} ...\n"
        f"...шум... password = {password} ...\n"
        f"09-21 12:00:00.000  1234  1234 I QRCodeDialog: sn=GOLDEN-SN-42\n"
    )
    result = json.loads(qap_android.get_password_from_zip_b64(_make_zip_b64(content)))
    assert result["ok"] is True
    assert result["code"] == "dOjtwQ"
    assert result["sn"] == "GOLDEN-SN-42"


def test_falls_back_to_unbracketed_salt_and_password(qap_android):
    """Тот же реальный пробел в разборе, что и на desktop — раньше на
    прошивках без квадратных скобок в логе Android-версия тоже ничего не
    находила."""
    content = (
        "...шум логката...\n"
        "salt = 1, 2, 3, 4\n"
        "password = 5, 6, 7, 8\n"
        "09-21 12:00:00.000  1234  1234 I QRCodeDialog: sn=NOBRACKETS1\n"
    )
    result = json.loads(qap_android.get_password_from_zip_b64(_make_zip_b64(content)))
    assert result["ok"] is True
    assert result["sn"] == "NOBRACKETS1"


def test_reports_error_when_fields_truly_missing(qap_android):
    content = "тут нет ни salt, ни password, ни sn"
    result = json.loads(qap_android.get_password_from_zip_b64(_make_zip_b64(content)))
    assert result["ok"] is False
    assert "salt/password/sn" in result["error"]
