package ru.magicsqd.mobile.usb

import java.io.ByteArrayOutputStream
import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

sealed class AdbPullResult {
    data class Success(val bytes: Long) : AdbPullResult()
    data class Failed(val reason: String) : AdbPullResult()
}

/**
 * Разбор ответа sync RECV (см. syncPull в AdbInstall.kt): последовательность пакетов «id[4] + len32 + данные»
 * (DATA — кусок файла, DONE — конец, FAIL — ошибка с сообщением) приходит внутри ADB-сообщений WRTE, и один пакет
 * (и даже его 8-байтный заголовок) может быть разорван между сообщениями. Отдельным классом и файлом без Android-
 * зависимостей — чтобы проверить сборку файла на «злых» разбиениях без реального устройства (тест в scratchpad/
 * jvmtest: AdbSyncRecvTest). Данные пишутся в out по мере прихода; feed возвращает статус.
 */
class SyncRecvParser(
    private val out: OutputStream,
    private val maxData: Int = 256 * 1024,          // DATA по спецификации ≤ 64 КБ; запас на нестандартный adbd
    private val maxTotal: Long = 700L * 1024 * 1024,
) {
    sealed class Status {
        object More : Status()
        data class Done(val bytes: Long) : Status()
        data class Fail(val reason: String) : Status()
    }

    private val header = ByteArray(8)
    private var headerLen = 0
    private var dataLeft = 0
    private var failLeft = -1
    private val failMsg = ByteArrayOutputStream()
    var total = 0L
        private set

    fun feed(payload: ByteArray, len: Int = payload.size): Status {
        var i = 0
        while (i < len) {
            if (dataLeft > 0) {
                val n = minOf(dataLeft, len - i)
                out.write(payload, i, n); i += n; dataLeft -= n; total += n
                if (total > maxTotal) return Status.Fail("Файл слишком большой")
                continue
            }
            if (failLeft >= 0) {
                val n = minOf(failLeft, len - i)
                failMsg.write(payload, i, n); i += n; failLeft -= n
                if (failLeft == 0) return Status.Fail("устройство отказало: ${failMsg.toString("UTF-8").take(200)}")
                continue
            }
            val need = 8 - headerLen
            val n = minOf(need, len - i)
            System.arraycopy(payload, i, header, headerLen, n); headerLen += n; i += n
            if (headerLen < 8) continue
            headerLen = 0
            val id = String(header, 0, 4, Charsets.US_ASCII)
            val plen = ByteBuffer.wrap(header, 4, 4).order(ByteOrder.LITTLE_ENDIAN).int
            when (id) {
                "DATA" -> { if (plen < 0 || plen > maxData) return Status.Fail("Некорректный размер DATA ($plen)"); dataLeft = plen }
                "DONE" -> { out.flush(); return Status.Done(total) }
                "FAIL" -> { failLeft = maxOf(0, plen); if (failLeft == 0) return Status.Fail("устройство отказало без причины") }
                else -> return Status.Fail("Неизвестный ответ sync: $id")
            }
        }
        return Status.More
    }
}
