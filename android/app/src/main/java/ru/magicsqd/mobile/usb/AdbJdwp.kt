package ru.magicsqd.mobile.usb

import java.io.ByteArrayOutputStream

/**
 * JDWP-патч белого списка установки для магнитол Desay Semidrive x9h (Haval Jolion 2026 / TR01025,
 * GWM Poer 2026 / TR4314 и родня). Прошивка с ro.debuggable=1 блокирует сторонние APK белым списком в
 * PackageManagerService.mInstallWhiteList — обычный pm install даёт INSTALL_FAILED_ABORTED / -115 (лог #364).
 * По JDWP дописываем имя пакета в этот список В ПАМЯТИ (только чтение/запись полей и массивов, без вызова
 * методов и точек останова — не нужен полноценный JDK), затем pm install. Патч сбрасывается при
 * перезагрузке; установленное приложение остаётся.
 *
 * Точная копия проверенного на моке app/jdwp_whitelist.py (desktop). Транспорт — ADB-поток к сервису
 * `jdwp:<pid>` (см. AdbByteStream): OPEN/WRTE/OKAY-обёртка вокруг наших ADB-примитивов, а не adb forward.
 */

class JdwpException(message: String) : RuntimeException(message)

/** Двунаправленный байтовый поток поверх одного ADB-стрима (сервис открыт вызывающим). JDWP синхронный:
 * пишем запрос целиком, читаем ответ целиком; попутные WRTE от устройства буферизуем и подтверждаем OKAY. */
class AdbByteStream(
    private val transport: AdbTransport,
    private val localId: Int,
    private val remoteId: Int,
    private val log: (String) -> Unit,
    private val timeoutMs: Int = 20000,
    private val maxPayload: Int = 4096,
) {
    private val readBuf = ArrayDeque<Byte>()

    fun write(data: ByteArray) {
        var off = 0
        while (off < data.size) {
            val end = minOf(off + maxPayload, data.size)
            val chunk = data.copyOfRange(off, end)
            if (!sendMessage(transport, AdbProtocol.A_WRTE, localId, remoteId, chunk)) {
                throw JdwpException("не удалось отправить данные в JDWP-поток")
            }
            off = end
            awaitOkay()
        }
    }

    private fun awaitOkay() {
        while (true) {
            val (header, payload) = readMessageForStream(transport, localId, log, timeoutMs)
            when (header.command) {
                AdbProtocol.A_OKAY -> return
                AdbProtocol.A_WRTE -> {
                    payload.forEach { readBuf.addLast(it) }
                    sendMessage(transport, AdbProtocol.A_OKAY, localId, remoteId, ByteArray(0))
                }
                AdbProtocol.A_CLSE -> throw JdwpException("JDWP-поток закрыт устройством")
                else -> {}
            }
        }
    }

    fun readExact(n: Int): ByteArray {
        while (readBuf.size < n) {
            val (header, payload) = readMessageForStream(transport, localId, log, timeoutMs)
            when (header.command) {
                AdbProtocol.A_WRTE -> {
                    payload.forEach { readBuf.addLast(it) }
                    sendMessage(transport, AdbProtocol.A_OKAY, localId, remoteId, ByteArray(0))
                }
                AdbProtocol.A_OKAY -> {}
                AdbProtocol.A_CLSE -> throw JdwpException("JDWP-поток закрыт устройством раньше времени")
                else -> {}
            }
        }
        val out = ByteArray(n)
        for (i in 0 until n) out[i] = readBuf.removeFirst()
        return out
    }

    fun close() {
        try { sendMessage(transport, AdbProtocol.A_CLSE, localId, remoteId, ByteArray(0)) } catch (_: Exception) {}
    }
}

class JdwpClient(private val stream: AdbByteStream) {
    private var nextId = 1
    private var fieldIdSize = 8
    private var objectIdSize = 8
    private var refTypeIdSize = 8

