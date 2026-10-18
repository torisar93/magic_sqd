package ru.magicsqd.mobile.usb

import android.content.Context
import android.os.Build
import android.os.SystemClock
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import me.jahnen.libaums.core.UsbMassStorageDevice
import me.jahnen.libaums.core.fs.FileSystem
import me.jahnen.libaums.core.fs.UsbFile
import me.jahnen.libaums.core.usb.UsbCommunication
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.nio.ByteBuffer
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.random.Random

/**
 * Запись на флешку через libusb и прежним способом — на эмуляторе с флешкой QEMU (android/native/libusb/README.md:
 * «Проверка на эмуляторе»). Каждый тест — отдельным запуском am instrument (состояние FlashTransports — на процесс).
 */
@RunWith(AndroidJUnit4::class)
class LibusbFlashTest {
    private val context: Context = InstrumentationRegistry.getInstrumentation().targetContext
    private val logs = mutableListOf<String>()
    private val log: (String) -> Unit = { line -> synchronized(logs) { logs += line }; Log.i(TAG, line) }

    private fun permittedDevice(): UsbMassStorageDevice {
        val device = listMassStorageDevices(context).first()
        val latch = CountDownLatch(1)
        var granted = false
        requestUsbPermission(context, device.usbDevice) { granted = it; latch.countDown() }
        latch.await(120, TimeUnit.SECONDS)
        assertTrue("нет разрешения на флешку", granted)
        return device
    }

    private fun writeChunked(dir: UsbFile, name: String, data: ByteArray, chunk: Int) {
        dir.search(name)?.delete()
        val file = dir.createFile(name)
        var offset = 0
        while (offset < data.size) {
            val n = minOf(chunk, data.size - offset)
            file.write(offset.toLong(), ByteBuffer.wrap(data, offset, n))
            offset += n
        }
        file.close()
    }

    private fun readAll(file: UsbFile): ByteArray {
        val buffer = ByteBuffer.allocate(file.length.toInt())
        file.read(0, buffer)
        file.close()
        return buffer.array()
    }

    private fun folder(fs: FileSystem, name: String): UsbFile =
        fs.rootDirectory.search(name) ?: fs.rootDirectory.createDirectory(name)

    /** Кусок i большого файла — заново по тому же зерну: файл в память целиком не помещается (куча эмулятора 48 МБ). */
    private fun chunkOf(i: Int, size: Int): ByteArray = Random(1000 + i).nextBytes(minOf(CHUNK, size - i * CHUNK))

    private fun writeBig(dir: UsbFile, name: String, size: Int) {
        dir.search(name)?.delete()
        val file = dir.createFile(name)
        for (i in 0 until (size + CHUNK - 1) / CHUNK) file.write(i.toLong() * CHUNK, ByteBuffer.wrap(chunkOf(i, size)))
        file.close()
    }

    private fun verifyBig(file: UsbFile, size: Int) {
        assertEquals(size.toLong(), file.length)
        for (i in 0 until (size + CHUNK - 1) / CHUNK) {
            val expected = chunkOf(i, size)
            val buffer = ByteBuffer.allocate(expected.size)
            file.read(i.toLong() * CHUNK, buffer)
            assertArrayEquals("кусок $i", expected, buffer.array())
        }
        file.close()
    }

