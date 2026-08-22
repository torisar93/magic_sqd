package ru.magicsqd.mobile.usb

import me.jahnen.libaums.core.partition.Partition
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Форматирование флешки в FAT32 "с нуля" — libaums (см. UsbFlashSpike.kt) умеет
 * только ЧИТАТЬ/писать файлы на уже существующей FAT32-файловой системе,
 * форматирования в библиотеке нет (`Fat32FileSystemCreator`/`Fat32BootSector`
 * внутри libaums имеют только `read()`, не `create`/`format`, и вдобавок
 * помечены `internal` — недоступны отсюда). Пишем свой минимальный FAT32-writer
 * поверх сырого `BlockDeviceDriver` (тут — `Partition`, который сам транслирует
 * offset'ы относительно начала раздела, так что таблицу разделов/MBR трогать
 * не нужно — только содержимое ВНУТРИ существующего раздела).
 *
 * Раскладка полей — по официальной спецификации Microsoft (fatgen103), СВЕРЕНА
 * (2026-08-21) byte-in-byte с hex-дампом boot sector'а/FSInfo реальной флешки
 * (~29.6GB), отформатированной проверенным сторонним Android-приложением —
 * см. dumpRawSectors(). Совпало почти всё (сигнатуры, RootClus, NumFATs,
 * RsvdSecCnt, Nxt_Free=3 — всё идентично); единственное расхождение —
 * BPB_HiddSec: реальный форматтер пишет 2048 (1MiB-выравнивание раздела),
 * мы пишем 0. Оставили 0 сознательно: узнать РЕАЛЬНОЕ смещение раздела на
 * диске из публичного API libaums нельзя (PartitionTableEntry — internal),
 * а хардкодить 2048 рискованно для флешек с другим выравниванием (в т.ч.
 * superfloppy без MBR, где HiddSec действительно должен быть 0). Поле
 * используется только для legacy BIOS/CHS-трансляции, современные ридеры
 * (включая embedded Linux в магнитолах) его не валидируют.
 *
 * В отличие от ADB здесь цена ошибки выше (испорченная файловая система может
 * потребовать ремонта через ПК), поэтому сразу после записи делаем
 * самопроверку — читаем результат обратно через ТОТ ЖЕ Fat32FileSystem.read(),
 * которым читает вся остальная часть приложения, и только это признаём
 * успехом.
 */

private const val BYTES_PER_SECTOR = 512
private const val RESERVED_SECTORS = 32
private const val NUM_FATS = 2

data class Fat32Layout(
    val totalSectors: Long,
    val sectorsPerCluster: Int,
    val fatSizeSectors: Long,
    val rootCluster: Int = 2,
) {
    val firstFatSector: Long get() = RESERVED_SECTORS.toLong()
    val firstDataSector: Long get() = RESERVED_SECTORS + NUM_FATS.toLong() * fatSizeSectors
    val dataSectors: Long get() = totalSectors - firstDataSector
    val totalDataClusters: Long get() = dataSectors / sectorsPerCluster
}

/**
 * Пороги сверены с byte-in-byte дампом boot sector'а реальной флешки (~29.6GB),
 * отформатированной проверенным сторонним Android-приложением: оно выбрало
 * 64 сектора/кластер (32KB), а не 32 (16KB), как в классической таблице
 * Microsoft для диапазона 16-32GB. Судя по всему у реальных мобильных
 * форматтеров порог проще: >16GB сразу 32KB, без промежуточной ступени.
 */
private fun pickSectorsPerCluster(totalBytes: Long): Int {
    val gb = 1024L * 1024 * 1024
    return when {
        totalBytes < 8L * gb -> 8   // 4KB
        totalBytes < 16L * gb -> 16 // 8KB
        else -> 64                  // 32KB (подтверждено эмпирически на >16GB)
    }
}

