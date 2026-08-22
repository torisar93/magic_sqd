package ru.magicsqd.mobile.usb

import android.content.Context
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import java.io.ByteArrayOutputStream
import java.net.Socket
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.interfaces.RSAPrivateKey
import java.security.interfaces.RSAPublicKey
import java.util.zip.CRC32

/**
 * САМОДЕЛЬНАЯ реализация ADB-протокола (CNXN + AUTH + OPEN/WRTE/OKAY/CLSE)
 * поверх [AdbTransport] — абстракция над байтовым каналом, т.к. сам протокол
 * (framing сообщений) одинаков что по USB (единственный способ проводного
 * ADB без root — штатный usbfs, которым пользуется adb.exe, обычным
 * приложениям недоступен), что по TCP (Wi-Fi ADB — `adb connect ip:port` на
 * desktop, см. cars/_shared/wifi_adb.py — у нас нет ADB-сервера, поэтому
 * тот же самодельный клиент, что и для USB, просто поверх Socket).
 *
 * Протокол взят из публичной спецификации AOSP (system/core/adb/protocol.txt),
 * AUTH — из эталонной python-реализации adb_shell (см. AdbAuth.kt). Проверено
 * на практике: CNXN без auth — на ESP32-S3 стенде (fake ADB device),
 * CNXN+AUTH (весь путь TOKEN->SIGNATURE->RSAPUBLICKEY->подтверждение на
 * экране) и полная установка APK — на реальном Android-телефоне по USB.
 * TCP-транспорт пока не проверялся на реальном железе (протокол
 * transport-agnostic, но живая магнитола с Wi-Fi ADB пока не попадалась).
 */
object AdbProtocol {
    const val A_SYNC = 0x434e5953
    const val A_CNXN = 0x4e584e43
    const val A_OPEN = 0x4e45504f
    const val A_OKAY = 0x59414b4f
    const val A_CLSE = 0x45534c43
    const val A_WRTE = 0x45545257
    const val A_AUTH = 0x48545541

    const val A_VERSION = 0x01000000

    const val ADB_AUTH_TOKEN = 1
    const val ADB_AUTH_SIGNATURE = 2
    const val ADB_AUTH_RSAPUBLICKEY = 3

    // Интерфейс ADB на устройстве всегда объявлен с этим class/subclass/protocol —
    // так их ищет и настоящий adb, и любой сторонний USB-ADB клиент.
    const val ADB_CLASS = 0xff
    const val ADB_SUBCLASS = 0x42
    const val ADB_PROTOCOL = 0x01
}

/**
 * Байтовый канал для ADB-сообщений — либо USB bulk-эндпоинты, либо TCP-сокет
 * (Wi-Fi ADB). read() обязан вернуть РОВНО buffer.size байт (или меньше —
 * только если канал закрылся/оборвался), даже если транспорт сам по себе
 * такое не гарантирует за один вызов (TCP-поток не хранит границ сообщений,
 * в отличие от USB bulkTransfer, которая на практике отдаёт цельный transfer
 * целиком) — иначе разбор 24-байтного заголовка выше по стеку сломается.
 */
interface AdbTransport {
    fun write(bytes: ByteArray, timeoutMs: Int): Boolean
    fun read(buffer: ByteArray, timeoutMs: Int): Int
    fun close()
}

data class AdbUsbInterface(
    val usbInterface: UsbInterface,
    val endpointIn: UsbEndpoint,
    val endpointOut: UsbEndpoint,
)

/** [AdbTransport] поверх уже claimed USB-интерфейса. */
class UsbAdbTransport(
    private val connection: UsbDeviceConnection,
    private val iface: AdbUsbInterface,
) : AdbTransport {
    override fun write(bytes: ByteArray, timeoutMs: Int): Boolean {
        val sent = connection.bulkTransfer(iface.endpointOut, bytes, bytes.size, timeoutMs)
        return sent == bytes.size
    }

    override fun read(buffer: ByteArray, timeoutMs: Int): Int =
        connection.bulkTransfer(iface.endpointIn, buffer, buffer.size, timeoutMs)

    override fun close() {
        try {
            connection.releaseInterface(iface.usbInterface)
        } catch (_: Exception) {
        }
        connection.close()
    }
}

