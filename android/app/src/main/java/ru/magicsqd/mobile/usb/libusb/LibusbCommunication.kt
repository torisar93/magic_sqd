package ru.magicsqd.mobile.usb.libusb

import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbEndpoint
import android.hardware.usb.UsbInterface
import android.hardware.usb.UsbManager
import me.jahnen.libaums.core.usb.PipeException
import me.jahnen.libaums.core.usb.UsbCommunication
import me.jahnen.libaums.core.usb.UsbCommunicationCreator
import java.io.IOException
import java.nio.ByteBuffer

private const val LIBUSB_ERROR_NO_DEVICE = -4
private const val LIBUSB_ERROR_PIPE = -9

/** Сбой libusb: code — LIBUSB_ERROR_*, в тексте — его имя («LIBUSB_ERROR_TIMEOUT»), для журнала и разбора логов. */
class LibusbException(operation: String, val code: Int) : IOException("libusb, $operation: ${LibusbNative.errorName(code)}")

/** JNI-мост android/native/libusb/msqd_usb.c — libmsqd_usb.so и libusb-1.0.so из jniLibs (собирает build.sh там же). */
internal object LibusbNative {
    /** Версия libusb («1.0.30»); первое обращение загружает библиотеки — нет их или не грузятся: UnsatisfiedLinkError,
     * флешка подключается стандартным способом (UsbFlashSession). */
    val version: String by lazy {
        System.loadLibrary("msqd_usb")
        nativeVersion()
    }

    fun errorName(code: Int): String = try { nativeErrorName(code) } catch (_: Throwable) { "код $code" }

    /** 0 — удалось, дескриптор сеанса в handle[0] (любое число, кроме 0: на arm64 указатель с меткой отрицательный). */
    @JvmStatic external fun nativeInit(fd: Int, handle: LongArray): Int
    @JvmStatic external fun nativeClose(handle: Long)
    @JvmStatic external fun nativeBulkTransfer(handle: Long, endpoint: Int, data: ByteArray, offset: Int, length: Int,
                                               timeout: Int): Int
    @JvmStatic external fun nativeControlTransfer(handle: Long, requestType: Int, request: Int, value: Int, index: Int,
                                                  data: ByteArray, length: Int, timeout: Int): Int
    @JvmStatic external fun nativeReset(handle: Long): Int
    @JvmStatic external fun nativeClearHalt(handle: Long, endpoint: Int): Int
    @JvmStatic external fun nativeErrorName(code: Int): String
    @JvmStatic external fun nativeVersion(): String
}

/**
 * Связь libaums с флешкой через libusb — новый способ записи на флешку с телефона (владелец, 18.10.2026); прежний —
 * UsbDeviceConnection.bulkTransfer внутри libaums. Чем плох прежний: с Android 9 bulkTransfer отдаёт ядру кусок
 * libaums (до 256 КБ) одной передачей, ядро ищет под него непрерывную память и на фрагментированной отказывает —
 * запись обрывается «MAX_RECOVERY_ATTEMPTS» (до 40% записей на Android 13/15/16, project_android_flash_max_recovery).
 * libusb отдаёт тот же кусок очередью URB, ядро режет её по 16 КБ; остановку конечной точки (STALL) libusb отличает
 * от прочих сбоев — libaums сбрасывает флешку (BOT reset), а не повторяет команду вслепую. Выбор способа и откат на
 * прежний — UsbFlashTransport.kt.
 *
 * По мотивам libaums/libusbcommunication (Apache-2.0, (c) magnusja, github.com/magnusja/libaums). Изменено: при сбое
 * подключения всё открытое закрывается; закрытие ждёт конца передачи (общий замок) — раньше close из другого потока
 * освобождал память libusb посреди передачи; сбои — IOException с именем ошибки libusb, а не
 * IllegalArgumentException; управляющие передачи и снятие остановки при ошибке не бросают — как у прежнего способа
 * (флешки, которые отвечают STALL на GET MAX LUN, иначе не подключались бы вовсе).
 */
