package ru.magicsqd.mobile.usb

import android.content.Context
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Держит ОДНО установленное ADB-соединение (CNXN+AUTH уже пройдены) живым
 * между несколькими этапами мастера установки — так же, как desktop-версия
 * держит один процесс adb.exe/adb-сервер на всю установку, а не
 * переподключается на каждую команду. Синглтон уровня процесса приложения:
 * реально нужно только одно соединение за раз (одна магнитола подключена
 * технику одновременно), см. InstallEngine.kt. Умеет два транспорта — USB
 * (обычный случай) и Wi-Fi/TCP (см. TcpAdbTransport — модели с `wifi: true`
 * в _wizard_spec.json, аналог desktop cars/_shared/wifi_adb.py:connect_wifi,
 * но без ADB-сервера — тот же самодельный клиент, что и для USB).
 */
object AdbSession {
    private enum class Mode { USB, WIFI }

    @Volatile private var transport: AdbTransport? = null
    @Volatile private var mode: Mode? = null
    @Volatile private var wifiHost: String? = null
    @Volatile private var wifiPort: Int = 0

    val isConnected: Boolean get() = transport != null

    fun disconnect() {
        try {
            transport?.close()
        } catch (_: Exception) {
        }
        transport = null
    }

    /**
     * Ищет среди подключённых по USB устройств первое с ADB-интерфейсом,
     * запрашивает разрешение и делает CNXN(+AUTH). БЛОКИРУЮЩИЙ вызов (AUTH
     * может ждать до 30с подтверждения на экране магнитолы) — только с
     * фонового потока.
     */
    fun connectBlocking(context: Context, log: (String) -> Unit): AdbHandshakeResult {
        disconnect()
        val usbManager = context.getSystemService(Context.USB_SERVICE) as UsbManager
        val target = usbManager.deviceList.values.firstOrNull { findAdbInterface(it) != null }
            ?: return AdbHandshakeResult.Failed(
                "Устройство с ADB-интерфейсом не найдено среди подключённых по USB — " +
                    "проверь, что на магнитоле включена отладка по USB и это OTG-подключение."
            )
        val targetIface = findAdbInterface(target)!!

        if (!requestUsbPermissionBlocking(context, target)) {
            return AdbHandshakeResult.Failed("Пользователь отклонил разрешение на доступ к USB-устройству")
        }

        val conn = usbManager.openDevice(target)
            ?: return AdbHandshakeResult.Failed("Не удалось открыть USB-соединение (openDevice вернул null)")
        if (!conn.claimInterface(targetIface.usbInterface, true)) {
            conn.close()
            return AdbHandshakeResult.Failed("Не удалось claimInterface — устройство занято другим процессом?")
        }

        val usbTransport = UsbAdbTransport(conn, targetIface)
        val result = performCnxnHandshake(usbTransport, context, log)
        if (result is AdbHandshakeResult.Connected) {
            transport = usbTransport
            mode = Mode.USB
        } else {
            usbTransport.close()
        }
        return result
    }

    /**
     * `adb connect host:port`-аналог (Wi-Fi ADB) — обычный TCP-сокет вместо
     * USB bulk-эндпоинтов, тот же CNXN(+AUTH) хендшейк поверх [TcpAdbTransport].
     * port обычно 5555, но у некоторых моделей свой (см. NewCarSpec.wifi_port
     * на desktop) — передаётся вызывающим кодом (WebBridge.kt), не хардкожен.
     */
    fun connectWifiBlocking(host: String, port: Int, context: Context, log: (String) -> Unit): AdbHandshakeResult {
        disconnect()
        val socket = try {
            // Явная привязка к Wi-Fi-сети — иначе при активном VPN на
            // телефоне (даже "по приложениям", даже если Magic SQD в него не
            // включена) сокет нередко следует системному default route,
            // который VPN подменяет на свой tun, и до магнитолы в локальной
            // сети просто не долетает (см. NetworkScan.bindToWifi — тот же
            // фикс для скана сети, реальный баг пользователя с NekoBox).
            Socket().apply { NetworkScan.bindToWifi(context, this); connect(InetSocketAddress(host, port), 5000) }
        } catch (e: Exception) {
            return AdbHandshakeResult.Failed("Не удалось подключиться по TCP к $host:$port: ${e.javaClass.simpleName}: ${e.message}")
        }
        val tcpTransport = TcpAdbTransport(socket)
        val result = performCnxnHandshake(tcpTransport, context, log)
        if (result is AdbHandshakeResult.Connected) {
            transport = tcpTransport
            mode = Mode.WIFI
            wifiHost = host
            wifiPort = port
        } else {
            tcpTransport.close()
        }
        return result
    }

