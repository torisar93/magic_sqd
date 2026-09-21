"""Жалобы клиентов на неверный пароль ADB по QR-коду (2026-09-21), а в
постоянном журнале сессии — вообще ничего об этой попытке (это исправлено
на стороне JS/Kotlin — см. память проекта). Здесь — то, что проверяемо на
уровне Python:

1) app/usb_utils.py:drive_root_path — app/web/api/qr_adb_api.py раньше
   ВСЕГДА строил путь как Path(f"{drive_letter}\\...") (Windows-стиль),
   даже на macOS, где drive_letter уже полный путь точки монтирования
   ("/Volumes/..." — см. usb_utils_mac.py: DriveInfo.letter). На macOS это
   ломало запись/чтение файлов на флешку целиком — вероятная реальная
   причина части жалоб.
2) app/qr_adb_password.py:save_debug_copy/get_adb_password(debug_dir=...) —
   временная мера, пока формула не подтверждена 100%-но надёжной: сохранять
   исходный bugreport-*.zip ЦЕЛИКОМ, даже если разбор его полей не удался.
"""
from __future__ import annotations
import os
import sys
import zipfile
from pathlib import Path

import pytest

from app import qr_adb_password as qap
from app import usb_utils


def _make_bugreport_zip(path: Path, salt=None, password=None, sn="ABC123") -> Path:
    salt = salt if salt is not None else list(range(16))
    password = password if password is not None else list(range(16, 32))
    content = (
        f"...шум логката... salt = {salt} ...шум... password = {password} ...шум...\n"
        f"09-21 12:00:00.000  1234  1234 I QRCodeDialog: sn={sn}\n"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("bugreport-dump.txt", content)
    return path


def _make_drive(drive: Path, **kwargs) -> Path:
    """Флешка с папкой logs_*/bugreport-*.zip внутри — та же структура,
    что ищет find_latest_logs_folder/find_bugreport_zip."""
    logs_folder = drive / "logs_20260921_120000"
    logs_folder.mkdir(parents=True)
    zip_path = logs_folder / "bugreport-1.zip"
    _make_bugreport_zip(zip_path, **kwargs)
    return zip_path


# --- drive_root_path (баг с обратным слешем на не-Windows) -----------------

def test_drive_root_path_needs_trailing_backslash_only_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert str(usb_utils.drive_root_path("E:")) == "E:\\"


def test_drive_root_path_uses_mount_path_as_is_elsewhere(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert str(usb_utils.drive_root_path("/Volumes/CARINSTALL")) == "/Volumes/CARINSTALL"
    monkeypatch.setattr(sys, "platform", "linux")
    assert str(usb_utils.drive_root_path("/media/tech/CARINSTALL")) == "/media/tech/CARINSTALL"


# --- save_debug_copy / _trim_debug_dir --------------------------------------

def test_save_debug_copy_copies_whole_file_byte_for_byte(tmp_path):
    zip_path = _make_bugreport_zip(tmp_path / "bugreport-1.zip")
    debug_dir = tmp_path / "debug"

    dest = qap.save_debug_copy(debug_dir, zip_path)

    assert dest is not None and dest.exists()
    assert dest.read_bytes() == zip_path.read_bytes()
    assert dest.name.endswith("_bugreport-1.zip")


def test_save_debug_copy_returns_none_on_failure_without_raising(tmp_path):
    zip_path = _make_bugreport_zip(tmp_path / "bugreport.zip")
    blocked = tmp_path / "blocked"
    blocked.write_text("это файл, не папка")  # mkdir(parents=True, exist_ok=True) бросит FileExistsError

    assert qap.save_debug_copy(blocked, zip_path) is None


def test_trim_debug_dir_keeps_only_the_newest_files(tmp_path):
    debug_dir = tmp_path / "debug"
    debug_dir.mkdir()
    for i in range(5):
        path = debug_dir / f"file_{i}.zip"
        path.write_bytes(b"x")
        os.utime(path, (i, i))  # разные mtime, независимо от скорости выполнения теста

    qap._trim_debug_dir(debug_dir, keep=3)

    remaining = {p.name for p in debug_dir.glob("*.zip")}
    assert remaining == {"file_2.zip", "file_3.zip", "file_4.zip"}


# --- get_adb_password(debug_dir=...) ----------------------------------------

def test_get_adb_password_saves_debug_copy_on_success(tmp_path):
    drive = tmp_path / "drive"
    zip_path = _make_drive(drive, sn="SNXYZ")
    debug_dir = tmp_path / "debug"

    result = qap.get_adb_password(drive, debug_dir=debug_dir)

    assert result["sn"] == "SNXYZ" and result["code"]
    assert result["debug_copy"] is not None
    saved = Path(result["debug_copy"])
    assert saved.exists() and saved.read_bytes() == zip_path.read_bytes()


def test_get_adb_password_saves_debug_copy_even_when_fields_missing(tmp_path):
    """Именно этот случай важнее всего — если разбор упал, но zip сохранён,
    можно разобрать причину офлайн, не имея больше доступа к флешке техника."""
    drive = tmp_path / "drive"
    logs_folder = drive / "logs_20260921_120000"
    logs_folder.mkdir(parents=True)
    zip_path = logs_folder / "bugreport-1.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("bugreport-dump.txt", "тут нет ни salt, ни password, ни sn")
    debug_dir = tmp_path / "debug"

    with pytest.raises(qap.QrAdbError):
        qap.get_adb_password(drive, debug_dir=debug_dir)

    saved = list(debug_dir.glob("*.zip"))
    assert len(saved) == 1
    assert saved[0].read_bytes() == zip_path.read_bytes()


def test_get_adb_password_without_debug_dir_saves_nothing(tmp_path):
    drive = tmp_path / "drive"
    _make_drive(drive)

    result = qap.get_adb_password(drive)  # debug_dir не передан вовсе — старое поведение

    assert result["debug_copy"] is None


# --- сквозной сценарий: тот самый macOS-баг, целиком через QrAdbApi --------

def test_qr_adb_api_write_flag_reaches_real_mount_path_on_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    from app.web.api import qr_adb_api

    cars_dir = tmp_path / "cars"
    (cars_dir / "_shared").mkdir(parents=True)
    (cars_dir / "_shared" / "svlog.flag").write_text("trigger-content")
    drive = tmp_path / "Volumes" / "CARINSTALL"  # тот же формат, что и usb_utils_mac.py: DriveInfo.letter
    drive.mkdir(parents=True)
    api = qr_adb_api.QrAdbApi(tmp_path, cars_dir)

    result = api.write_flag(str(drive))

    assert result == {"ok": True}
    # ДО фикса файл ушёл бы в /Volumes/ с буквальным "\" в имени, мимо самой флешки.
    assert (drive / "svlog.flag").read_text() == "trigger-content"


def test_qr_adb_api_get_password_reaches_real_mount_path_on_macos(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    from app.web.api import qr_adb_api

    cars_dir = tmp_path / "cars"
    cars_dir.mkdir(parents=True)
    drive = tmp_path / "Volumes" / "CARINSTALL"
    _make_drive(drive, sn="MACSN1")
    api = qr_adb_api.QrAdbApi(tmp_path, cars_dir)

    result = api.get_password(str(drive))

    assert result["ok"] is True
    assert result["sn"] == "MACSN1"
    assert result["debug_copy"] is not None  # base_dir/qr_adb_debug — передан автоматически