/**
 * [AdbTransport] поверх голого TCP-сокета — Wi-Fi ADB (порт обычно 5555,
 * но у некоторых магнитол свой, см. NewCarSpec.wifi_port на desktop).
 * read() читает ПОЛНОСТЬЮ (loop), т.к. TCP не сохраняет границы записей —
 * один InputStream.read() может вернуть меньше, чем реально уже пришло.
 */
class TcpAdbTransport(private val socket: Socket) : AdbTransport {
    override fun write(bytes: ByteArray, timeoutMs: Int): Boolean {
        return try {
            socket.soTimeout = timeoutMs
            val out = socket.getOutputStream()
            out.write(bytes)
            out.flush()
            true
        } catch (_: Exception) {
            false
        }
    }

    override fun read(buffer: ByteArray, timeoutMs: Int): Int {
        return try {
            socket.soTimeout = timeoutMs
            val input = socket.getInputStream()
            var total = 0
            while (total < buffer.size) {
                val n = input.read(buffer, total, buffer.size - total)
                if (n <= 0) break
                total += n
            }
            total
        } catch (_: Exception) {
            -1
        }
    }

    override fun close() {
        try {
            socket.close()
        } catch (_: Exception) {
        }
    }
}

/** Ищет на устройстве интерфейс с сигнатурой ADB (class 0xff/subclass 0x42/protocol 0x01). */
fun findAdbInterface(device: UsbDevice): AdbUsbInterface? {
    for (i in 0 until device.interfaceCount) {
        val iface = device.getInterface(i)
        if (iface.interfaceClass != AdbProtocol.ADB_CLASS ||
            iface.interfaceSubclass != AdbProtocol.ADB_SUBCLASS ||
            iface.interfaceProtocol != AdbProtocol.ADB_PROTOCOL
        ) continue

        var epIn: UsbEndpoint? = null
        var epOut: UsbEndpoint? = null
        for (e in 0 until iface.endpointCount) {
            val ep = iface.getEndpoint(e)
            if (ep.type != UsbConstants.USB_ENDPOINT_XFER_BULK) continue
            if (ep.direction == UsbConstants.USB_DIR_IN) epIn = ep
            if (ep.direction == UsbConstants.USB_DIR_OUT) epOut = ep
        }
        if (epIn != null && epOut != null) {
            return AdbUsbInterface(iface, epIn, epOut)
        }
    }
    return null
}

private fun buildMessage(command: Int, arg0: Int, arg1: Int, payload: ByteArray): ByteArray {
    val crc = CRC32().apply { update(payload) }.value.toInt()
    val header = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN)
    header.putInt(command)
    header.putInt(arg0)
    header.putInt(arg1)
    header.putInt(payload.size)
    header.putInt(crc)
    header.putInt(command.inv())
    return header.array()
}

data class AdbMessageHeader(
    val command: Int,
    val arg0: Int,
    val arg1: Int,
    val dataLength: Int,
    val dataCrc32: Int,
)

private fun parseHeader(bytes: ByteArray): AdbMessageHeader {
    val buf = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
    return AdbMessageHeader(
        command = buf.int,
        arg0 = buf.int,
        arg1 = buf.int,
        dataLength = buf.int,
        dataCrc32 = buf.int,
        // 6-е поле (magic) не проверяем отдельно — оно избыточно (== command.inv()).
    )
}

private const val TIMEOUT_MS = 5000

// Ожидание после отправки RSAPUBLICKEY — требует, чтобы человек физически
// нажал "Разрешить" на экране устройства, поэтому таймаут намного больше.
private const val AUTH_APPROVAL_TIMEOUT_MS = 30000

