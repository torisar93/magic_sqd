package ru.magicsqd.mobile.usb

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build
import me.jahnen.libaums.core.UsbMassStorageDevice

/**
 * Спайк для проверки чтения флешки через USB host без root (см. план: запись
 * APK на флешку для установки в магнитолу через её собственный USB-порт).
 * Библиотека libaums сама говорит с флешкой по SCSI/bulk-only transport, минуя
 * штатный Android-маунт — это даёт сырой доступ к разделу (и, на следующем
 * шаге, форматирование), а не только то, что видно через SAF.
 *
 * Область этого спайка: получить разрешение и прочитать метаданные раздела
 * (файловая система, размер, свободное место). Запись/форматирование —
 * следующий шаг после того, как чтение подтверждено на реальной флешке.
 */
private const val ACTION_USB_PERMISSION = "ru.magicsqd.mobile.USB_PERMISSION"

fun listMassStorageDevices(context: Context): List<UsbMassStorageDevice> {
    return UsbMassStorageDevice.getMassStorageDevices(context).toList()
}

/**
 * Запрашивает разрешение пользователя (системный диалог) и вызывает [onResult]
 * после ответа. Если разрешение уже есть — диалог не показывается, callback
 * вызывается сразу.
 */
fun requestUsbPermission(context: Context, device: UsbDevice, onResult: (granted: Boolean) -> Unit) {
    val usbManager = context.getSystemService(Context.USB_SERVICE) as UsbManager
    if (usbManager.hasPermission(device)) {
        onResult(true)
        return
    }

    val receiver = object : BroadcastReceiver() {
        override fun onReceive(ctx: Context, intent: Intent) {
            if (intent.action != ACTION_USB_PERMISSION) return
            ctx.unregisterReceiver(this)
            val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
            onResult(granted)
        }
    }

    val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
        PendingIntent.FLAG_MUTABLE
    } else {
        0
    }
    // На Android 14+ FLAG_MUTABLE запрещён для implicit-интентов (только action,
    // без явного получателя) — setPackage делает интент "достаточно явным".
    val intent = Intent(ACTION_USB_PERMISSION).setPackage(context.packageName)
    val permissionIntent = PendingIntent.getBroadcast(context, 0, intent, flags)

    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
        context.registerReceiver(receiver, IntentFilter(ACTION_USB_PERMISSION), Context.RECEIVER_NOT_EXPORTED)
    } else {
        @Suppress("UnspecifiedRegisterReceiverFlag")
        context.registerReceiver(receiver, IntentFilter(ACTION_USB_PERMISSION))
    }
    usbManager.requestPermission(device, permissionIntent)
}

data class FlashInfo(
    val volumeLabel: String,
    val fsType: String,
    val capacityBytes: Long,
    val freeBytes: Long,
)

/** Инициализирует устройство и читает метаданные первого раздела. */
fun readFlashInfo(device: UsbMassStorageDevice): FlashInfo {
    device.init()
    val partition = device.partitions[0]
    val fs = partition.fileSystem
    return FlashInfo(
        volumeLabel = fs.volumeLabel ?: "(без метки)",
        fsType = fs.type.toString(),
        capacityBytes = fs.capacity,
        freeBytes = fs.freeSpace,
    )
}

/**
 * Пишет тестовый файл через штатный UsbFile API libaums (createFile/write —
 * в отличие от форматирования, тут ничего самодельного, библиотека это
 * умеет сама) и сразу читает обратно, чтобы проверить, что содержимое
 * совпадает. Диагностическая цель: проверить, работает ли запись файлов
 * СРАЗУ ПОСЛЕ форматирования в ТОЙ ЖЕ сессии (без переподключения флешки).
 */
fun writeAndVerifyTestFile(
    fs: me.jahnen.libaums.core.fs.FileSystem,
    fileName: String,
    content: ByteArray,
): Boolean {
    val root = fs.rootDirectory
    val file = root.createFile(fileName)
    file.write(0L, java.nio.ByteBuffer.wrap(content))
    file.close()

    val readBack = root.search(fileName) ?: return false
    val buf = java.nio.ByteBuffer.allocate(content.size)
    readBack.read(0L, buf)
    readBack.close()
    return buf.array().contentEquals(content)
}
