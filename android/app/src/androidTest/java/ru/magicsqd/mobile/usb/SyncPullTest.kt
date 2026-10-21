package ru.magicsqd.mobile.usb

import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.ByteArrayOutputStream
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.zip.CRC32
import kotlin.random.Random

/**
 * Снятие APK с магнитолы (syncPull, «Работа в движении»): ход по мегабайтам и остановка кнопкой (лог №8317 — Spotify
 * снимался по Wi-Fi 4 минуты молча). Магнитола — поддельный adbd поверх AdbTransport: отвечает на OPEN sync:, на RECV
 * шлёт файл кусками DATA, каждый следующий — только после нашего OKAY, как настоящий.
 */
@RunWith(AndroidJUnit4::class)
class SyncPullTest {
    private class FakeAdbd(file: ByteArray, chunk: Int = 64 * 1024) : AdbTransport {
        private val packets = ArrayDeque<ByteArray>()
        private val toHost = ByteArrayOutputStream()
        private var toHostPos = 0
        private val fromHost = ByteArrayOutputStream()
        private var localId = 0
        private val remoteId = 77
        var okaysAfterRecv = 0
        var closedByHost = false

        init {
            var offset = 0
            while (offset < file.size) {
                val n = minOf(chunk, file.size - offset)
                packets += syncPacket("DATA", n, file.copyOfRange(offset, offset + n))
                offset += n
            }
            packets += syncPacket("DONE", 0, ByteArray(0))
        }

        private fun syncPacket(id: String, value: Int, data: ByteArray): ByteArray =
            ByteBuffer.allocate(8 + data.size).order(ByteOrder.LITTLE_ENDIAN)
                .put(id.toByteArray(Charsets.US_ASCII)).putInt(value).put(data).array()

        private fun send(command: Int, arg0: Int, arg1: Int, payload: ByteArray = ByteArray(0)) {
            val crc = CRC32().apply { update(payload) }.value.toInt()
            toHost.write(ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN).putInt(command).putInt(arg0)
                .putInt(arg1).putInt(payload.size).putInt(crc).putInt(command.inv()).array())
            toHost.write(payload)
        }

        private fun nextPacket() {
            packets.removeFirstOrNull()?.let { send(AdbProtocol.A_WRTE, remoteId, localId, it) }
        }

        override fun write(bytes: ByteArray, timeoutMs: Int): Boolean {
            fromHost.write(bytes)
            while (true) {
                val buf = fromHost.toByteArray()
                if (buf.size < 24) return true
                val head = ByteBuffer.wrap(buf, 0, 24).order(ByteOrder.LITTLE_ENDIAN)
                val command = head.int; val arg0 = head.int; head.int; val length = head.int
                if (buf.size < 24 + length) return true
                fromHost.reset(); fromHost.write(buf, 24 + length, buf.size - 24 - length)
                when (command) {
                    AdbProtocol.A_OPEN -> { localId = arg0; send(AdbProtocol.A_OKAY, remoteId, localId) }
                    AdbProtocol.A_WRTE -> { send(AdbProtocol.A_OKAY, remoteId, localId); nextPacket() }  // RECV
                    AdbProtocol.A_OKAY -> { okaysAfterRecv++; nextPacket() }
                    AdbProtocol.A_CLSE -> closedByHost = true
                }
            }
        }

        override fun read(buffer: ByteArray, timeoutMs: Int): Int {
            val data = toHost.toByteArray()
            val n = minOf(buffer.size, data.size - toHostPos)
            if (n <= 0) return -1  // как таймаут чтения
            System.arraycopy(data, toHostPos, buffer, 0, n)
            toHostPos += n
            return n
        }

        override fun close() {}
    }

    private val dest = File.createTempFile("pull", ".apk")

    @Test
    fun progressIsLoggedPerMegabyteAndFileIsWhole() {
        val file = Random(1).nextBytes(3 * 1024 * 1024 + 12345)
        val logs = mutableListOf<String>()
        val result = syncPull(FakeAdbd(file), "/data/app/x/base.apk", dest, { logs += it }, file.size.toLong())
        assertTrue(result.toString(), result is AdbPullResult.Success)
        assertArrayEquals(file, dest.readBytes())
        assertTrue(logs.toString(), logs.any { it.startsWith("sync-поток открыт") && it.contains("(${file.size} байт)") })
        assertEquals(listOf("...снято 0MB/3MB", "...снято 1MB/3MB", "...снято 2MB/3MB", "...снято 3MB/3MB"),
            logs.filter { it.startsWith("...снято") })
    }

    @Test
    fun unknownSizeStillShowsProgress() {
        val file = Random(2).nextBytes(2 * 1024 * 1024)
        val logs = mutableListOf<String>()
        assertTrue(syncPull(FakeAdbd(file), "/data/app/x/base.apk", dest, { logs += it }) is AdbPullResult.Success)
        assertTrue(logs.toString(), logs.contains("...снято 1MB"))
    }

    @Test
    fun stopButtonEndsPullWithoutAskingForMore() {
        val file = Random(3).nextBytes(4 * 1024 * 1024)
        val adbd = FakeAdbd(file)
        val logs = mutableListOf<String>()
        var stop = false
        val result = syncPull(adbd, "/data/app/x/base.apk", dest, { line ->
            logs += line
            if (line == "...снято 1MB/4MB") stop = true
        }, file.size.toLong(), { stop })
        assertEquals(AdbPullResult.Failed(PULL_CANCELLED), result)
        val okays = adbd.okaysAfterRecv
        assertTrue("после остановки устройство больше не просили слать: $okays", okays in 16..17)
        assertTrue("поток закрыт нами", adbd.closedByHost)
        assertTrue(dest.length() < file.size)
    }
}