/** Читает одно ADB-сообщение (заголовок + payload, если есть). */
fun readMessage(
    transport: AdbTransport,
    timeoutMs: Int = TIMEOUT_MS,
): Pair<AdbMessageHeader, ByteArray> {
    val headerBuf = ByteArray(24)
    val read = transport.read(headerBuf, timeoutMs)
    check(read == 24) { "Ожидали 24-байтный заголовок, получили $read байт" }
    val header = parseHeader(headerBuf)
    val payload = if (header.dataLength > 0) {
        val buf = ByteArray(header.dataLength)
        val readData = transport.read(buf, timeoutMs)
        if (readData > 0) buf.copyOf(readData) else ByteArray(0)
    } else ByteArray(0)
    return header to payload
}

/**
 * Читает следующее сообщение, АДРЕСОВАННОЕ конкретно потоку myLocalId
 * (device всегда кладёт наш local-id в arg1 своего ответа), пропуская и
 * логируя всё остальное. Без этой фильтрации "прилипший" непрочитанный ответ
 * от только что закрытого потока (например device-side CLSE-эхо на наш CLSE,
 * который мы раньше просто не вычитывали) может быть ошибочно принят за ответ
 * на следующий OPEN совсем другого потока — похоже, именно это ломало sync:
 * сразу после успешного shell.
 */
fun readMessageForStream(
    transport: AdbTransport,
    myLocalId: Int,
    log: (String) -> Unit,
    timeoutMs: Int = TIMEOUT_MS,
): Pair<AdbMessageHeader, ByteArray> {
    repeat(20) {
        val (header, payload) = readMessage(transport, timeoutMs)
        if (header.arg1 == myLocalId) return header to payload
        log("Пропускаю чужое сообщение (arg1=${header.arg1}, ждали $myLocalId): command=0x${header.command.toUInt().toString(16)}")
    }
    error("Не дождались сообщения для потока $myLocalId после 20 попыток")
}

/** Шлёт одно ADB-сообщение (заголовок и payload — двумя отдельными записями). */
fun sendMessage(
    transport: AdbTransport,
    command: Int,
    arg0: Int,
    arg1: Int,
    payload: ByteArray,
    timeoutMs: Int = TIMEOUT_MS,
): Boolean {
    val header = buildMessage(command, arg0, arg1, payload)
    if (!transport.write(header, timeoutMs)) return false
    if (payload.isNotEmpty()) {
        if (!transport.write(payload, timeoutMs)) return false
    }
    return true
}

sealed class AdbHandshakeResult {
    data class Connected(val bannerFromDevice: String) : AdbHandshakeResult()
    data class Failed(val reason: String) : AdbHandshakeResult()
}

/**
 * Полный CNXN(+AUTH) хендшейк поверх уже готового транспорта (USB-интерфейс
 * должен быть claimed заранее вызывающим кодом, TCP-сокет — просто открыт).
 */
fun performCnxnHandshake(
    transport: AdbTransport,
    context: Context,
    log: (String) -> Unit,
): AdbHandshakeResult {
    val banner = "host:: ".toByteArray(Charsets.US_ASCII)
    if (!sendMessage(transport, AdbProtocol.A_CNXN, AdbProtocol.A_VERSION, 0x1000, banner)) {
        return AdbHandshakeResult.Failed("Не удалось отправить CNXN")
    }
    log("CNXN отправлен, жду ответ...")

    val (respHeader, respPayload) = readMessage(transport)
    log("Ответ: command=0x${respHeader.command.toUInt().toString(16)} dataLength=${respHeader.dataLength}")

    return when (respHeader.command) {
        AdbProtocol.A_CNXN -> AdbHandshakeResult.Connected(
            String(respPayload, Charsets.US_ASCII).trimEnd(' ', ' ')
        )
        AdbProtocol.A_AUTH -> performAuth(transport, context, respHeader, respPayload, log)
        else -> AdbHandshakeResult.Failed("Неожиданная команда в ответе: 0x${respHeader.command.toUInt().toString(16)}")
    }
}