    private companion object {
        val HANDSHAKE = "JDWP-Handshake".toByteArray(Charsets.US_ASCII)
        const val VM = 1; const val VM_IDSIZES = 7; const val VM_SUSPEND = 8; const val VM_RESUME = 9
        const val VM_DISPOSE = 6; const val VM_CREATE_STRING = 11; const val VM_CLASSES_BY_SIG = 2
        const val REF = 2; const val REF_FIELDS = 4; const val REF_INSTANCES = 16
        const val OBJ = 9; const val OBJ_REFERENCE_TYPE = 1; const val OBJ_GET_VALUES = 2; const val OBJ_SET_VALUES = 3
        const val STR = 10; const val STR_VALUE = 1
        const val ARR = 13; const val ARR_LENGTH = 1; const val ARR_GET_VALUES = 2; const val ARR_SET_VALUES = 3
        const val ARRTYPE = 4; const val ARRTYPE_NEW_INSTANCE = 1
        const val TAG_INT = 'I'.code
        val OBJECT_TAGS = setOf('L'.code, '['.code, 's'.code, 't'.code, 'g'.code, 'l'.code, 'c'.code)
        const val PMS_SIGNATURE = "Lcom/android/server/pm/PackageManagerService;"
        const val WHITELIST_FIELD = "mInstallWhiteList"
    }

    // -- запись/чтение целых big-endian --
    private fun putInt(out: ByteArrayOutputStream, v: Int) {
        out.write((v ushr 24) and 0xFF); out.write((v ushr 16) and 0xFF); out.write((v ushr 8) and 0xFF); out.write(v and 0xFF)
    }
    private fun putId(out: ByteArrayOutputStream, v: Long, size: Int) {
        for (i in size - 1 downTo 0) out.write(((v ushr (8 * i)) and 0xFF).toInt())
    }
    private fun readInt(b: ByteArray, off: Int): Int =
        ((b[off].toInt() and 0xFF) shl 24) or ((b[off + 1].toInt() and 0xFF) shl 16) or
            ((b[off + 2].toInt() and 0xFF) shl 8) or (b[off + 3].toInt() and 0xFF)
    private fun readId(b: ByteArray, off: Int, size: Int): Long {
        var v = 0L
        for (i in 0 until size) v = (v shl 8) or (b[off + i].toLong() and 0xFF)
        return v
    }

    private fun command(cmdSet: Int, cmd: Int, data: ByteArray = ByteArray(0)): ByteArray {
        val id = nextId++
        val length = 11 + data.size
        val header = ByteArrayOutputStream()
        putInt(header, length); putInt(header, id); header.write(0); header.write(cmdSet); header.write(cmd)
        stream.write(header.toByteArray() + data)
        while (true) {
            val head = stream.readExact(9)
            val replyLen = readInt(head, 0); val replyId = readInt(head, 4); val flags = head[8].toInt() and 0xFF
            val rest = stream.readExact(replyLen - 9)
            if (flags and 0x80 == 0) continue          // командный пакет от VM (событие) — пропускаем
            if (replyId != id) continue
            val error = ((rest[0].toInt() and 0xFF) shl 8) or (rest[1].toInt() and 0xFF)
            if (error != 0) throw JdwpException("JDWP-команда $cmdSet/$cmd вернула ошибку $error")
            return rest.copyOfRange(2, rest.size)
        }
    }

    fun handshake() {
        stream.write(HANDSHAKE)
        val reply = stream.readExact(HANDSHAKE.size)
        if (!reply.contentEquals(HANDSHAKE)) throw JdwpException("нет JDWP-рукопожатия (system_server не отлаживается?)")
    }

    fun idSizes() {
        val d = command(VM, VM_IDSIZES)
        fieldIdSize = readInt(d, 0)
        objectIdSize = readInt(d, 8)
        refTypeIdSize = readInt(d, 12)
    }

    fun suspend() { command(VM, VM_SUSPEND) }
    fun resume() { command(VM, VM_RESUME) }
    fun dispose() { try { command(VM, VM_DISPOSE) } catch (_: Exception) {} }

