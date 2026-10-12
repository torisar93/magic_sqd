"""Запись на флешку: файл на месте папки из комплекта (остался от прошлой записи или другой программы) не роняет
запись целиком. Логи №5376, №5380 (ПК 1.1.5, Belgee X70 Дорест (Atlas Pro)): в комплекте — папка-триггер
b832bc…/ со скриптом, а на флешке техника — файл с тем же именем; запись падала «[WinError 183] Невозможно создать
файл, так как он уже существует», 0 из 7 файлов. Теперь файл убирается и на его месте пишется папка — ПК
(app/usb_context.py: _make_dirs) и Android (UsbFlashWrite.kt: writeFileToUsb)."""
from __future__ import annotations

import threading
from pathlib import Path

from app.usb_context import UsbContext

TRIGGER = "b832bc61472727635baffcf25dd28e9f239273e2"
ANDROID_USB = Path(__file__).resolve().parents[1] / "android/app/src/main/java/ru/magicsqd/mobile/usb"


def make_ctx(tmp_path, logs):
    return UsbContext(drive_root=tmp_path / "drive", model_dir=tmp_path / "model", selected_apks=[],
                      log_fn=logs.append, cancel_flag=threading.Event())


def test_kit_folder_replaces_a_file_with_the_same_name(tmp_path):
    kit = tmp_path / "kit"
    (kit / TRIGGER).mkdir(parents=True)
    (kit / TRIGGER / "d4ad0e79.sh").write_text("#!/system/bin/sh\n", encoding="utf-8")
    (kit / "done.sh").write_text("echo done\n", encoding="utf-8")
    drive = tmp_path / "drive"
    drive.mkdir()
    (drive / TRIGGER).write_bytes(b"old")  # файл, а не папка
    logs = []
    make_ctx(tmp_path, logs).copy_dir(kit, "")
    assert (drive / TRIGGER).is_dir() and (drive / TRIGGER / "d4ad0e79.sh").is_file()
    assert (drive / "done.sh").read_text(encoding="utf-8") == "echo done\n"
    assert any(f"«{TRIGGER}»" in line and "убран" in line for line in logs)


def test_copy_file_into_a_folder_blocked_by_a_file(tmp_path):
    src = tmp_path / "x.apk"
    src.write_bytes(b"apk")
    drive = tmp_path / "drive"
    (drive / "magic_sqd").mkdir(parents=True)
    (drive / "magic_sqd" / "for_install").write_bytes(b"")  # пустой файл на месте подпапки
    logs = []
    make_ctx(tmp_path, logs).copy_file(src, "magic_sqd/for_install/x.apk")
    assert (drive / "magic_sqd" / "for_install" / "x.apk").read_bytes() == b"apk"


def test_existing_folders_are_kept(tmp_path):
    drive = tmp_path / "drive"
    (drive / "apps").mkdir(parents=True)
    (drive / "apps" / "keep.txt").write_text("keep", encoding="utf-8")
    src = tmp_path / "y.apk"
    src.write_bytes(b"y")
    logs = []
    make_ctx(tmp_path, logs).copy_file(src, "apps/y.apk")
    assert (drive / "apps" / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert not any("убран" in line for line in logs)


def test_android_does_the_same():
    code = (ANDROID_USB / "UsbFlashWrite.kt").read_text(encoding="utf-8")
    write = code[code.index("fun writeFileToUsb("):]
    assert "if (found != null && !found.isDirectory)" in write and "found.delete()" in write
    assert "dir = if (found != null && found.isDirectory) found else dir.createDirectory(name)" in write


def test_android_incomplete_qr_report_has_a_reason():
    # «length=2; index=2» из библиотеки флешки на недописанном отчёте (логи №1619, №5035, №5306) — теперь причина,
    # которую ловит окно «Магнитола не успела записать отчёт» (js/user_errors.js: qr_no_bugreport).
    code = (ANDROID_USB / "UsbFlashQrAdb.kt").read_text(encoding="utf-8")
    read = code[code.index("fun readQrAdbBugreportZip("):]
    assert read.count("catch (e: IndexOutOfBoundsException)") == 2
    assert read.count("на флешке повреждён или недописан") == 2
