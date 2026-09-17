package ru.magicsqd.mobile.usb

import android.content.Context
import android.hardware.usb.UsbDevice
import me.jahnen.libaums.core.UsbMassStorageDevice
import me.jahnen.libaums.core.fs.FileSystem
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Держит ОДНУ смонтированную флешку живой между шагами "usb"-этапа мастера
 * установки — тот же принцип, что и AdbSession для ADB-соединения. НЕ
 * форматирует флешку сама по себе при подключении (см. connectBlocking) —
 * desktop-версия тоже не форматирует автоматически на каждом "usb"-этапе
 * (ctx.copy_dir просто пишет поверх того, что уже смонтировано); явное
 * форматирование — отдельное действие (см. format), технику решать самому.
 */
object UsbFlashSession {
    @Volatile private var device: UsbMassStorageDevice? = null
    @Volatile private var fs: FileSystem? = null

    val isMounted: Boolean get() = fs != null

    fun disconnect() {
        device = null
        fs = null
    }

    fun connectBlocking(context: Context, log: (String) -> Unit): Result<FileSystem> {
        disconnect()
        val target = listMassStorageDevices(context).firstOrNull()
            ?: return Result.failure(IllegalStateException(
                "Флешка не найдена (нет USB mass storage устройств) — проверь OTG-подключение."))
        if (!requestUsbPermissionBlocking(context, target.usbDevice)) {
            return Result.failure(IllegalStateException("Пользователь отклонил разрешение на доступ к флешке"))
        }
        return try {
            target.init()
            // Сразу после подключения раздел иногда ещё не успел
            // определиться (см. install_logs на сервере, 2026-09: "Index 0
            // out of bounds for length 0" — сырое исключение от голого
            // partitions[0] на пустом списке) — JS-сторона сама ретраит
            // usbConnect несколько раз подряд, так что это не фатально
            // (в логах видно, что через несколько попыток монтирование
            // всё-таки удаётся), но лучше человеческое сообщение, чем
            // голый IndexOutOfBoundsException на каждую неудачную попытку.
            val partitions = target.partitions
            if (partitions.isEmpty()) {
                return Result.failure(IllegalStateException(
                    "Флешка подключена, но разделы ещё не определились — пробую снова..."))
            }
            val partitionFs = partitions[0].fileSystem
            device = target
            fs = partitionFs
            log("Флешка смонтирована: ${partitionFs.volumeLabel ?: "(без метки)"}, ${partitionFs.type}")
            Result.success(partitionFs)
        } catch (e: Exception) {
            Result.failure(e)
        }
    }

    /** Полное форматирование в FAT32 (см. UsbFlashFormat.kt) — ЗАТИРАЕТ всё
     * содержимое. Флешка должна быть уже подключена через connectBlocking. */
    fun format(capacityBytes: Long, volumeLabel: String, log: (String) -> Unit): Result<FileSystem> {
        val dev = device ?: return Result.failure(IllegalStateException("Флешка не подключена"))
        return when (val result = formatFat32(dev.partitions[0], capacityBytes, volumeLabel, log)) {
            is FormatResult.Success -> {
                fs = result.fs
                Result.success(result.fs)
            }
            is FormatResult.Failed -> Result.failure(IllegalStateException(result.reason))
        }
    }

    fun requireFs(): FileSystem = fs ?: error("Флешка не подключена — сначала нужно её смонтировать")

    fun capacityBytes(): Long = requireFs().capacity

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