    private fun stringPayload(text: String): ByteArray {
        val b = text.toByteArray(Charsets.UTF_8)
        val out = ByteArrayOutputStream(); putInt(out, b.size); out.write(b); return out.toByteArray()
    }
    private fun readJdwpString(b: ByteArray, off: Int): Pair<String, Int> {
        val len = readInt(b, off)
        return String(b, off + 4, len, Charsets.UTF_8) to (off + 4 + len)
    }

    fun classesBySignature(signature: String): Long? {
        val d = command(VM, VM_CLASSES_BY_SIG, stringPayload(signature))
        if (readInt(d, 0) == 0) return null
        return readId(d, 5, refTypeIdSize) // int count, byte tag, затем referenceTypeID
    }

    fun createString(text: String): Long {
        val d = command(VM, VM_CREATE_STRING, stringPayload(text))
        return readId(d, 0, objectIdSize)
    }

    /** Имя поля → fieldID (только объявленные в самом типе). */
    fun fields(refTypeId: Long): Map<String, Long> {
        val out = ByteArrayOutputStream(); putId(out, refTypeId, refTypeIdSize)
        val d = command(REF, REF_FIELDS, out.toByteArray())
        val declared = readInt(d, 0)
        var off = 4
        val result = HashMap<String, Long>()
        repeat(declared) {
            val fid = readId(d, off, fieldIdSize); off += fieldIdSize
            val (name, o1) = readJdwpString(d, off); off = o1
            val (_, o2) = readJdwpString(d, off); off = o2
            off += 4 // modBits
            result[name] = fid
        }
        return result
    }

    fun instances(refTypeId: Long, max: Int = 0): List<Long> {
        val out = ByteArrayOutputStream(); putId(out, refTypeId, refTypeIdSize); putInt(out, max)
        val d = command(REF, REF_INSTANCES, out.toByteArray())
        val count = readInt(d, 0); var off = 4
        val result = ArrayList<Long>()
        repeat(count) { off += 1; result.add(readId(d, off, objectIdSize)); off += objectIdSize }
        return result
    }

    fun objectReferenceType(objectId: Long): Long {
        val out = ByteArrayOutputStream(); putId(out, objectId, objectIdSize)
        val d = command(OBJ, OBJ_REFERENCE_TYPE, out.toByteArray())
        return readId(d, 1, refTypeIdSize) // byte refTypeTag, затем referenceTypeID
    }

    /** Значение одного поля-объекта: (tag, objectID). */
    fun getObjectField(objectId: Long, fieldId: Long): Long {
        val out = ByteArrayOutputStream(); putId(out, objectId, objectIdSize); putInt(out, 1); putId(out, fieldId, fieldIdSize)
        val d = command(OBJ, OBJ_GET_VALUES, out.toByteArray())
        return readId(d, 5, objectIdSize) // int count, byte tag, затем objectID
    }

    fun getIntField(objectId: Long, fieldId: Long): Int {
        val out = ByteArrayOutputStream(); putId(out, objectId, objectIdSize); putInt(out, 1); putId(out, fieldId, fieldIdSize)
        val d = command(OBJ, OBJ_GET_VALUES, out.toByteArray())
        return readInt(d, 5) // int count, byte tag=I, затем int
    }

    fun setObjectField(objectId: Long, fieldId: Long, valueObjectId: Long) {
        val out = ByteArrayOutputStream()
        putId(out, objectId, objectIdSize); putInt(out, 1); putId(out, fieldId, fieldIdSize); putId(out, valueObjectId, objectIdSize)
        command(OBJ, OBJ_SET_VALUES, out.toByteArray())
    }

    fun setIntField(objectId: Long, fieldId: Long, value: Int) {
        val out = ByteArrayOutputStream()
        putId(out, objectId, objectIdSize); putInt(out, 1); putId(out, fieldId, fieldIdSize); putInt(out, value)
        command(OBJ, OBJ_SET_VALUES, out.toByteArray())
    }

    fun arrayLength(arrayId: Long): Int {
        val out = ByteArrayOutputStream(); putId(out, arrayId, objectIdSize)
        return readInt(command(ARR, ARR_LENGTH, out.toByteArray()), 0)
    }