/** FATSz32 по формуле из fatgen103.doc (RootDirSectors всегда 0 для FAT32). */
private fun computeFatSize(totalSectors: Long, sectorsPerCluster: Int): Long {
    val tmpVal1 = totalSectors - RESERVED_SECTORS
    val tmpVal2 = (256L * sectorsPerCluster + NUM_FATS) / 2
    return (tmpVal1 + tmpVal2 - 1) / tmpVal2
}

fun computeFat32Layout(totalCapacityBytes: Long): Fat32Layout {
    val totalSectors = totalCapacityBytes / BYTES_PER_SECTOR
    val sectorsPerCluster = pickSectorsPerCluster(totalCapacityBytes)
    val fatSize = computeFatSize(totalSectors, sectorsPerCluster)
    return Fat32Layout(totalSectors, sectorsPerCluster, fatSize)
}

private fun sector(n: Int): ByteBuffer = ByteBuffer.allocate(n * BYTES_PER_SECTOR).order(ByteOrder.LITTLE_ENDIAN)

private fun buildBootSector(layout: Fat32Layout, volumeSerial: Int, volumeLabel: String): ByteArray {
    val buf = sector(1)
    buf.put(byteArrayOf(0xEB.toByte(), 0x58, 0x90.toByte()))  // BS_jmpBoot
    buf.put("MSWIN4.1".toByteArray(Charsets.US_ASCII))    // BS_OEMName (8)
    buf.putShort(BYTES_PER_SECTOR.toShort())               // BPB_BytsPerSec
    buf.put(layout.sectorsPerCluster.toByte())             // BPB_SecPerClus
    buf.putShort(RESERVED_SECTORS.toShort())               // BPB_RsvdSecCnt
    buf.put(NUM_FATS.toByte())                             // BPB_NumFATs
    buf.putShort(0)                                        // BPB_RootEntCnt (0 для FAT32)
    buf.putShort(0)                                        // BPB_TotSec16 (0, см. TotSec32)
    buf.put(0xF8.toByte())                                 // BPB_Media
    buf.putShort(0)                                        // BPB_FATSz16 (0, см. FATSz32)
    buf.putShort(63)                                       // BPB_SecPerTrk (63 — сверено с реальным дампом)
    buf.putShort(255)                                      // BPB_NumHeads (255 — сверено с реальным дампом)
    buf.putInt(0)                                          // BPB_HiddSec
    buf.putInt(layout.totalSectors.toInt())                // BPB_TotSec32
    buf.putInt(layout.fatSizeSectors.toInt())              // BPB_FATSz32
    buf.putShort(0)                                        // BPB_ExtFlags
    buf.putShort(0)                                        // BPB_FSVer
    buf.putInt(layout.rootCluster)                         // BPB_RootClus
    buf.putShort(1)                                        // BPB_FSInfo (сектор 1)
    buf.putShort(6)                                        // BPB_BkBootSec (сектор 6)
    buf.put(ByteArray(12))                                 // BPB_Reserved
    buf.put(0x80.toByte())                                 // BS_DrvNum
    buf.put(0)                                             // BS_Reserved1
    buf.put(0x29)                                          // BS_BootSig
    buf.putInt(volumeSerial)                               // BS_VolID
    val labelPadded = volumeLabel.uppercase().take(11).padEnd(11, ' ')
    buf.put(labelPadded.toByteArray(Charsets.US_ASCII))    // BS_VolLab (11)
    buf.put("FAT32   ".toByteArray(Charsets.US_ASCII))     // BS_FilSysType (8)
    buf.put(ByteArray(420))                                // boot code — не загрузочный том
    buf.put(0x55)
    buf.put(0xAA.toByte())                                 // сигнатура сектора
    return buf.array()
}

private fun buildFsInfoSector(freeClusterCount: Long, nextFreeCluster: Int): ByteArray {
    val buf = sector(1)
    buf.putInt(0x41615252)                  // LeadSig
    buf.put(ByteArray(480))                 // Reserved1
    buf.putInt(0x61417272)                  // StrucSig
    buf.putInt(freeClusterCount.toInt())    // Free_Count
    buf.putInt(nextFreeCluster)             // Nxt_Free
    buf.put(ByteArray(12))                  // Reserved2
    buf.putInt(-0x55AB0000)                 // TrailSig 0xAA550000 как знаковый Int
    return buf.array()
}

