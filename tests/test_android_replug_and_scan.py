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


def test_link_loss_in_actions_does_not_crash_the_app():
    # Лог #1266 (1.0.42): связь умерла на «Выдать разрешения» — AdbLinkLostException из ответа на открытие
    # потока вылетел из потока операции, и программа упала («ПРИЛОЖЕНИЕ ЗАВЕРШИЛОСЬ…»).
    permissions = _code(KOTLIN / "usb/AdbPermissions.kt")
    assert permissions.count("AdbSession.shell(") == 1  # единственный — внутри safeShell
    safe = _function(permissions, "private fun safeShell(")
    assert "catch (e: AdbLinkLostException)" in safe and "AdbSession.markLinkLost()" in safe
    assert "if (!AdbSession.isConnected) return AdbShellResult.Failed" in safe  # мёртвая связь — без таймаутов

    session = _code(KOTLIN / "usb/AdbSession.kt")
    assert "fun markLinkLost()" in session and "fun markLost()" in session

    bridge = _code(KOTLIN / "WebBridge.kt")
    run = _function(bridge, "private fun runExclusive(")
    assert run.index("catch (e: AdbLinkLostException)") < run.index("catch (e: Exception)") < run.index("finally")


def test_last_head_unit_address_from_other_network_is_explained():
    # Адрес прошлой магнитолы (8 логов, №1226, №1339): подставляется только из сети телефона (tests/js/
    # wifi_last_host.test.js), а неудачное подключение к адресу из другой сети объясняет, в чём дело.
    scan = _code(KOTLIN / "usb/NetworkScan.kt")
    subnet = _function(scan, "fun inWifiSubnet(")
    assert "getLocalIPv4Subnet(context) ?: return null" in subnet and "(own and mask) == (target and mask)" in subnet
    session = _code(KOTLIN / "usb/AdbSession.kt")
    connect = _function(session, "fun connectWifiBlocking(")
    assert "NetworkScan.inWifiSubnet(context, host) == false" in connect and "адрес не из сети телефона" in connect
    bridge = _code(KOTLIN / "WebBridge.kt")
    assert '"wifi_host_in_subnet" -> JSONObject()' in bridge


def test_same_apk_already_on_head_unit_is_not_reinstalled():
    # Владелец, 2026-09-27: после «Файл не скачан»/обрыва уже поставленное ставилось заново (№1347). ПК — tests/
    # test_same_apk_skip.py; здесь Android: проверка до заливки файла, сравнение SHA-256 с base.apk на магнитоле.
    engine = _code(KOTLIN / "usb/InstallEngine.kt")
    same = _function(engine, "private fun sameApkInstalled(")
    assert 'AdbSession.shell("pm path $pkg", log)' in same and "sha256sum ${paths[0]}" in same
    assert "paths.size != 1" in same and "remote == sha256Hex(apk)" in same and "catch (_: Exception)" in same
    loop = engine[engine.index("for ((index, path) in apkPaths.withIndex())"):]
    assert loop.index("if (sameApkInstalled(currentPackageName, signedFile, log))") < loop.index("if (confirmedMethod != null)")
    assert "на магнитоле уже стоит этот же файл — установку пропускаю" in loop


def test_launch_app_reports_what_really_happened():
    # Geely Preface (владелец, 2026-09-27): «не срабатывает запуск приложений», а в журнале — «Готово.» по десять раз
    # (лог #1348). Поведение — как у ПК (tests/test_launch_app_result.py, ответы с эмулятора).
    permissions = _code(KOTLIN / "usb/AdbPermissions.kt")
    launch = _function(permissions, "fun launchMainActivity(")
    assert launch.index('monkey.contains("No activities found")') < launch.index('!monkey.contains("Events injected: 1")')
    assert "cmd package resolve-activity --brief" in launch and 'shellText("am start -n $component", log)' in launch
    assert "Thread.sleep(1500)" in launch and "onScreen(pkg, log)" in launch
    screen = _function(permissions, "private fun onScreen(")
    assert "dumpsys activity activities | grep -E 'Display #|ResumedActivity'" in screen
    assert 'line.contains("topResumedActivity")' in screen and 'component.startsWith("$pkg/")' in screen
