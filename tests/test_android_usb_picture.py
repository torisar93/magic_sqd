"""Android, «Подключить» по проводу (разбор логов 04.11.2026, Haval Jolion 2026: с ПК подключаются 40 из 41 техника, с
Android — 18 из 26, у многих десятки «Устройство с ADB-интерфейсом не найдено» подряд). Теперь телефон ждёт магнитолу
до 20 с, а если её так и нет — пишет, что видит по USB, и окно советует по причине. Kotlin-тестов в проекте нет —
проверяем исходники."""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KOTLIN = ROOT / "android/app/src/main/java/ru/magicsqd/mobile"
APP_JS = ROOT / "android/app/src/main/assets/js/app.js"


def _code(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def test_connect_waits_for_head_unit_only_on_user_press():
    session = _code(KOTLIN / "usb/AdbSession.kt")
    connect = re.search(r"fun connectBlocking\(.*?\n    }\n", session, flags=re.S).group(0)
    assert "waitForDeviceMs: Long = 0" in connect and "Thread.sleep(1000)" in connect
    assert connect.index("UsbPicture.describe(context, usbManager)") < connect.index("noDevice = true")
    assert "usbCause = picture.cause" in connect
    bridge = _code(KOTLIN / "WebBridge.kt")
    assert "AdbSession.connectBlocking(context, ::pushAdbLog, waitForDeviceMs = ADB_USB_WAIT_MS)" in bridge
    assert "ADB_USB_WAIT_MS = 20_000L" in bridge
    assert '.put("usb_cause", result.usbCause ?: JSONObject.NULL)' in bridge
    # переподключение посреди установки ждёт само и в описание USB не ходит
    assert "connectBlocking(context, log)" in session


def test_usb_picture_tells_three_cases_apart():
    picture = _code(KOTLIN / "usb/UsbPicture.kt")
    describe = re.search(r"fun describe\(.*?\n    }\n", picture, flags=re.S).group(0)
    # 1) устройства есть, ADB нет → no_adb; 2) пусто, но телефон сам устройство/заряжается → phone_is_device; 3) nothing
    assert describe.index("CAUSE_NO_ADB") < describe.index("CAUSE_PHONE_IS_DEVICE") < describe.index("CAUSE_NOTHING")
    assert '"android.hardware.usb.action.USB_STATE"' in picture and "BatteryManager.EXTRA_PLUGGED" in picture
    assert '"%02x/%02x/%02x".format(f.interfaceClass, f.interfaceSubclass, f.interfaceProtocol)' in picture
    # системное липкое сообщение на Android 13+ — с флагом, иначе registerReceiver бросает на targetSdk 34+
    assert "Context.RECEIVER_NOT_EXPORTED" in picture and "runCatching" in picture


def test_window_follows_usb_cause():
    js = APP_JS.read_text(encoding="utf-8")
    assert 'if (wired && r.usb_cause === "no_adb") window.StageRun.showUserError({ id: "usb_no_adb" });' in js
    assert "else if (wired) showOtgHintModal(r.usb_cause);" in js
    modal = re.search(r"function showOtgHintModal\(cause\) \{.*?\n  }\n", js, flags=re.S).group(0)
    assert 'const role = cause === "phone_is_device";' in modal and "Телефон стал устройством" in modal


def test_connect_button_waits_instead_of_busy():
    # 1.1.21 в поле (разбор 06.11.2026): 41 из 49 «Уже выполняется другая операция» — сразу после «жду до 20 с»:
    # техник жал «Подключить» снова. Пока телефон ждёт магнитолу, кнопка выключена и повтор не уходит в Kotlin.
    js = APP_JS.read_text(encoding="utf-8")
    connect = re.search(r"function onAdbConnect\(\) \{.*?\n  }\n", js, flags=re.S).group(0)
    assert connect.index("if (adbUsbConnecting) return;") < connect.index('Bridge.call("adb_connect", {});')
    assert "setAdbUsbConnecting(true);" in connect and "жду магнитолу по USB" in connect
    result = re.search(r"function onAdbConnectResult\(event\) \{.*?\n  }\n", js, flags=re.S).group(0)
    assert result.index("setAdbUsbConnecting(false);") < result.index("if (r.connected)")
    helper = re.search(r"function setAdbUsbConnecting\(on\) \{.*?\n  }\n", js, flags=re.S).group(0)
    assert "setTimeout(() => setAdbUsbConnecting(false), 35000)" in helper  # ответа нет (Kotlin занят) — не залипнуть
    assert "adbConnectBtn.disabled = labInstallBusy || adbUsbConnecting;" in js
