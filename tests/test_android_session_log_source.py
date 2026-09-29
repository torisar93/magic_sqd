"""Android: журнал сессии не обрывается при сворачивании программы и подробнее (Belgee S50, 28.09). Kotlin-тестов в
проекте нет — проверяем исходники; поведение JS — tests/js/session_log_continuity.test.js, скачивание —
tests/test_android_download_sink.py."""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KOTLIN = ROOT / "android/app/src/main/java/ru/magicsqd/mobile"
APP_JS = ROOT / "android/app/src/main/assets/js/app.js"


def _code(path: Path) -> str:
    text = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def _block(code: str, start: str) -> str:
    return re.split(r"\n    }(?:\n|$)", code[code.index(start):])[0]


def test_minimizing_no_longer_seals_the_session():
    activity = _code(KOTLIN / "MainActivity.kt")
    on_stop = _block(activity, "override fun onStop()")
    assert "__onAppHidden" in on_stop and "flush" not in on_stop.lower()
    assert "__onAppShown" in _block(activity, "override fun onStart()")
    assert "__flushInstallLogOnStop" not in APP_JS.read_text(encoding="utf-8")


def test_recovery_knows_the_session_ended_minimized():
    queue = _code(KOTLIN / "InstallLogQueue.kt")
    js = APP_JS.read_text(encoding="utf-8")
    hidden = re.search(r'HIDDEN_LINE = "([^"]+)"', queue).group(1)
    shown = re.search(r'SHOWN_LINE = "([^"]+)"', queue).group(1)
    assert f'log("{hidden} (' in js and f"log(`{shown} (" in js  # те же начала строк, что пишет app.js
    recovered = _block(queue, "internal fun recoveredText(")
    assert "indexOfLast { it.startsWith(HIDDEN_LINE) }" in recovered and "hidden > shown" in recovered
    assert "CLOSED_WHILE_HIDDEN_MARKER" in recovered and '"$STALE_MARKER\\n$logText"' in recovered
    assert "recoveredText(logText)" in _block(queue, "private fun recoverStaleCurrent(")


def test_flash_write_reports_downloads_live_and_sums_up():
    bridge = _code(KOTLIN / "WebBridge.kt")
    run = _block(bridge, "private fun usbRunStage(")
    assert "DownloadSink(::pushAdbLog)" in run and 'ApkOperationProgress("download", done, total)' in run
    assert "ensureApksDownloaded(files + selectedApks, sink = sink)" in run and "syncSharedFolder(sharedFolder, sink)" in run
    assert "Запись на флешку закончена:" in run and "Запись на флешку прервалась: записано $filesWritten" in run
    assert '"ensure_apks_downloaded", apkDir, carsDir, BASE_URL, pathsArr.toString(), progress, sink' in bridge
    assert '"sync_shared_folder_for", carsDir, BASE_URL, folderName, sink' in bridge
    sink = _code(KOTLIN / "DownloadSink.kt")
    assert "fun line(text: String)" in sink and "fun progress(done: Long, total: Long)" in sink  # как зовёт Python


def test_session_header_names_the_phone():
    bridge = _code(KOTLIN / "WebBridge.kt")
    version = bridge[bridge.index('"app_version" -> JSONObject()'):][:600]
    assert '.put("device",' in version and '.put("android",' in version and '.put("sdk",' in version
