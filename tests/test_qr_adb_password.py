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
2) Копий bugreport-*.zip больше нет (владелец, 2026-09-24: неверные коды
   объяснились шрифтом и двумя флагами на флешке у Jolion — «можно полностью
   убрать логирование»): get_adb_password ничего не сохраняет, QrAdbApi при
   запуске убирает прежнюю папку qr_adb_debug.
"""
from __future__ import annotations
import sys
import zipfile
from pathlib import Path

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


# --- без копий bugreport-*.zip ---------------------------------------------

def test_get_adb_password_saves_nothing(tmp_path):
    drive = tmp_path / "drive"
    _make_drive(drive, sn="SNXYZ")
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*"))

    result = qap.get_adb_password(drive)

    assert result["sn"] == "SNXYZ" and result["code"]
    assert set(result) == {"code", "sn", "logs_folder", "zip_name"}
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*")) == before


def test_qr_adb_api_removes_leftover_debug_copies(tmp_path):
    from app.web.api import qr_adb_api

    leftover = tmp_path / "qr_adb_debug"
    leftover.mkdir()
    (leftover / "20260922_120000_bugreport-1.zip").write_bytes(b"zip")
    (tmp_path / "keep.txt").write_text("не наше — не трогаем")

    qr_adb_api.QrAdbApi(tmp_path, tmp_path / "cars")

    assert not leftover.exists()
    assert (tmp_path / "keep.txt").exists()


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

    assert result == {"ok": True, "removed": []}  # removed — см. tests/test_flash_trigger_flags.py
    # ДО фикса файл ушёл бы в /Volumes/ с буквальным "\" в имени, мимо самой флешки.
    assert (drive / "svlog.flag").read_text() == "trigger-content"


# --- сверка с эталонным скриптом поставщика (deploy/QR.py, MonGuard) -------
#
# 2026-09-22: жалоба клиента "неправильно генерируется пароль" — заново
# сверено с deploy/QR.py и deploy/QR_mac.py из свежего релизного бандла
# MonGuard (1.8.1). Сам HKDF-алгоритм (hkdf_extract/hkdf_expand/кодирование
# в alphanumeric) оказался идентичен побайтово — см. золотой тест ниже,
# посчитанный РЕАЛЬНЫМ запуском их QR.py, не переписан по памяти. Найдено
# одно настоящее отличие: у них ЕСТЬ запасной regex без квадратных скобок,
# если основной (со скобками) ничего не нашёл — у нас раньше не было
# (см. _SALT_FALLBACK_RE/_PASSWORD_FALLBACK_RE в app/qr_adb_password.py).

def test_compute_auth_code_matches_monguard_reference_script():
    """Золотой тест: salt/password/sn ниже прогнаны через настоящий
    deploy/QR.py (release_1.8.1_20342_monguard_app) — результат "dOjtwQ"
    получен ИХ кодом, не пересчитан вручную. Если кто-то случайно поменяет
    порядок аргументов hkdf_extract/hkdf_expand или байт счётчика — тест
    упадёт."""
    salt = bytes(range(16))
    password = bytes(range(16, 32))
    assert qap.compute_auth_code(salt, password, "GOLDEN-SN-42") == "dOjtwQ"


def test_extract_fields_falls_back_to_unbracketed_salt_and_password(tmp_path):
    """Реальное отличие от эталонного скрипта, найденное 2026-09-22: на
    части прошивок salt/password в логе могут быть напечатаны БЕЗ квадратных
    скобок — раньше наш разбор в этом случае просто ничего не находил."""
    zip_path = tmp_path / "bugreport-1.zip"
    content = (
        "...шум логката...\n"
        "salt = 1, 2, 3, 4\n"
        "password = 5, 6, 7, 8\n"
        "09-21 12:00:00.000  1234  1234 I QRCodeDialog: sn=NOBRACKETS1\n"
    )
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("bugreport-dump.txt", content)

    salt, password, sn = qap._extract_fields(zip_path)

    assert salt == bytes([1, 2, 3, 4])
    assert password == bytes([5, 6, 7, 8])
    assert sn == "NOBRACKETS1"


def test_extract_fields_prefers_bracketed_pattern_when_both_present(tmp_path):
    """Основной (со скобками) паттерн должен побеждать всегда, когда сам
    находит совпадение — запасной вариант только на крайний случай, как и в
    эталонном скрипте (там та же проверка "if not salt_matches: ...")."""
    zip_path = tmp_path / "bugreport-1.zip"
    content = (
        "salt = [9, 9, 9] ...\n"
        "password = [8, 8, 8] ...\n"
        "09-21 12:00:00.000  1234  1234 I QRCodeDialog: sn=BRACKETED1\n"
    )
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("bugreport-dump.txt", content)

    salt, password, sn = qap._extract_fields(zip_path)

    assert salt == bytes([9, 9, 9])
    assert password == bytes([8, 8, 8])


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
    assert "debug_copy" not in result and not (tmp_path / "qr_adb_debug").exists()