/** Первый сектор FAT: три служебных записи (0,1 — media/EOC, 2 — корневой каталог), остальное — нули (свободно). */
private fun buildFatFirstSector(): ByteArray {
    val buf = sector(1)
    // ИСПРАВЛЕНО: изначально тут стояло -0x1/-0x7L.toInt(), что даёт 0xFFFFFFFF/0xFFFFFFF9 —
    // неверно. 0x0FFFFFF8 и 0x0FFFFFFF умещаются как ПОЛОЖИТЕЛЬНЫЕ Int (старший бит=0),
    // никакого трюка с отрицательными литералами тут не нужно.
    buf.putInt(0x0FFFFFF8)           // FAT[0] = media descriptor + все 1 (кроме служебных бит)
    buf.putInt(0x0FFFFFFF)           // FAT[1] = "чистое" размонтирование
    buf.putInt(0x0FFFFFFF)           // FAT[2] = корневой каталог — один кластер, EOC
    // остаток сектора уже нулевой (ByteBuffer.allocate заполняет нулями)
    return buf.array()
}

/**
 * Ретрай на случай, если MAX_RECOVERY_ATTEMPTS/транспортный сбой — временная
 * помеха (шумный OTG-переходник, флешка "задумалась"), а не системная
 * проблема. Небольшая пауза перед повтором — дать устройству прийти в себя.
 */
private fun writeWithRetry(partition: Partition, offset: Long, buffer: ByteBuffer, log: (String) -> Unit, attempts: Int = 3) {
    var lastError: Exception? = null
    repeat(attempts) { attempt ->
        try {
            buffer.rewind()
            partition.write(offset, buffer)
            return
        } catch (e: Exception) {
            lastError = e
            log("  (запись на offset=$offset упала, попытка ${attempt + 1}/$attempts: ${e.message})")
            Thread.sleep(300)
        }
    }
    throw lastError!!
}

sealed class FormatResult {
    // fs — та же файловая система, которую только что создали и проверили
    // чтением, чтобы вызывающий код мог сразу попробовать что-то на неё
    // записать В ТОЙ ЖЕ сессии, без переоткрытия устройства.
    data class Success(val layout: Fat32Layout, val fs: me.jahnen.libaums.core.fs.FileSystem) : FormatResult()
    data class Failed(val reason: String) : FormatResult()
}

/**
 * Форматирует раздел в FAT32. РАЗРУШИТЕЛЬНАЯ операция — стирает все данные.
 * capacityBytes нужно взять ДО форматирования (например, из старого
 * FileSystem.capacity — см. readFlashInfo), т.к. Partition.blocks в libaums
 * возвращает размер ВСЕГО устройства, а не раздела (см. ByteBlockDevice —
 * blocks не корректируется на partition offset, это единственный надёжный
 * источник размера именно раздела, который доступен отсюда).
 */