class LibusbCommunication(
    usbManager: UsbManager,
    usbDevice: UsbDevice,
    override val usbInterface: UsbInterface,
    override val outEndpoint: UsbEndpoint,
    override val inEndpoint: UsbEndpoint,
) : UsbCommunication {
    private val lock = Any()
    private val connection: UsbDeviceConnection
    private var handle = 0L  // 0 — закрыто

    init {
        LibusbNative.version
        connection = usbManager.openDevice(usbDevice) ?: throw IOException("libusb: Android не открыл флешку (openDevice)")
        try {
            val opened = LongArray(1)
            val result = LibusbNative.nativeInit(connection.fileDescriptor, opened)
            if (result != 0 || opened[0] == 0L) throw LibusbException("подключение", result)
            handle = opened[0]
            if (!connection.claimInterface(usbInterface, true)) {
                throw IOException("libusb: Android не отдал интерфейс флешки (claimInterface)")
            }
        } catch (e: Throwable) {
            if (handle != 0L) LibusbNative.nativeClose(handle)
            handle = 0L
            connection.close()
            throw e
        }
    }

    private fun open(): Long =
        handle.takeIf { it != 0L } ?: throw LibusbException("подключение закрыто", LIBUSB_ERROR_NO_DEVICE)

    override fun bulkOutTransfer(src: ByteBuffer): Int = bulk(outEndpoint, src, "запись")

    override fun bulkInTransfer(dest: ByteBuffer): Int = bulk(inEndpoint, dest, "чтение")

    private fun bulk(endpoint: UsbEndpoint, buffer: ByteBuffer, what: String): Int = synchronized(lock) {
        val length = buffer.remaining()
        val transferred = LibusbNative.nativeBulkTransfer(open(), endpoint.address, buffer.array(),
            buffer.arrayOffset() + buffer.position(), length, UsbCommunication.TRANSFER_TIMEOUT)
        if (transferred == LIBUSB_ERROR_PIPE) throw PipeException()
        if (transferred < 0) throw LibusbException("$what $length байт", transferred)
        buffer.position(buffer.position() + transferred)
        transferred
    }

    /** Как UsbDeviceConnection.controlTransfer: при сбое -1, без исключения (libaums так проверяет результат). */
    override fun controlTransfer(requestType: Int, request: Int, value: Int, index: Int, buffer: ByteArray, length: Int): Int =
        synchronized(lock) {
            val result = LibusbNative.nativeControlTransfer(open(), requestType, request, value, index, buffer, length,
                UsbCommunication.TRANSFER_TIMEOUT)
            if (result < 0) -1 else result
        }

    override fun resetDevice() {
        synchronized(lock) {
            val opened = open()
            connection.releaseInterface(usbInterface)
            val result = LibusbNative.nativeReset(opened)
            if (!connection.claimInterface(usbInterface, true)) {
                throw IOException("libusb: после сброса (${LibusbNative.errorName(result)}) Android не отдал интерфейс флешки")
            }
        }
    }

    /** Как прежний способ: не вышло — libaums всё равно повторит команду. */
    override fun clearFeatureHalt(endpoint: UsbEndpoint) {
        synchronized(lock) { LibusbNative.nativeClearHalt(open(), endpoint.address) }
    }

    override fun close() {
        synchronized(lock) {
            val opened = handle
            if (opened == 0L) return
            handle = 0L
            try {
                connection.releaseInterface(usbInterface)
            } finally {
                LibusbNative.nativeClose(opened)
                connection.close()
            }
        }
    }
}

/** Для UsbCommunicationFactory (режим OTHER) — его включает UsbFlashTransport.kt только на время подключения. */
class LibusbCommunicationCreator : UsbCommunicationCreator {
    override fun create(usbManager: UsbManager, usbDevice: UsbDevice, usbInterface: UsbInterface, outEndpoint: UsbEndpoint,
                        inEndpoint: UsbEndpoint): UsbCommunication =
        LibusbCommunication(usbManager, usbDevice, usbInterface, outEndpoint, inEndpoint)
}
