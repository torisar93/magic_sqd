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

    /** ro.product.model из баннера последнего успешного подключения — ключ памяти «какой способ установки
     * сработал на этой магнитоле» (см. InstallEngine.rememberedMethod). null, если магнитола его не назвала. */
    @Volatile var deviceModel: String? = null
        private set

    private fun parseDeviceModel(banner: String): String? =
        Regex("ro\\.product\\.model=([^;]+)").find(banner)?.groupValues?.get(1)?.trim()?.takeIf { it.isNotEmpty() }

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
        // Ищем устройство и его ADB-интерфейс ОДНИМ проходом (не два отдельных
        // — раньше второй повторный findAdbInterface(target) был обёрнут в "!!"
        // и мог упасть NPE, если устройство отвалилось по USB между первым и
        // вторым поиском, например от дёрнувшегося кабеля/OTG-переходника).
        val (target, targetIface) = usbManager.deviceList.values
            .firstNotNullOfOrNull { device -> findAdbInterface(device)?.let { device to it } }
            ?: return AdbHandshakeResult.Failed(
                "Устройство с ADB-интерфейсом не найдено среди подключённых по USB — " +
                    "проверь, что на магнитоле включена отладка по USB и это OTG-подключение.",
                noDevice = true
            )

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
            deviceModel = parseDeviceModel(result.bannerFromDevice)
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
            deviceModel = parseDeviceModel(result.bannerFromDevice)
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
        var lastFailure: String? = null
        while (System.currentTimeMillis() < deadline) {
            if (usbManager.deviceList.values.any { findAdbInterface(it) != null }) {
                // Устройство уже видно, но рукопожатие могло не пройти (магнитола ещё
                // поднимается после обрыва) — пробуем ещё до конца ожидания, а не
                // сдаёмся после первой неудачи.
                val result = connectBlocking(context, log)
                if (result is AdbHandshakeResult.Connected) return result
                lastFailure = (result as AdbHandshakeResult.Failed).reason
                if (lastFailure.startsWith("Пользователь отклонил")) return result
            }
            Thread.sleep(1000)
        }
        return AdbHandshakeResult.Failed(
            "Устройство не переподключилось за ${timeoutMs / 1000}с" + (lastFailure?.let { ": $it" } ?: "")
        )
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

    // stagedPath != null — APK уже залит движком по этому пути один раз для всех способов (см.
    // InstallEngine.installApksWithProgress / AdbInstall.stageApkIfNeeded).
    fun installApk(bytes: ByteArray, log: (String) -> Unit, stagedPath: String? = null): AdbInstallResult =
        if (stagedPath == null) installApkOverAdb(requireTransport(), bytes, log = log)
        else installApkOverAdb(requireTransport(), bytes, remotePath = stagedPath, log = log, prePushed = true)

    fun installApkPmStream(bytes: ByteArray, log: (String) -> Unit, stagedPath: String? = null): AdbInstallResult =
        if (stagedPath == null) installApkStreamOverAdb(requireTransport(), bytes, log = log)
        else installApkStreamOverAdb(requireTransport(), bytes, remotePath = stagedPath, log = log, prePushed = true)

    fun installApkSpoofed(bytes: ByteArray, log: (String) -> Unit, stagedPath: String? = null): AdbInstallResult =
        if (stagedPath == null) installApkSpoofedOverAdb(requireTransport(), bytes, log = log)
        else installApkSpoofedOverAdb(requireTransport(), bytes, remotePath = stagedPath, log = log, prePushed = true)

    fun installApkHavalRevived(bytes: ByteArray, log: (String) -> Unit, stagedPath: String? = null): AdbInstallResult =
        if (stagedPath == null) installApkHavalRevivedOverAdb(requireTransport(), bytes, log = log)
        else installApkHavalRevivedOverAdb(requireTransport(), bytes, remotePath = stagedPath, log = log, prePushed = true)

    fun installApkLocalinstall(bytes: ByteArray, helperBytes: ByteArray, log: (String) -> Unit, stagedPath: String? = null): AdbInstallResult =
        if (stagedPath == null) installApkViaLocalinstall(requireTransport(), bytes, helperBytes, log = log)
        else installApkViaLocalinstall(requireTransport(), bytes, helperBytes, log = log, remoteApk = stagedPath, prePushed = true)

    /** stagedPath, если задан, обязан быть "/data/local/tmp/<apkName>" — dex-хелпер работает именно с этим путём. */
    fun installApkDexShell(bytes: ByteArray, apkName: String, helperBytes: ByteArray, log: (String) -> Unit, stagedPath: String? = null): AdbInstallResult =
        installApkViaDexShell(requireTransport(), bytes, apkName, helperBytes, log = log, prePushed = stagedPath != null)

    /** Desay x9h (Haval Jolion 2026 / TR01025 и родня): JDWP-патч mInstallWhiteList + pm install (см.
     * AdbJdwp.kt, desktop app/install_context.py:install_apk_jdwp_whitelist). packageName нужен ДО установки
     * — берётся из самого APK (getPackageArchiveInfo). stagedPath — уже залитый движком файл. */
    fun installApkJdwpWhitelist(bytes: ByteArray, packageName: String, log: (String) -> Unit,
                                stagedPath: String? = null, apkName: String = "install.apk"): AdbInstallResult {
        val transport = requireTransport()
        if (packageName.isBlank()) return AdbInstallResult.Failed("не удалось прочитать имя пакета — нужно для JDWP-патча")
        // 1) PID system_server
        val pidResult = runAdbShellCommand(transport, "pidof system_server", log)
        val pid = when (pidResult) {
            is AdbShellResult.Output -> pidResult.text.trim().split(Regex("\\s+")).firstOrNull() ?: ""
            else -> ""
        }
        if (!pid.matches(Regex("\\d+"))) return AdbInstallResult.Failed("не удалось получить PID system_server")
        // 2) JDWP-поток к процессу и патч белого списка
        try {
            val (localId, remoteId) = openAdbStream(transport, "jdwp:$pid", log)
            val jdwpStream = AdbByteStream(transport, localId, remoteId, log)
            try {
                log("JDWP: подключаюсь к system_server(pid $pid), патчу белый список для $packageName...")
                JdwpClient(jdwpStream).patchWhitelist(listOf(packageName), log)
            } finally {
                jdwpStream.close()
            }
        } catch (e: JdwpException) {
            return AdbInstallResult.Failed("JDWP-патч белого списка не удался: ${e.message}")
        }
        // 3) push (если не залит заранее) + pm install -r -t
        val remotePath = stagedPath ?: "/data/local/tmp/$apkName"
        if (stagedPath == null) {
            AdbInstallProgress.beginTransfer(bytes.size.toLong())
            when (val r = syncPushBytes(transport, bytes, remotePath, log)) {
                is AdbPushResult.Failed -> return AdbInstallResult.Failed(r.reason)
                AdbPushResult.Success -> {}
            }
        }
        AdbInstallProgress.installing()
        val installResult = runAdbShellCommand(transport, "pm install -r -t $remotePath", log, timeoutMs = 120000)
        if (stagedPath == null) runAdbShellCommand(transport, "rm -f $remotePath", log)
        val pmOutput = when (installResult) {
            is AdbShellResult.Output -> installResult.text
            is AdbShellResult.Rejected -> return AdbInstallResult.Failed("pm install отклонён: ${installResult.reason}")
            is AdbShellResult.Failed -> return AdbInstallResult.Failed("pm install ошибка: ${installResult.reason}")
        }
        return if (pmOutput.contains("Success", ignoreCase = true)) AdbInstallResult.Success(pmOutput.trim())
        else AdbInstallResult.Failed("pm install не вернул Success: ${pmOutput.trim()}")
    }

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
