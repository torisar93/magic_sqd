"""Android, разбор логов 2026-09-26 (№970–1244). Kotlin-тестов в проекте нет — проверяем исходники.

- Флешку вынимали и вставили снова (QR ADB и флаги Jolion: флешка ходит в магнитолу и обратно), а сессия
  держала старое подключение: 16 из 19 сбоев MAX_RECOVERY за 25–26.09 — на подключении, которое до этого
  уже работало (№1052, №1106, №1154, №1205). Теперь каждая операция с флешкой сначала подключается заново,
  если смонтированного устройства уже нет среди подключённых.
- VPN поверх Wi-Fi принимался за Wi-Fi магнитолы (скан по tun0, №1233; EPERM при привязке сокета, №880,
  №887), а в списке найденных устройств был сам телефон (telnet «to /X from /X», №1151, №1178, №1233).
- Незагрузившаяся картинка писалась в журнал как «ПРИЛОЖЕНИЕ ЗАВЕРШИЛОСЬ…» (№1148, №1172, №1179): ошибка JS —
  с тем же маркером, что на ПК, «вылет» — только настоящему падению процесса."""
from __future__ import annotations
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KOTLIN = ROOT / "android/app/src/main/java/ru/magicsqd/mobile"


def _code(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def _function(code: str, signature: str, indent: str = "    ") -> str:
    match = re.search(re.escape(signature) + r".*?\n" + indent + r"}(?:\n|$)", code, flags=re.S)
    assert match, signature
    return match.group(0)


def test_flash_operations_reconnect_after_replug():
    session = _code(KOTLIN / "usb/UsbFlashSession.kt")
    attached = _function(session, "fun isStillAttached(")
    assert "manager.deviceList.values.any { it.deviceName == mounted.usbDevice.deviceName }" in attached

    bridge = _code(KOTLIN / "WebBridge.kt")
    remount = _function(bridge, "private fun remountIfReplugged(")
    assert "UsbFlashSession.isStillAttached(context)" in remount and "connectFlashAndReport()" in remount
    for signature in ("private fun usbRunStage(", "private fun qrAdbWritePrepFlag(", "private fun qrAdbWriteFlag(",
                      "private fun qrAdbGetPassword(", "private fun usbFormat("):
        body = _function(bridge, signature)
        assert "remountIfReplugged()" in body, signature
        # переподключение — до первого обращения к флешке
        first_use = min(i for i in (body.find("requireFs()"), body.find("capacityBytes()"),
                                    body.find("writeUsbStage(")) if i != -1)
        assert body.index("remountIfReplugged()") < first_use, signature
    # «Подключить» и переподключение шлют в интерфейс один и тот же итог
    assert "private fun usbConnect() = runExclusive(::onBusy) { connectFlashAndReport() }" in bridge


def test_wifi_network_is_not_a_vpn():
    scan = _code(KOTLIN / "usb/NetworkScan.kt")
    wifi = _function(scan, "fun wifiNetwork(")
    assert "hasTransport(NetworkCapabilities.TRANSPORT_WIFI)" in wifi
    assert "!caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)" in wifi


def test_scan_never_offers_the_phone_itself():
    bridge = _code(KOTLIN / "WebBridge.kt")
    scan_hosts = _function(bridge, "private fun scanHosts(")
    assert scan_hosts.count("val own = NetworkScan.ownAddresses()") == 2  # telnet (IPv6) и Wi-Fi ADB (IPv4)
    assert scan_hosts.count(".filter { NetworkScan.withoutZone(it) !in own }") == 2
    assert scan_hosts.count("takeIf { NetworkScan.withoutZone(it) !in own }") == 2  # и «рекомендованный» тоже
    scan = _code(KOTLIN / "usb/NetworkScan.kt")
    assert "host.trim('[', ']').substringBefore('%').lowercase()" in scan


def test_js_error_is_not_logged_as_app_crash():
    bridge = _code(KOTLIN / "WebBridge.kt")
    client_error = _function(bridge, "private fun clientLogError(")
    assert "НЕОБРАБОТАННАЯ ОШИБКА JS" in client_error and "CRASH_MARKER" not in client_error
    main_activity = _code(KOTLIN / "MainActivity.kt")
    assert "InstallLogQueue.CRASH_MARKER" in main_activity  # настоящий вылет процесса — по-прежнему «вылет»
