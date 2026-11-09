"""Android, запись флешки (UsbFlashWrite.kt; разбор 09.11.2026):
- поиск на флешке без учёта регистра — FAT его не различает, libaums в createFile проверяет без учёта, а search() — с учётом:
  «Item already exists!» на всех попытках (Tugella, №14257);
- сбой самого драйвера не из IOException (NullPointerException libaums, №9998) — тоже повтор, а не обрыв записи;
- Android-обновление программы ждёт конца операции с магнитолой (№13592)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KOTLIN = ROOT / "android/app/src/main/java/ru/magicsqd/mobile"


def _code(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_flash_lookup_ignores_case_everywhere():
    src = _code(KOTLIN / "usb/UsbFlashWrite.kt")
    assert "internal fun UsbFile.child(name: String): UsbFile? =\n    search(name) ?: listFiles().firstOrNull { it.name.equals(name, ignoreCase = true) }" in src
    assert ".search(" not in src.replace("search(name) ?: listFiles()", "")
    assert "val found = dir.child(name)" in src and "dir.child(fileName)?.let { it.delete() }" in src


def test_driver_errors_are_retried_not_thrown():
    src = _code(KOTLIN / "usb/UsbFlashWrite.kt")
    write = re.search(r"fun writeFileToUsb\(.*?\n}\n", src, flags=re.S).group(0)
    assert "if (e is InterruptedException) throw e" in write
    assert "e !is IOException && e !is IllegalArgumentException" not in write


def test_self_update_waits_for_operation():
    src = _code(KOTLIN / "WebBridge.kt")
    update = re.search(r"private fun startAppUpdate\(.*?\n    }\n", src, flags=re.S).group(0)
    assert update.index("while (operationBusy.get()) Thread.sleep(1000)") < update.index("AppSelfUpdate.install(context, apk)")
    assert 'pushAppUpdate("wait", "")' in update


def test_py_call_resyncs_incomplete_shared_code_once():
    """«Собрать подробный лог» отказывал «без подписи разработчика»: на телефоне был .py без .sig — синхронизация
    каталога прервалась (OnePlus, №13001/13003). Теперь — докачать скрипты и повторить один раз."""
    src = _code(KOTLIN / "usb/PyCtxBridge.kt")
    call = re.search(r"    fun call\(module: String.*?\n    }\n", src, flags=re.S).group(0)
    assert call.count("callOnce(module, function, args, modelDir, files)") == 2
    assert "!first.unavailable || !INCOMPLETE_CODE.containsMatchIn(first.error)" in call
    pattern = re.search(r'INCOMPLETE_CODE = Regex\("(.*?)"\)', src).group(1)
    signing = (ROOT / "android/app/src/main/python/code_signing.py").read_text(encoding="utf-8")
    for message in ("diagnostics.py без подписи разработчика — на телефоне не исполняется",
                    "подпись diagnostics.py не сошлась — на телефоне не исполняется",
                    "нет файла diagnostics.py — синхронизируйте каталог"):
        assert re.search(pattern, message), message
    assert "без подписи разработчика" in signing and "не сошлась" in signing and "синхронизируйте каталог" in signing
    bridge = _code(KOTLIN / "WebBridge.kt")
    assert 'resyncShared = { pyModule("mobile_bridge").callAttr("sync_cars", carsDir, BASE_URL) }' in bridge


def test_anr_note_includes_main_thread_stack():
    src = _code(KOTLIN / "ProcessExitNote.kt")
    assert "info.reason != ApplicationExitInfo.REASON_ANR" in src and "info.traceInputStream" in src
    fn = re.search(r"internal fun anrMainStack\(.*?\n    }\n", src, flags=re.S).group(0)
    assert 'it.startsWith("\\"main\\"")' in fn and "takeWhile { it.isNotBlank() }" in fn


def test_deepal_shell_login_stops_install_on_both_platforms():
    """Deepal S07: «Please log in to 'adb shell' first» на любую команду — стоп с понятной причиной, без перебора всех
    способов по каждому приложению (№10597, №13195, №14153)."""
    from app import install_context
    assert install_context.SHELL_LOGIN_RE.search("Failure: Please log in to 'adb shell' first")
    assert "Deepal" in install_context.SHELL_LOGIN_TEXT
    src = (ROOT / "app/install_context.py").read_text(encoding="utf-8")
    assert "if SHELL_LOGIN_RE.search(str(exc)):\n                    raise InstallCancelled(SHELL_LOGIN_TEXT) from exc" in src
    engine = _code(KOTLIN / "usb/InstallEngine.kt")
    rejection = re.search(r"private fun definitiveRejection\(.*?\n    }\n", engine, flags=re.S).group(0)
    assert "SHELL_LOGIN_RE.containsMatchIn(reason) ->" in rejection
    assert 'Regex("log in to .adb shell.", RegexOption.IGNORE_CASE)' in engine
