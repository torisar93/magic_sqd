"""Android: APK заливается на магнитолу с диска потоком, а не читается в память целиком.

Реальные вылеты: #449 (1.0.23, лог оборвался на «Устанавливаю YK_MonjaroMOD…»)
и #660 (1.0.31: java.lang.OutOfMemoryError: Failed to allocate a 173684792 byte
allocation … at kotlin.io.readBytes … InstallEngine.installApksWithProgress) —
InstallEngine делал signedFile.readBytes() при лимите памяти приложения 256 МБ,
а APK бывают по 500 МБ и больше (владелец, 2026-09-23). Теперь APK передаётся
как PushSource (usb/AdbInstall.kt) и читается кусками прямо во время sync-push.
Kotlin-тестов в проекте нет (Android SDK — только в CI), поэтому страхуемся
проверкой исходников: чтобы целиковое чтение APK не вернулось незаметно."""
from __future__ import annotations
import re
from pathlib import Path

USB = Path(__file__).resolve().parents[1] / "android/app/src/main/java/ru/magicsqd/mobile/usb"


def _code(name: str) -> str:
    # без комментариев — в них readBytes() упоминается как история бага
    text = (USB / name).read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def test_install_engine_never_reads_whole_files_except_small_helpers():
    reads = [line.strip() for line in _code("InstallEngine.kt").splitlines() if "readBytes()" in line]
    # единственное допустимое — маленькие хелперы установки (chery_localinstall.apk, dex_shell_helper.dex)
    assert reads and all("helper.readBytes()" in line for line in reads), reads


def test_apk_installers_take_a_streaming_source_not_a_byte_array():
    code = _code("AdbInstall.kt")
    for name in ("installApkOverAdb", "installApkHavalRevivedOverAdb", "installApkSpoofedOverAdb",
                 "installApkStreamOverAdb", "installApkViaLocalinstall", "installApkViaDexShell"):
        signature = re.search(rf"fun {name}\((.*?)\): AdbInstallResult", code, flags=re.S)
        assert signature, name
        assert "apk: PushSource" in signature.group(1), (name, signature.group(1))
    # сам sync-push читает источник кусками размером в DATA-пакет
    push = re.search(r"fun syncPush\(.*?\n}\n", code, flags=re.S).group(0)
    assert "source.open().use" in push and "readChunk(input, buffer)" in push
    assert "val buffer = ByteArray(MAX_CHUNK)" in push


def test_session_wrappers_and_engine_pass_the_file_source_through():
    session = _code("AdbSession.kt")
    assert "fun push(source: PushSource," in session
    assert not re.search(r"fun installApk\w*\(bytes: ByteArray", session)
    engine = _code("InstallEngine.kt")
    assert "PushSource.of(signedFile)" in engine
    assert "(PushSource, String?, (String) -> Unit) -> AdbInstallResult" in engine
