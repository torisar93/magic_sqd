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

    @Volatile private var transport: LinkWatch? = null
    @Volatile private var mode: Mode? = null
    @Volatile private var wifiHost: String? = null
    @Volatile private var wifiPort: Int = 0

    /** Связь есть и не умерла: запись в магнитолу, не прошедшая хоть раз (кабель вынут, магнитола ушла в сон,
     * Wi-Fi пропал), значит соединение мёртвое — дальше каждая команда падала бы так же («Не удалось
     * отправить OPEN», лог #788: 7 способов установки подряд, 4 запуска этапа). */
    val isConnected: Boolean get() = transport?.let { !it.lost } ?: false

    /** Соединение было, но запись в него не прошла (см. isConnected). Сбрасывается новым подключением. */
    val linkLost: Boolean get() = transport?.lost == true

    /** Команда упала с AdbLinkLostException (магнитола не ответила на открытие потока) — связь мёртвая, как и при
     * неудачной записи: дальше isConnected=false, следующие команды не ждут каждая свой таймаут. */
    fun markLinkLost() {
        transport?.markLost()
    }

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
            transport = LinkWatch(usbTransport)
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
            // Адрес не из сети телефона — чаще всего магнитола из прошлой машины (см. NetworkScan.inWifiSubnet).
            val otherNetwork = if (NetworkScan.inWifiSubnet(context, host) == false) {
                " — адрес не из сети телефона: похоже, это магнитола из прошлой машины. Выберите магнитолу из найденных устройств."
            } else ""
            return AdbHandshakeResult.Failed(
                "Не удалось подключиться по TCP к $host:$port: ${e.javaClass.simpleName}: ${e.message}$otherNetwork")
        }
        val tcpTransport = TcpAdbTransport(socket)
        val result = performCnxnHandshake(tcpTransport, context, log)
        if (result is AdbHandshakeResult.Connected) {
            deviceModel = parseDeviceModel(result.bannerFromDevice)
            transport = LinkWatch(tcpTransport)
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

    // Файл — с диска потоком, не целиком в память (см. PushSource: APK бывают по 500 МБ и больше).
    fun push(source: PushSource, remotePath: String, log: (String) -> Unit): AdbPushResult =
        syncPush(requireTransport(), source, remotePath, log)

    // Снять файл с устройства (sync RECV) — нужно кнопке «работа в движении» (MotionOptimize.kt).
    fun pull(remotePath: String, dest: java.io.File, log: (String) -> Unit): AdbPullResult =
        syncPull(requireTransport(), remotePath, dest, log)

    // stagedPath != null — APK уже залит движком по этому пути один раз для всех способов (см.
    // InstallEngine.installApksWithProgress / AdbInstall.stageApkIfNeeded).
    fun installApk(apk: PushSource, log: (String) -> Unit, stagedPath: String? = null): AdbInstallResult =
        if (stagedPath == null) installApkOverAdb(requireTransport(), apk, log = log)
        else installApkOverAdb(requireTransport(), apk, remotePath = stagedPath, log = log, prePushed = true)

    fun installApkPmStream(apk: PushSource, log: (String) -> Unit, stagedPath: String? = null): AdbInstallResult =
        if (stagedPath == null) installApkStreamOverAdb(requireTransport(), apk, log = log)
        else installApkStreamOverAdb(requireTransport(), apk, remotePath = stagedPath, log = log, prePushed = true)

    fun installApkSpoofed(apk: PushSource, log: (String) -> Unit, stagedPath: String? = null): AdbInstallResult =
        if (stagedPath == null) installApkSpoofedOverAdb(requireTransport(), apk, log = log)
        else installApkSpoofedOverAdb(requireTransport(), apk, remotePath = stagedPath, log = log, prePushed = true)

    fun installApkHavalRevived(apk: PushSource, log: (String) -> Unit, stagedPath: String? = null): AdbInstallResult =
        if (stagedPath == null) installApkHavalRevivedOverAdb(requireTransport(), apk, log = log)
        else installApkHavalRevivedOverAdb(requireTransport(), apk, remotePath = stagedPath, log = log, prePushed = true)

    fun installApkLocalinstall(apk: PushSource, helperBytes: ByteArray, log: (String) -> Unit, stagedPath: String? = null): AdbInstallResult =
        if (stagedPath == null) installApkViaLocalinstall(requireTransport(), apk, helperBytes, log = log)
        else installApkViaLocalinstall(requireTransport(), apk, helperBytes, log = log, remoteApk = stagedPath, prePushed = true)

    /** stagedPath, если задан, обязан быть "/data/local/tmp/<apkName>" — dex-хелпер работает именно с этим путём. */
    fun installApkDexShell(apk: PushSource, apkName: String, helperBytes: ByteArray, log: (String) -> Unit, stagedPath: String? = null): AdbInstallResult =
        installApkViaDexShell(requireTransport(), apk, apkName, helperBytes, log = log, prePushed = stagedPath != null)

    /** Desay x9h (Haval Jolion 2026 / TR01025 и родня): JDWP-патч mInstallWhiteList + pm install (см.
     * AdbJdwp.kt, desktop app/install_context.py:install_apk_jdwp_whitelist). packageName нужен ДО установки
     * — берётся из самого APK (getPackageArchiveInfo). stagedPath — уже залитый движком файл. */
    fun installApkJdwpWhitelist(apk: PushSource, packageName: String, log: (String) -> Unit,
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
            AdbInstallProgress.beginTransfer(apk.size)
            when (val r = syncPush(transport, apk, remotePath, log)) {
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

    private val REBOOT_NEEDED = Regex("reboot your device|now reboot", RegexOption.IGNORE_CASE)

    /** Текст ответа сервиса adbd (root:, disable-verity:, remount:) одной строкой. */
    private fun serviceText(name: String, log: (String) -> Unit, timeoutMs: Int): String = try {
        when (val r = service(name, log, timeoutMs)) {
            is AdbShellResult.Output -> r.text.split(Regex("\\s+")).joinToString(" ").trim()
            is AdbShellResult.Rejected -> "сервис отклонён: ${r.reason}"
            is AdbShellResult.Failed -> "ошибка: ${r.reason}"
        }
    } catch (e: AdbLinkLostException) {
        markLinkLost()
        "ошибка: ${e.message}"
    }

    /** Способ «в системную папку» (BAIC U5 Plus): adb root → adb disable-verity → adb remount. null — раздел открыт
     * на запись, иначе причина. Если disable-verity только что выключил проверку раздела, она перестанет действовать
     * лишь после перезагрузки (remount ответит «succeeded», но записать будет нельзя). Перезагрузить сама программа
     * не может: ADB на BAIC U5 Plus перезагрузку не переживает — просим техника. Как ПК:
     * install_context._open_system_partition. */
    fun openSystemPartition(context: Context, log: (String) -> Unit): String? {
        log("Открываю системный раздел на запись: adb root → adb disable-verity → adb remount.")
        rootAndReconnect(context, log)?.let { return it }
        val verity = serviceText("disable-verity:", log, 30_000)
        log("  adb disable-verity: ${verity.ifBlank { "(пусто)" }}")
        if (REBOOT_NEEDED.containsMatchIn(verity)) {
            return "Проверка системного раздела отключена, но начнёт действовать только после перезагрузки. " +
                "Перезагрузите магнитолу, снова включите ADB в инженерном меню и запустите этап ещё раз."
        }
        return remountSystem(log)
    }

    /** adb remount (нужен root) — null, если системный раздел открыт на запись, иначе причина. */
    fun remountSystem(log: (String) -> Unit): String? {
        val remount = serviceText("remount:", log, 60_000)
        log("  adb remount: ${remount.ifBlank { "(пусто)" }}")
        if (!remount.contains("remount succeeded", ignoreCase = true)) {
            return "системный раздел не открылся на запись (adb remount: ${remount.ifBlank { "нет ответа" }.take(200)})"
        }
        return null
    }

    /** adb root: adbd перезапускается от root и связь рвётся — переподключаемся тем же транспортом (по USB телефон
     * может снова спросить доступ к устройству). null — готово, иначе причина. */
    fun rootAndReconnect(context: Context, log: (String) -> Unit): String? {
        val out = serviceText("root:", log, 15_000)
        log("  adb root: ${out.ifBlank { "(пусто)" }}")
        if (out.contains("cannot run as root", ignoreCase = true) || out.contains("root access is disabled", ignoreCase = true) ||
            out.startsWith("сервис отклонён")) {
            return "магнитола не дала права root (adb root: $out)"
        }
        if (out.contains("already running as root", ignoreCase = true)) return null
        disconnect()
        Thread.sleep(1500)
        log("Жду магнитолу после перезапуска adb (если на телефоне появится запрос доступа к USB — разрешите)…")
        val back = waitForDeviceAndReconnect(context, 60_000, log)
        return if (back is AdbHandshakeResult.Connected) null
        else "магнитола не вернулась после adb root: ${(back as? AdbHandshakeResult.Failed)?.reason ?: "нет ответа"}"
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

/** Транспорт, который помнит, что запись в магнитолу не прошла ([AdbSession.linkLost]). Только запись:
 * чтение по USB возвращает -1 и на обычный таймаут долгой команды (транспорт не отличает его от обрыва),
 * а неудачная запись на живой магнитоле не бывает — это вынутый кабель, уснувшая магнитола или пропавший Wi-Fi. */
class LinkWatch(private val inner: AdbTransport) : AdbTransport {
    @Volatile var lost = false
        private set

    override fun write(bytes: ByteArray, timeoutMs: Int): Boolean =
        inner.write(bytes, timeoutMs).also { if (!it) lost = true }

    /** Магнитола не ответила даже на открытие потока (см. AdbSession.markLinkLost). */
    fun markLost() {
        lost = true
    }

    override fun read(buffer: ByteArray, timeoutMs: Int): Int = inner.read(buffer, timeoutMs)

    override fun close() = inner.close()
}