    /** Большой файл кусками по 4 МБ и 40 мелких — запись, чтение обратно, сверка; заново подключившись — ещё раз. */
    private fun roundTrip(transport: FlashTransport) {
        val bigSize = 40 * 1024 * 1024 + 4321
        val random = Random(42)
        val small = List(40) { random.nextBytes(random.nextInt(1, 300 * 1024)) }
        val name = "msqd_${transport.name.lowercase()}"
        val device = permittedDevice()
        FlashTransports.init(device, transport)
        try {
            val dir = folder(device.partitions[0].fileSystem, name)
            val started = SystemClock.elapsedRealtime()
            writeBig(dir, "big.bin", bigSize)
            val bigMs = SystemClock.elapsedRealtime() - started
            small.forEachIndexed { i, data -> writeChunked(dir, "small_$i.bin", data, CHUNK) }
            val allMs = SystemClock.elapsedRealtime() - started
            log("$transport: 40 МБ за $bigMs мс (${40_000 / maxOf(bigMs, 1)} МБ/с), с мелкими — за $allMs мс")
            val readStarted = SystemClock.elapsedRealtime()
            verifyBig(dir.search("big.bin")!!, bigSize)
            log("$transport: чтение 40 МБ за ${SystemClock.elapsedRealtime() - readStarted} мс")
        } finally {
            device.close()
        }
        val again = permittedDevice()
        FlashTransports.init(again, transport)
        try {
            val dir = again.partitions[0].fileSystem.rootDirectory.search(name)!!
            verifyBig(dir.search("big.bin")!!, bigSize)
            small.forEachIndexed { i, data -> assertArrayEquals("small_$i", data, readAll(dir.search("small_$i.bin")!!)) }
        } finally {
            again.close()
        }
    }

    @Test
    fun libusbRoundTrip() = roundTrip(FlashTransport.LIBUSB)

    @Test
    fun androidRoundTrip() = roundTrip(FlashTransport.ANDROID)

    private fun stageFile(name: String, size: Int, seed: Int): File =
        File(context.cacheDir, name).apply { writeBytes(Random(seed).nextBytes(size)) }

    private fun verifyOnFlash(dest: String, expected: File) {
        val fs = UsbFlashSession.connectBlocking(context, log).getOrThrow()
        var file: UsbFile = fs.rootDirectory
        dest.split("/").forEach { file = file.search(it)!! }
        assertArrayEquals(expected.readBytes(), readAll(file))
        UsbFlashSession.disconnect()
    }