    fun arrayGetObjectIds(arrayId: Long, first: Int, length: Int): List<Long> {
        if (length <= 0) return emptyList()
        val out = ByteArrayOutputStream(); putId(out, arrayId, objectIdSize); putInt(out, first); putInt(out, length)
        val d = command(ARR, ARR_GET_VALUES, out.toByteArray())
        val tag = d[0].toInt() and 0xFF
        if (tag !in OBJECT_TAGS) throw JdwpException("ожидался object-массив, пришёл тег $tag")
        val count = readInt(d, 1); var off = 5
        val result = ArrayList<Long>()
        repeat(count) { off += 1; result.add(readId(d, off, objectIdSize)); off += objectIdSize }
        return result
    }

    fun arraySetObjectIds(arrayId: Long, first: Int, objectIds: List<Long>) {
        val out = ByteArrayOutputStream()
        putId(out, arrayId, objectIdSize); putInt(out, first); putInt(out, objectIds.size)
        for (oid in objectIds) putId(out, oid, objectIdSize)
        command(ARR, ARR_SET_VALUES, out.toByteArray())
    }

    fun newObjectArray(arrayTypeId: Long, length: Int): Long {
        val out = ByteArrayOutputStream(); putId(out, arrayTypeId, refTypeIdSize); putInt(out, length)
        val d = command(ARRTYPE, ARRTYPE_NEW_INSTANCE, out.toByteArray())
        return readId(d, 1, objectIdSize) // byte tag, затем objectID
    }

    fun stringValue(stringId: Long): String {
        if (stringId == 0L) return ""
        val out = ByteArrayOutputStream(); putId(out, stringId, objectIdSize)
        val d = command(STR, STR_VALUE, out.toByteArray())
        return readJdwpString(d, 0).first
    }

    fun patchWhitelist(packages: List<String>, log: (String) -> Unit) {
        handshake(); idSizes(); suspend()
        try {
            val pmsType = classesBySignature(PMS_SIGNATURE)
                ?: throw JdwpException("PackageManagerService не найден в system_server — это не Desay x9h")
            val pmsFields = fields(pmsType)
            val whitelistField = pmsFields[WHITELIST_FIELD]
                ?: throw JdwpException("поле $WHITELIST_FIELD отсутствует — прошивка не поддерживается этим способом")
            val pmsInstances = instances(pmsType, 0)
            if (pmsInstances.isEmpty()) throw JdwpException("живой экземпляр PackageManagerService не найден")
            val pms = pmsInstances[0]

            val whitelist = getObjectField(pms, whitelistField)
            if (whitelist == 0L) throw JdwpException("mInstallWhiteList пуст (null) — способ не применим")
            val listType = objectReferenceType(whitelist)
            val listFields = fields(listType)
            val elementDataField = listFields["elementData"]
            val sizeField = listFields["size"]
            if (elementDataField == null || sizeField == null) throw JdwpException("не найдены поля ArrayList (elementData/size)")

            for (packageName in packages) {
                val elementData = getObjectField(whitelist, elementDataField)
                val size = getIntField(whitelist, sizeField)
                val existing = if (elementData != 0L) arrayGetObjectIds(elementData, 0, size) else emptyList()
                if (existing.any { it != 0L && stringValue(it) == packageName }) {
                    log("$packageName: уже в белом списке")
                    continue
                }
                val capacity = if (elementData != 0L) arrayLength(elementData) else 0
                val pkgRef = createString(packageName)
                if (elementData != 0L && size < capacity) {
                    arraySetObjectIds(elementData, size, listOf(pkgRef))
                } else {
                    val arrayType = objectReferenceType(elementData)
                    val newArray = newObjectArray(arrayType, maxOf(size + 1, 4))
                    if (size > 0) arraySetObjectIds(newArray, 0, existing)
                    arraySetObjectIds(newArray, size, listOf(pkgRef))
                    setObjectField(whitelist, elementDataField, newArray)
                }
                setIntField(whitelist, sizeField, size + 1)
                log("$packageName: добавлен в белый список (размер ${size + 1})")
            }
        } finally {
            try { resume() } catch (_: Exception) {}
            dispose()
        }
    }
}
