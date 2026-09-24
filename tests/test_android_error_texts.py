"""Android: понятные тексты ошибок вместо сырых исключений (разбор логов 2026-09-23).

- #577/#578: запись svlog.flag не удалась после всех попыток — техник видел голый
  английский текст libaums («newLimit > capacity: (1037 > 1024)», «MAX_RECOVERY_ATTEMPTS
  Exceeded … please reattach device»). Теперь writeFileToUsb бросает UsbWriteFailedException
  с подсказкой, что делать (отформатировать / переподключить флешку), исходный текст — в скобках.
- #584: остановка очереди приходила из Python (Chaquopy) как «java.lang.IllegalStateException:
  Очередь остановлена пользователем».
Kotlin-тестов в проекте нет (Android SDK — только в CI), поэтому проверяем исходники."""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KOTLIN = ROOT / "android/app/src/main/java/ru/magicsqd/mobile"


def _code(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def _function(code: str, signature: str, indent: str = "") -> str:
    match = re.search(re.escape(signature) + r".*?\n" + indent + r"}(?:\n|$)", code, flags=re.S)
    assert match, signature
    return match.group(0)


def test_usb_write_failure_explains_what_to_do():
    code = _code(KOTLIN / "usb/UsbFlashWrite.kt")
    message = _function(code, "internal fun usbWriteFailureMessage(")
    # баг ClusterChain libaums → форматирование; обрыв шины → переподключить, затем форматировать
    assert 'LIBAUMS_CLUSTER_BUG = "newLimit > capacity"' in code
    assert 'LIBAUMS_RECOVERY_FAILED = "MAX_RECOVERY_ATTEMPTS"' in code
    assert "LIBAUMS_CLUSTER_BUG in it" in message and "LIBAUMS_RECOVERY_FAILED in it" in message
    assert "$FORMAT_HINT" in message and "($last)" in message  # исходная ошибка остаётся для разбора логов

    write = _function(code, "fun writeFileToUsb(")
    assert "throw UsbWriteFailedException(usbWriteFailureMessage(fileName, errors), errors.last())" in write
    assert "lastError" not in write

    stage = _function(code, "fun writeUsbStage(")
    # понятный текст — без приставки с именем класса исключения
    assert stage.index("catch (e: UsbWriteFailedException)") < stage.rindex("catch (e: Exception)")
    assert "StageRunResult.Failed(e.message.orEmpty())" in stage


def test_format_hint_points_to_a_real_button():
    code = (KOTLIN / "usb/UsbFlashWrite.kt").read_text(encoding="utf-8")
    hint = re.search(r'FORMAT_HINT = "(.*?)"\n', code).group(1)
    app = (ROOT / "android/app/src/main/assets/js/app.js").read_text(encoding="utf-8")
    for label in re.findall(r"«(.*?)»", hint):
        assert f"'{label}'" in app or f'"{label}"' in app, label


def test_download_cancel_has_no_java_class_prefix():
    code = _code(KOTLIN / "WebBridge.kt")
    ensure = _function(code, "private fun ensureApksDownloaded(", indent="    ")
    assert "if (progress.isCancelled()) throw IllegalStateException(ApkDownloadProgress.CANCELLED_MESSAGE)" in ensure
    assert 'const val CANCELLED_MESSAGE = "Очередь остановлена пользователем"' in code


def test_grant_permissions_does_not_claim_success_without_link():
    """Разбор логов 2026-09-25 (#721, #910, #966): связь с магнитолой пропала — ни одна команда выдачи не ушла,
    а в журнале было «Разрешения выданы.» (в #966 — сразу после переустановки Podpratel Pro)."""
    code = _code(KOTLIN / "usb/AdbPermissions.kt")
    grant = _function(code, "fun grantAllPermissions(", indent="    ")
    check = grant.index("if (!AdbSession.isConnected)")
    assert grant.index('shellText("dumpsys package $pkg"') < check < grant.index("pm grant $pkg $perm")
    assert grant.count("log(linkLostMessage(pkg))") == 2 and grant.count("lastGrantAt.remove(pkg)") == 2
    assert grant.index("if (AdbSession.isConnected) {") < grant.index('log("Разрешения выданы.")')
    assert re.search(r"private fun linkLostMessage\(pkg: String\) =\s+\"Не удалось выдать разрешения \$pkg: связь с магнитолой потеряна", code)