/**
 * AUTH-ветка: TOKEN -> подписываем существующим ключом -> SIGNATURE.
 * Если устройство не узнало подпись (ключ ему не знаком) — шлём сам публичный
 * ключ (RSAPUBLICKEY), что вызывает диалог "Разрешить отладку?" на экране
 * устройства, и ждём CNXN с увеличенным таймаутом (нужно время на нажатие).
 */
private fun performAuth(
    transport: AdbTransport,
    context: Context,
    authHeader: AdbMessageHeader,
    authPayload: ByteArray,
    log: (String) -> Unit,
): AdbHandshakeResult {
    if (authHeader.arg0 != AdbProtocol.ADB_AUTH_TOKEN) {
        return AdbHandshakeResult.Failed("Неожиданный тип AUTH: arg0=${authHeader.arg0} (ждали ADB_AUTH_TOKEN=1)")
    }
    log("Получен AUTH TOKEN (${authPayload.size} байт), подписываю сохранённым ключом...")

    val keyPair = AdbAuth.loadOrCreateKeyPair(context)
    val signature = AdbAuth.signToken(keyPair.private as RSAPrivateKey, authPayload)
    if (!sendMessage(transport, AdbProtocol.A_AUTH, AdbProtocol.ADB_AUTH_SIGNATURE, 0, signature)) {
        return AdbHandshakeResult.Failed("Не удалось отправить SIGNATURE")
    }
    log("SIGNATURE отправлена, жду ответ...")

    val (resp2, payload2) = readMessage(transport)
    when (resp2.command) {
        AdbProtocol.A_CNXN -> return AdbHandshakeResult.Connected(
            String(payload2, Charsets.US_ASCII).trimEnd(' ', ' ')
        )
        AdbProtocol.A_AUTH -> {
            log("Подпись не распознана (ключ ещё не доверенный) — отправляю публичный ключ. " +
                "ПОДТВЕРДИ НА ЭКРАНЕ УСТРОЙСТВА диалог разрешения отладки...")
        }
        else -> return AdbHandshakeResult.Failed(
            "Неожиданный ответ после SIGNATURE: 0x${resp2.command.toUInt().toString(16)}"
        )
    }

    val pubKeyPayload = AdbAuth.encodePublicKeyForAdb(keyPair.public as RSAPublicKey)
    if (!sendMessage(transport, AdbProtocol.A_AUTH, AdbProtocol.ADB_AUTH_RSAPUBLICKEY, 0, pubKeyPayload)) {
        return AdbHandshakeResult.Failed("Не удалось отправить RSAPUBLICKEY")
    }

    val (resp3, payload3) = readMessage(transport, AUTH_APPROVAL_TIMEOUT_MS)
    return when (resp3.command) {
        AdbProtocol.A_CNXN -> AdbHandshakeResult.Connected(
            String(payload3, Charsets.US_ASCII).trimEnd(' ', ' ')
        )
        else -> AdbHandshakeResult.Failed(
            "После RSAPUBLICKEY не пришёл CNXN (0x${resp3.command.toUInt().toString(16)}) — " +
                "не подтвердили на экране за ${AUTH_APPROVAL_TIMEOUT_MS / 1000} сек, или устройство отклонило."
        )
    }
}

// Каждый OPEN должен использовать УНИКАЛЬНЫЙ local-id — реальные клиенты (см.
// adb_shell._local_id, инкрементируется на каждый _open, никогда не переиспользуется
// в рамках сессии) никогда не берут маленькие фиксированные числа именно поэтому:
// если adbd по какой-то причине не до конца забыл поток с тем же id из ПРЕДЫДУЩЕГО
// подключения (USB отключили/включили между тестами не идеально чисто), id=1/2
// каждый раз может коллизировать с "призрачным" состоянием на устройстве — похоже,
// именно это и ломало sync: (CLSE сразу на OPEN) при фиксированном id=2 из раза в раз.
// Сид от времени запуска процесса — чтобы разные запуски приложения не пересекались.
private val nextStreamId = java.util.concurrent.atomic.AtomicInteger(
    (System.currentTimeMillis() and 0x7fffffff).toInt().coerceAtLeast(1)
)

