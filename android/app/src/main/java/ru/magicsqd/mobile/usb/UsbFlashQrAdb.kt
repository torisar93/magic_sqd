package ru.magicsqd.mobile.usb

import me.jahnen.libaums.core.fs.FileSystem
import java.io.File
import java.nio.ByteBuffer

/** Имя файла-триггера — та же константа, что и в app/qr_adb_password.py и
 * app/web/api/qr_adb_api.py на desktop (см. их докстринги за смыслом всей
 * процедуры: магнитола платформы Geely без Wi-Fi при обнаружении этого
 * файла на флешке выгружает на неё диагностический дамп с данными для
 * расчёта пароля ADB). */
private const val FLAG_FILENAME = "svlog.flag"

/** Пишет svlog.flag в корень флешки — тонкая обёртка над writeFileToUsb
 * (см. UsbFlashWrite.kt), локальный файл уже должен быть на диске (общий
 * cars/_shared/svlog.flag, синхронизируется тем же путём, что и на
 * desktop). */
fun writeQrAdbFlag(fs: FileSystem, localFlagFile: File, log: (String) -> Unit): Result<Unit> {
    return try {
        writeFileToUsb(fs, localFlagFile, FLAG_FILENAME, log)
        Result.success(Unit)
    } catch (e: Exception) {
        Result.failure(e)
    }
}

/** Находит самую свежую по имени папку logs_* в корне флешки (имя содержит
 * таймстемп — обычная сортировка строк даёт хронологический порядок), внутри
 * неё — bugreport-*.zip, и читает его целиком в память (в отличие от
 * writeFileToUsb — сами баг-репорты этой платформы единицы МБ, а не
 * прошивки на сотни МБ/ГБ, поточность не нужна). Дальше zip передаётся в
 * Python (см. WebBridge.kt: qrAdbGetPassword, android/.../python/
 * qr_adb_password.py) — тот же расчёт (HKDF), что и на desktop
 * (app/qr_adb_password.py), просто по уже прочитанным байтам вместо пути
 * на диске (у Android нет прямого файлового пути к содержимому флешки,
 * смонтированной через libaums, только UsbFile API). */
fun readQrAdbBugreportZip(fs: FileSystem): Result<ByteArray> {
    return try {
        val root = fs.rootDirectory
        val logsFolder = root.listFiles()
            .filter { it.isDirectory && it.name.startsWith("logs_") }
            .maxByOrNull { it.name }
            ?: return Result.failure(IllegalStateException(
                "На флешке не найдена папка logs_*. Проверьте: файл svlog.flag был на флешке ДО " +
                "того, как её вставили в магнитолу, и на экране появилась надпись «QNX OK»."
            ))
        val zipFile = logsFolder.listFiles()
            .filter { !it.isDirectory && it.name.startsWith("bugreport-") && it.name.endsWith(".zip") }
            .maxByOrNull { it.name }
            ?: return Result.failure(IllegalStateException(
                "В папке ${logsFolder.name} не найден файл bugreport-*.zip"
            ))
        val size = zipFile.length
        if (size <= 0 || size > 200L * 1024 * 1024) {
            return Result.failure(IllegalStateException("Подозрительный размер файла ${zipFile.name}: $size байт"))
        }
        val buffer = ByteBuffer.allocate(size.toInt())
        zipFile.read(0L, buffer)
        zipFile.close()
        Result.success(buffer.array())
    } catch (e: Exception) {
        Result.failure(e)
    }
}