    /**
     * Ждёт, пока устройство снова не станет доступным (после reboot оно на
     * время отваливается), затем переподключается — тем же транспортом
     * (USB/Wi-Fi), которым было установлено ТЕКУЩЕЕ соединение до
     * disconnect(). У нас нет постоянного ADB-сервера, как на desktop
     * (`adb wait-for-device`) — только периодический опрос.
     */
    fun waitForDeviceAndReconnect(context: Context, timeoutMs: Long, log: (String) -> Unit): AdbHandshakeResult {
        return when (mode) {
            Mode.WIFI -> waitForWifiAndReconnect(context, timeoutMs, log)
            else -> waitForUsbAndReconnect(context, timeoutMs, log)
        }
    }

    private fun waitForUsbAndReconnect(context: Context, timeoutMs: Long, log: (String) -> Unit): AdbHandshakeResult {
        val usbManager = context.getSystemService(Context.USB_SERVICE) as UsbManager
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            if (usbManager.deviceList.values.any { findAdbInterface(it) != null }) {
                return connectBlocking(context, log)
            }
            Thread.sleep(1000)
        }
        return AdbHandshakeResult.Failed("Устройство не переподключилось за ${timeoutMs / 1000}с")
    }

    private fun waitForWifiAndReconnect(context: Context, timeoutMs: Long, log: (String) -> Unit): AdbHandshakeResult {
        val host = wifiHost
        val port = wifiPort
        if (host == null) return AdbHandshakeResult.Failed("Нет сохранённого Wi-Fi адреса для переподключения")
        log("Жду возвращения устройства по Wi-Fi ($host:$port)...")
        val deadline = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < deadline) {
            Thread.sleep(2000)
            val result = connectWifiBlocking(host, port, context, log)
            if (result is AdbHandshakeResult.Connected) return result
        }
        return AdbHandshakeResult.Failed("Устройство не переподключилось по Wi-Fi за ${timeoutMs / 1000}с")
    }

    fun shell(command: String, log: (String) -> Unit, timeoutMs: Int = 5000): AdbShellResult =
        runAdbShellCommand(requireTransport(), command, log, timeoutMs)

    fun service(serviceName: String, log: (String) -> Unit, timeoutMs: Int = 5000): AdbShellResult =
        runAdbService(requireTransport(), serviceName, log, timeoutMs)

    fun push(bytes: ByteArray, remotePath: String, log: (String) -> Unit): AdbPushResult =
        syncPushBytes(requireTransport(), bytes, remotePath, log)

    fun installApk(bytes: ByteArray, log: (String) -> Unit): AdbInstallResult =
        installApkOverAdb(requireTransport(), bytes, log = log)

    private fun requireTransport(): AdbTransport =
        transport ?: error("ADB не подключён — сначала нужно установить соединение с устройством")

    private fun requestUsbPermissionBlocking(context: Context, device: UsbDevice): Boolean {
        val latch = CountDownLatch(1)
        var granted = false
        requestUsbPermission(context, device) { result ->
            granted = result
            latch.countDown()
        }
        latch.await(60, TimeUnit.SECONDS)
        return granted
    }
}