fun newLocalStreamId(): Int = nextStreamId.getAndIncrement()

sealed class AdbShellResult {
    data class Output(val text: String) : AdbShellResult()
    data class Rejected(val reason: String) : AdbShellResult()
    data class Failed(val reason: String) : AdbShellResult()
}

/**
 * Открывает ПРОИЗВОЛЬНЫЙ ADB-сервис (не только "shell:command", но и
 * "root:"/"remount:"/"disable-verity:"/"reboot:" и т.п. — см. AOSP
 * system/core/adbd/services.cpp) ПОСЛЕ успешного CNXN. Реализует
 * OPEN -> OKAY -> WRTE*(с ACK) -> CLSE. serviceName уже включает
 * ведущее двоеточие там, где оно нужно (например "shell:echo hi", "root:").
 */
fun runAdbService(
    transport: AdbTransport,
    serviceName: String,
    log: (String) -> Unit,
    timeoutMs: Int = TIMEOUT_MS,
): AdbShellResult {
    // \0-терминатор обязателен (см. AdbInstall.kt) — раньше тут был просто trailing
    // space, который "случайно" не ломал echo, но это не протокольно корректно.
    val service = serviceName.toByteArray(Charsets.UTF_8) + byteArrayOf(0)
    val localId = newLocalStreamId()
    if (!sendMessage(transport, AdbProtocol.A_OPEN, localId, 0, service)) {
        return AdbShellResult.Failed("Не удалось отправить OPEN")
    }
    log("OPEN отправлен ($serviceName), жду OKAY/CLSE...")

    val (openResp, _) = readMessageForStream(transport, localId, log, timeoutMs)
    if (openResp.command == AdbProtocol.A_CLSE) {
        return AdbShellResult.Rejected("Устройство сразу закрыло поток (CLSE) — сервис не поддерживается?")
    }
    if (openResp.command != AdbProtocol.A_OKAY) {
        return AdbShellResult.Failed("Неожиданный ответ на OPEN: 0x${openResp.command.toUInt().toString(16)}")
    }
    val remoteId = openResp.arg0
    log("Поток открыт (remoteId=$remoteId), читаю вывод...")

    val output = ByteArrayOutputStream()
    var guard = 0
    while (guard++ < 1000) { // защита от зависания, если CLSE потеряется
        val (msg, payload) = try {
            readMessageForStream(transport, localId, log, timeoutMs)
        } catch (e: Exception) {
            // "reboot:"/"root:" законно рвут соединение (adbd перезапускается) —
            // это не ошибка выполнения самой команды, а ожидаемый конец потока.
            log("Поток прервался во время чтения (ожидаемо для reboot/root/remount): ${e.message}")
            break
        }
        when (msg.command) {
            AdbProtocol.A_WRTE -> {
                output.write(payload)
                sendMessage(transport, AdbProtocol.A_OKAY, localId, remoteId, ByteArray(0))
            }
            AdbProtocol.A_CLSE -> break
            else -> log("Пропускаю неожиданное сообщение 0x${msg.command.toUInt().toString(16)} во время чтения потока")
        }
        if (msg.command == AdbProtocol.A_CLSE) break
    }
    // Устройство закрыло свою сторону — закрываем и мы свою (как TCP FIN/FIN-ACK).
    sendMessage(transport, AdbProtocol.A_CLSE, localId, remoteId, ByteArray(0))
    return AdbShellResult.Output(String(output.toByteArray(), Charsets.UTF_8))
}

/** Частный случай runAdbService для обычных shell-команд. */
fun runAdbShellCommand(
    transport: AdbTransport,
    command: String,
    log: (String) -> Unit,
    timeoutMs: Int = TIMEOUT_MS,
): AdbShellResult = runAdbService(transport, "shell:$command", log, timeoutMs)
