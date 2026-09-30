"""«Работа в движении» (владелец, 2026-09-30: на Haval Dargo 2026 не работает видео в движении). motion_patch
вписывает <meta-data android:name="distractionOptimized" android:value="true"/> во все окна приложения прямо в
двоичный AndroidManifest.xml и пересобирает APK. Фикстура tests/fixtures/motion_manifest.axml.b64 — настоящий
манифест, собранный aapt2 (activity/activity-alias, одна уже помечена true, одна помечена false, есть provider —
его трогать нельзя). Копии motion_patch.py на ПК и Android сверяются здесь же."""
from __future__ import annotations
import base64
import io
import zipfile
from pathlib import Path

import pytest

from app import motion_patch

ROOT = Path(__file__).resolve().parents[1]
AXML = base64.b64decode((ROOT / "tests/fixtures/motion_manifest.axml.b64").read_text())


def test_marks_every_window_once_and_is_idempotent():
    # Фикстура: 5 окон (Main, Player, Alias, AlreadyOk=true, StaleFlag=false); provider — не окно.
    assert motion_patch.count_optimized(AXML) == (5, 1)
    patched, marked = motion_patch.patch_manifest(AXML)
    assert marked == 4  # четыре без пометки; AlreadyOk уже помечена
    assert motion_patch.count_optimized(patched) == (5, 5)
    again, marked2 = motion_patch.patch_manifest(patched)
    assert marked2 == 0 and again == patched  # второй раз менять нечего — тот же байт-в-байт


def test_stale_false_flag_is_replaced_not_duplicated():
    patched, _ = motion_patch.patch_manifest(AXML)
    # У StaleFlag было value="false" — должно стать true, а не появиться вторая meta-data рядом.
    total, optimized = motion_patch.count_optimized(patched)
    assert total == optimized == 5
    # Двойной прогон не плодит строки в пуле (иначе манифест бы пух с каждым патчем).
    assert len(motion_patch.patch_manifest(patched)[0]) == len(patched)


def _apk(manifest: bytes, method=zipfile.ZIP_DEFLATED, extra=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as z:
        z.writestr("AndroidManifest.xml", manifest)
        z.writestr("classes.dex", b"dex-bytes" * 700)
        z.writestr("resources.arsc", b"arsc" * 500)
        for name, data in (extra or {}).items():
            z.writestr(name, data)
    return buf.getvalue()


def test_patch_apk_rebuilds_a_valid_zip(tmp_path):
    src, dst = tmp_path / "in.apk", tmp_path / "out.apk"
    src.write_bytes(_apk(AXML))
    assert motion_patch.patch_apk(src, dst) == 4
    with zipfile.ZipFile(dst) as z:
        assert z.testzip() is None
        assert motion_patch.count_optimized(z.read("AndroidManifest.xml")) == (5, 5)
        assert z.read("classes.dex") == b"dex-bytes" * 700  # прочие записи не тронуты
        assert z.read("resources.arsc") == b"arsc" * 500


def test_patch_apk_handles_stored_manifest(tmp_path):
    # Часть APK хранит AndroidManifest.xml без сжатия (method=0) — тоже должен пропатчиться.
    src, dst = tmp_path / "in.apk", tmp_path / "out.apk"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("AndroidManifest.xml"), AXML, compress_type=zipfile.ZIP_STORED)
        z.writestr("classes.dex", b"x" * 100)
    src.write_bytes(buf.getvalue())
    assert motion_patch.patch_apk(src, dst) == 4
    with zipfile.ZipFile(dst) as z:
        assert motion_patch.count_optimized(z.read("AndroidManifest.xml")) == (5, 5)


def test_patch_apk_skips_when_all_windows_marked(tmp_path):
    once = tmp_path / "once.apk"
    once.write_bytes(_apk(motion_patch.patch_manifest(AXML)[0]))
    twice = tmp_path / "twice.apk"
    assert motion_patch.patch_apk(once, twice) == 0  # менять нечего
    assert not twice.exists()  # выход не создаётся — устанавливать нечего заново


def test_patch_apk_rejects_non_apk(tmp_path):
    bad = tmp_path / "bad.apk"
    bad.write_bytes(b"not a zip at all" * 10)
    with pytest.raises(motion_patch.MotionPatchError):
        motion_patch.patch_apk(bad, tmp_path / "out.apk")


def test_patch_manifest_rejects_garbage():
    for junk in (b"", b"\x00\x00", b"\x03\x00\x08\x00\xff\xff\xff\x7f", AXML[:20]):
        with pytest.raises(motion_patch.MotionPatchError):
            motion_patch.patch_manifest(junk)


def test_car_service_verdict_reads_allowlist():
    accepted = ("...\n  **System allowlist**\n    com.example.video\n    com.other.app\n  **Something else**\n  x")
    assert motion_patch.car_service_verdict(accepted, "com.example.video") == "accepted"
    assert motion_patch.car_service_verdict(accepted, "com.example.vid") == "rejected"  # не подстрокой
    assert motion_patch.car_service_verdict(accepted, "com.missing") == "rejected"
    whitelist = "**System whitelist**\n com.example.video\n**end**"
    assert motion_patch.car_service_verdict(whitelist, "com.example.video") == "accepted"
    assert motion_patch.car_service_verdict("Can't find service: car_service", "com.x") == "no_car_service"
    assert motion_patch.car_service_verdict("", "com.x") == "no_car_service"
    assert motion_patch.car_service_verdict("какой-то другой дамп без списка", "com.x") == "unknown"


def test_verdict_line_wording():
    assert "разрешены в движении" in motion_patch.verdict_line("accepted", "com.x")
    assert "не приняла" in motion_patch.verdict_line("rejected", "com.x")
    assert "Android Automotive на магнитоле нет" in motion_patch.verdict_line("no_car_service", "com.x")
    assert "не удалось проверить" in motion_patch.verdict_line("unknown", "com.x")


def test_desktop_and_android_copies_match():
    desktop = (ROOT / "app/motion_patch.py").read_bytes()
    android = (ROOT / "android/app/src/main/python/motion_patch.py").read_bytes()
    assert desktop == android, "motion_patch.py на ПК и Android должны совпадать байт в байт"