    /** Сессия программы: Android 11+ — libusb, раньше — прежний способ; запись writeFileToUsb и сверка после переподключения. */
    @Test
    fun sessionPicksTransportByAndroidVersion() {
        UsbFlashSession.connectBlocking(context, log).getOrThrow()
        val expected = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) FlashTransport.LIBUSB else FlashTransport.ANDROID
        assertEquals(expected, UsbFlashSession.transport)
        assertTrue(logs.joinToString("\n"), logs.any { it.startsWith("Флешка смонтирована: ") && it.endsWith(FlashTransports.title(expected)) })
        val src = stageFile("stage.bin", 12 * 1024 * 1024 + 123, 7)
        writeFileToUsb(UsbFlashSession.requireFs(), src, "magic_sqd/stage.bin", log)
        UsbFlashSession.disconnect()
        verifyOnFlash("magic_sqd/stage.bin", src)
    }

    /** client.json "settings": {"android_flash_transport": "android"} — libusb выключен с сервера. */
    @Test
    fun serverSwitchTurnsLibusbOff() {
        val config = File(context.filesDir, "client_config.json")
        val previous = config.takeIf { it.exists() }?.readBytes()
        config.writeText("""{"schema": 1, "settings": {"android_flash_transport": "android"}}""")
        try {
            UsbFlashSession.connectBlocking(context, log).getOrThrow()
            assertEquals(FlashTransport.ANDROID, UsbFlashSession.transport)
            assertTrue(logs.joinToString("\n"), logs.any { "выключен с сервера" in it })
        } finally {
            UsbFlashSession.disconnect()
            if (previous != null) config.writeBytes(previous) else config.delete()
        }
    }

    /** Связь через libusb рвётся посреди работы (закрываем её из-под libaums) — запись сама переходит на прежний
     * способ и проходит; счёт откатов подряд — 1. */
    @Test
    fun brokenLibusbFallsBackToStandardMidWrite() {
        prefs().edit().clear().commit()
        UsbFlashSession.connectBlocking(context, log).getOrThrow()
        assertEquals(FlashTransport.LIBUSB, UsbFlashSession.transport)
        val first = stageFile("first.bin", 3 * 1024 * 1024, 1)
        writeFileToUsb(UsbFlashSession.requireFs(), first, "magic_sqd/first.bin", log)
        currentCommunication().close()
        val second = stageFile("second.bin", 9 * 1024 * 1024 + 7, 2)
        writeFileToUsb(UsbFlashSession.requireFs(), second, "magic_sqd/second.bin", log)
        assertEquals(FlashTransport.ANDROID, UsbFlashSession.transport)
        assertTrue(logs.joinToString("\n"), logs.any { it.startsWith("libusb: сбой при записи magic_sqd/second.bin") })
        assertEquals(1, prefs().getInt("libusb_fallbacks_in_row", 0))
        UsbFlashSession.disconnect()
        // до перезапуска программы — только прежний способ
        verifyOnFlash("magic_sqd/second.bin", second)
        verifyOnFlash("magic_sqd/first.bin", first)
        assertTrue(logs.last { it.startsWith("Флешка смонтирована: ") }.endsWith("стандартный способ"))
        prefs().edit().clear().commit()
    }

    /** Второй откат подряд (стандартный способ справился оба раза) — libusb на этом телефоне выключается. */
    @Test
    fun secondFallbackInRowTurnsLibusbOffOnThisPhone() {
        prefs().edit().clear().putInt("libusb_fallbacks_in_row", 1).commit()
        UsbFlashSession.connectBlocking(context, log).getOrThrow()
        assertEquals(FlashTransport.LIBUSB, UsbFlashSession.transport)
        currentCommunication().close()
        val file = stageFile("third.bin", 2 * 1024 * 1024 + 3, 3)
        writeFileToUsb(UsbFlashSession.requireFs(), file, "magic_sqd/third.bin", log)
        assertTrue(logs.joinToString("\n"), logs.any { it.startsWith("libusb: 2-й откат подряд") })
        assertEquals(ru.magicsqd.mobile.BuildConfig.VERSION_CODE, prefs().getInt("libusb_off_version", -1))
        UsbFlashSession.disconnect()
        verifyOnFlash("magic_sqd/third.bin", file)
        prefs().edit().clear().commit()
    }

    /** Проверка защиты от падения — только по запросу (-e crashGuard 1): часть 1 ждёт, пока процесс уронят снаружи. */
    private fun crashGuardRequested() =
        assumeTrue("только с -e crashGuard 1", InstrumentationRegistry.getArguments().getString("crashGuard") == "1")

    /** Часть 1 из 2: флешка через libusb, процесс ждёт — снаружи его роняют сигналом SIGSEGV (README). */
    @Test
    fun crashGuardMountAndWait() {
        crashGuardRequested()
        prefs().edit().clear().commit()
        UsbFlashSession.connectBlocking(context, log).getOrThrow()
        assertEquals(FlashTransport.LIBUSB, UsbFlashSession.transport)
        Log.i(TAG, "ready-for-crash pid=${android.os.Process.myPid()}")
        SystemClock.sleep(120_000)
    }

    /** Часть 2 из 2: после падения в машинном коде при флешке через libusb — на этом телефоне прежний способ. */
    @Test
    fun crashGuardAfterCrash() {
        crashGuardRequested()
        UsbFlashSession.connectBlocking(context, log).getOrThrow()
        assertEquals(FlashTransport.ANDROID, UsbFlashSession.transport)
        assertTrue(logs.joinToString("\n"), logs.any { it.startsWith("libusb: прошлый запуск программы упал") })
        UsbFlashSession.disconnect()
        prefs().edit().clear().commit()
    }

    private fun prefs() = context.getSharedPreferences("usb_flash_transport", Context.MODE_PRIVATE)

    private fun currentCommunication(): UsbCommunication {
        val deviceField = UsbFlashSession::class.java.getDeclaredField("device").apply { isAccessible = true }
        val device = deviceField.get(null) as UsbMassStorageDevice
        val field = UsbMassStorageDevice::class.java.getDeclaredField("usbCommunication").apply { isAccessible = true }
        return field.get(device) as UsbCommunication
    }

    companion object {
        private const val TAG = "LibusbFlashTest"
        private const val CHUNK = 4 * 1024 * 1024
    }
}