fun formatFat32(
    partition: Partition,
    capacityBytes: Long,
    volumeLabel: String,
    log: (String) -> Unit,
): FormatResult {
    val layout = computeFat32Layout(capacityBytes)
    log("Layout: totalSectors=${layout.totalSectors} secPerClus=${layout.sectorsPerCluster} " +
        "fatSize=${layout.fatSizeSectors} firstDataSector=${layout.firstDataSector} " +
        "dataClusters=${layout.totalDataClusters}")

    if (layout.totalDataClusters < 65525) {
        return FormatResult.Failed(
            "Слишком мало кластеров для FAT32 (${layout.totalDataClusters}, нужно >=65525) — " +
                "либо неверно посчитан capacityBytes, либо флешка слишком маленькая."
        )
    }

    val volumeSerial = System.currentTimeMillis().toInt()

    try {
        val bootSector = buildBootSector(layout, volumeSerial, volumeLabel)
        val freeClusters = layout.totalDataClusters - 1 // минус корневой каталог
        val fsInfo = buildFsInfoSector(freeClusters, nextFreeCluster = 3)
        val fatFirstSector = buildFatFirstSector()
        val emptyRootDirCluster = ByteArray(layout.sectorsPerCluster * BYTES_PER_SECTOR)

        log("Пишу boot sector (0) и его копию (6)...")
        writeWithRetry(partition, 0L, ByteBuffer.wrap(bootSector), log)
        writeWithRetry(partition, 6L * BYTES_PER_SECTOR, ByteBuffer.wrap(bootSector), log)

        log("Пишу FSInfo (1) и его копию (7)...")
        writeWithRetry(partition, 1L * BYTES_PER_SECTOR, ByteBuffer.wrap(fsInfo), log)
        writeWithRetry(partition, 7L * BYTES_PER_SECTOR, ByteBuffer.wrap(fsInfo), log)

        // QUICK FORMAT — как и делают реальные приложения (реальный форматтер
        // отработал за ~1 секунду, что возможно ТОЛЬКО если он не зануляет
        // всю FAT-таблицу). Пишем только первый сектор каждой FAT-копии со
        // служебными записями (0,1 — media/EOC, 2 — корневой каталог);
        // остальные тысячи секторов НЕ трогаем — на них ничего не ссылается
        // из (пустого) корневого каталога, так что мусор от предыдущей
        // файловой системы там не мешает. Именно попытка явно занулить всю
        // таблицу (тысячи SCSI-записей подряд) раньше и подвешивала запись.
        log("Пишу первый сектор ${NUM_FATS} копий FAT (quick format, без зануления всей таблицы)...")
        for (fatCopy in 0 until NUM_FATS) {
            val fatStart = (layout.firstFatSector + fatCopy * layout.fatSizeSectors) * BYTES_PER_SECTOR
            writeWithRetry(partition, fatStart, ByteBuffer.wrap(fatFirstSector), log)
        }

        log("Пишу пустой корневой каталог...")
        val rootDirStart = layout.firstDataSector * BYTES_PER_SECTOR
        writeWithRetry(partition, rootDirStart, ByteBuffer.wrap(emptyRootDirCluster), log)

        log("Запись завершена, проверяю результат чтением через Fat32FileSystem...")
    } catch (e: Exception) {
        return FormatResult.Failed("Ошибка записи: ${e.javaClass.simpleName}: ${e.message}")
    }

    return try {
        val fs = me.jahnen.libaums.core.fs.fat32.Fat32FileSystem.read(partition)
        if (fs == null) {
            FormatResult.Failed("Отформатировано, но Fat32FileSystem.read() вернул null — результат нечитаем")
        } else {
            log("Самопроверка OK: volumeLabel='${fs.volumeLabel}' capacity=${fs.capacity} type=${fs.type}")
            FormatResult.Success(layout, fs)
        }
    } catch (e: Exception) {
        FormatResult.Failed("Отформатировано, но самопроверка чтением упала: ${e.javaClass.simpleName}: ${e.message}")
    }
}

/**
 * Диагностика: сырой hex-дамп boot sector'а (0) и FSInfo (1) флешки — ДО
 * попытки собственной записи. Даёт возможность отформатировать флешку
 * проверенным сторонним приложением и свериться байт-в-байт с тем, что
 * реально пишет рабочая реализация, прежде чем доверять своей.
 */
fun dumpRawSectors(partition: Partition, log: (String) -> Unit) {
    for (sectorNum in listOf(0, 1)) {
        val buf = ByteBuffer.allocate(BYTES_PER_SECTOR)
        partition.read(sectorNum.toLong() * BYTES_PER_SECTOR, buf)
        val bytes = buf.array()
        log("Сектор $sectorNum:")
        // по 16 байт на строку (обычный hexdump-формат) — иначе одна гигантская
        // строка обрежется в logcat/UI.
        bytes.toList().chunked(16).forEachIndexed { i, row ->
            val hex = row.joinToString(" ") { "%02X".format(it) }
            log("  +${(i * 16).toString().padStart(3)}: $hex")
        }
    }
}
