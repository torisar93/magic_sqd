"""Android, находки разбора логов 30.09 (№1731–1788). Kotlin-тестов в проекте нет — сверяем исходники; сама логика
translateError и ProcessExitNote прогнана временным JVM-тестом (junit во времянке, 9/9).
- консоль: ошибку ищем в первых строках и не прячем вывод (№1773: `cat` куска logcat → «файл не найден»);
- флешка перестала отвечать → следующая операция подключается заново, без сырого «IOException: …» (№1779);
- «Как Android закрыл программу»: кто остановил принудительно (№1773, «Безопасность» Xiaomi) и окно без закрытия
  процесса (№1775, «не сообщил»)."""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KOTLIN = ROOT / "android/app/src/main/java/ru/magicsqd/mobile"


def _code(path: Path) -> str:
    text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def _block(code: str, start: str) -> str:
    return re.split(r"\n(?:    )?}(?:\n|$)", code[code.index(start):])[0]


def test_console_translates_only_the_head_and_keeps_the_output():
    fmt = _code(KOTLIN / "usb/AdbConsoleFormat.kt")
    translate = _block(fmt, "fun translateError(")
    assert ".take(ERROR_HEAD_LINES)" in translate and "lowered = head.lowercase()" in translate
    assert "FAILURE_REGEX.find(head)" in translate
    bridge = _code(KOTLIN / "WebBridge.kt")
    assert bridge.count('translateError(out)?.let { "$it\\n$out" }') == 1  # ИИ-чат
    assert bridge.count('translateError(text)?.let { "$it\\n$text" }') == 1  # консоль лога


def test_broken_flash_is_reconnected_before_the_next_operation():
    session = _code(KOTLIN / "usb/UsbFlashSession.kt")
    assert 'message?.contains("MAX_RECOVERY_ATTEMPTS") == true) broken = true' in _block(session, "fun noteFailure(")
    disconnect = _block(session, "fun disconnect()")
    assert "device?.close()" in disconnect and "broken = false" in disconnect
    remount = _block(_code(KOTLIN / "WebBridge.kt"), "private fun remountIfReplugged()")
    assert "UsbFlashSession.broken ->" in remount and "connectFlashAndReport()" in remount
    password = _block(_code(KOTLIN / "WebBridge.kt"), "private fun qrAdbGetPassword()")
    assert password.count("UsbFlashSession.noteFailure(e.message)") == 2


def test_flash_write_failures_get_retries_and_a_plain_message():
    write = _code(KOTLIN / "usb/UsbFlashWrite.kt")
    one_file = write[write.index("fun writeFileToUsb("):write.index("fun writeUsbStage(")]
    # папки создаются уже внутри попытки — сбой на них тоже повторяется и получает подсказку
    assert one_file.index("for (attempt in 1..WRITE_RETRY_ATTEMPTS)") < one_file.index("var dir: UsbFile = fs.rootDirectory")
    assert "errors.forEach { UsbFlashSession.noteFailure(it.message) }" in one_file
    stage = write[write.index("fun writeUsbStage("):]
    assert 'usbWriteFailureMessage("файлы", listOf(e))' in stage and "UsbFlashSession.noteFailure(e.message)" in stage


def test_exit_note_names_who_stopped_the_program():
    note = _code(KOTLIN / "ProcessExitNote.kt")
    assert '"com.miui.securitycenter" to "принудительно остановила «Безопасность» Xiaomi' in note
    assert 'Regex("from process:([\\\\w.]+)")' in note
    describe = _block(note, "fun describe(")
    assert "Process.getStartElapsedRealtime()" in describe and "startedAt + 2_000 < lastLineAt" in describe
    assert "не сообщил (его последняя запись" in describe


def test_flash_write_checks_downloads_before_writing():
    # №1818 (Coolray, без интернета): запись шла до первого недостающего файла — «записано 6 из 7», потом «Файл не скачан»
    stage = _code(KOTLIN / "usb/UsbFlashWrite.kt")
    stage = stage[stage.index("fun writeUsbStage("):]
    check = "(files + selectedApkPaths).firstOrNull { !File(it).exists() }?.let { return StageRunResult.Failed(\"Файл не скачан: $it\") }"
    assert check in stage and stage.index(check) < stage.index("removeOtherTriggerFlags(")
    sink = _code(KOTLIN / "DownloadSink.kt")
    assert 'if (text.startsWith("Не удалось скачать")) failed++' in sink
    assert 'if (failed > 0) log("Скачано не всё: не скачались ${files(failed)}' in sink


def test_exit_note_reads_android_descriptions():
    # №1799/№1810 «[REMOVE TASK]», №1800 «SwipeUpClean» (код OTHER), №1813 «[KILL BACKGROUND]»
    reason = _block(_code(KOTLIN / "ProcessExitNote.kt"), "internal fun reasonText(")
    assert '"remove task" in text || "swipeupclean" in text) return "смахнули из недавних приложений"' in reason
    assert '"kill background" in text) return "закрыл в фоне при очистке памяти"' in reason
