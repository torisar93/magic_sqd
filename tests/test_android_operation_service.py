"""Android: служба «Идёт установка» на время операции с магнитолой (разбор логов 14.10: свёрнутая программа рвала
заливку по Wi-Fi в 29 из 43 случаев — Android замораживал процесс). Kotlin-тестов в проекте нет — проверяем исходники;
жизненный цикл службы (уведомление, WakeLock/WifiLock, остановка через 20 с) проверен на эмуляторе hu."""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KOTLIN = ROOT / "android/app/src/main/java/ru/magicsqd/mobile"
MANIFEST = ROOT / "android/app/src/main/AndroidManifest.xml"


def _code(path: Path) -> str:
    text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def _block(code: str, start: str) -> str:
    return re.split(r"\n    }(?:\n|$)", code[code.index(start):])[0]


def test_every_exclusive_operation_keeps_the_service():
    run = _block(_code(KOTLIN / "WebBridge.kt"), "private fun runExclusive(")
    begin, thread = run.index("OperationKeeper.begin("), run.index("Thread {")
    assert begin < thread, "служба включается до начала операции"
    finally_part = run[run.index("} finally {"):]
    assert "operationBusy.set(false)" in finally_part and "OperationKeeper.end()" in finally_part


def test_service_is_started_without_the_start_foreground_service_trap():
    # startForegroundService + остановка раньше startForeground роняет программу; обычный startService из видимой
    # программы — нет, а не включилась служба — строка в журнал.
    code = _code(KOTLIN / "OperationService.kt")
    assert "startForegroundService" not in code and "app.startService(" in code
    start = _block(code, "override fun onStartCommand(")
    assert "ServiceCompat.startForeground(" in start and "FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE" in start
    assert "catch (e: Exception)" in start and "stopSelf()" in start
    assert "START_NOT_STICKY" in start


def test_manifest_declares_the_service_and_its_permissions():
    manifest = MANIFEST.read_text(encoding="utf-8")
    for permission in ("FOREGROUND_SERVICE", "FOREGROUND_SERVICE_CONNECTED_DEVICE", "WAKE_LOCK",
                       "CHANGE_WIFI_MULTICAST_STATE"):  # последнее — условие типа connectedDevice на Android 14+
        assert f'android:name="android.permission.{permission}"' in manifest, permission
    service = re.search(r"<service\s[^>]*OperationService[^>]*/>", manifest, flags=re.S)
    assert service, "служба объявлена"
    tag = service.group(0)
    assert 'android:foregroundServiceType="connectedDevice"' in tag
    assert 'android:exported="false"' in tag and 'android:stopWithTask="true"' in tag
    assert (ROOT / "android/app/src/main/res/drawable/ic_stat_operation.xml").is_file()
