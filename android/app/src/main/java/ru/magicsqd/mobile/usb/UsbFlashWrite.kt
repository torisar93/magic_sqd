package ru.magicsqd.mobile.usb

import me.jahnen.libaums.core.fs.FileSystem
import me.jahnen.libaums.core.fs.UsbFile
import java.io.File
import java.nio.ByteBuffer

// Потоково, НЕ через readBytes() целиком в память — usb_files реально
// содержат прошивки под гигабайт (см. Haval M6 "до 04.2026": firmware ISO
// ~700МБ, подтверждено на реальном синке в этой сессии), а heap приложения
// на телефоне такое не выдержит одним ByteArray.
private const val WRITE_CHUNK_SIZE = 4 * 1024 * 1024

/**
 * Пишет один локальный файл на смонтированную флешку по относительному
 * пути (создавая недостающие подпапки) — аналог desktop UsbContext.copy_file
 * (app/usb_context.py), но поверх штатного libaums UsbFile API (то же самое,
 * что уже использует writeAndVerifyTestFile в UsbFlashSpike.kt — ничего
 * самодельного, в отличие от форматирования).
 */
fun writeFileToUsb(fs: FileSystem, localFile: File, destRelativePath: String, log: (String) -> Unit) {
    val segments = destRelativePath.split("/").filter { it.isNotEmpty() }
    require(segments.isNotEmpty()) { "Пустой путь назначения" }

    var dir: UsbFile = fs.rootDirectory
    for (i in 0 until segments.size - 1) {
        val name = segments[i]
        dir = dir.search(name) ?: dir.createDirectory(name)
    }
    val fileName = segments.last()
    dir.search(fileName)?.let { it.delete() } // перезаписываем, если уже есть (повторная установка на ту же флешку)

    val target = dir.createFile(fileName)
    val totalSize = localFile.length()
    val buffer = ByteArray(WRITE_CHUNK_SIZE)
    var offset = 0L
    var lastLoggedMb = -1
    localFile.inputStream().use { input ->
        while (true) {
            val read = input.read(buffer)
            if (read <= 0) break
            target.write(offset, ByteBuffer.wrap(buffer, 0, read))
            offset += read
            val mb = (offset / (1024 * 1024)).toInt()
            if (mb != lastLoggedMb && totalSize > WRITE_CHUNK_SIZE) {
                log("...записано ${mb}MB/${totalSize / 1024 / 1024}MB ($destRelativePath)")
                lastLoggedMb = mb
            }
        }
    }
    target.close()
    log("Записано: $destRelativePath ($offset байт)")
}

/**
 * Исполняет "usb"-этап _wizard_spec.json целиком: файлы модели (usb_files —
 * копируются в КОРЕНЬ флешки, аналог desktop ctx.copy_dir(usb_dir, "") для
 * плоского набора файлов, см. app/car_generator.py:_render_install_py),
 * общий набор из cars/_shared/<usb_shared_folder>/ (со своей структурой
 * подпапок — desktop копирует его рекурсивно, мы тоже) и выбранные техником
 * необязательные APK (usb_apks_dest — подпапка на флешке).
 */
fun writeUsbStage(
    files: List<String>,
    sharedFolderDir: File?,
    selectedApkPaths: List<String>,
    apksDestSubdir: String,
    log: (String) -> Unit,
): StageRunResult {
    val fs = try {
        UsbFlashSession.requireFs()
    } catch (e: Exception) {
        return StageRunResult.Failed(e.message ?: "Флешка не подключена")
    }
    return try {
        for (path in files) {
            val f = File(path)
            if (!f.exists()) return StageRunResult.Failed("Файл не скачан: $path")
            writeFileToUsb(fs, f, f.name, log)
        }
        if (sharedFolderDir != null && sharedFolderDir.exists()) {
            sharedFolderDir.walkTopDown().filter { it.isFile }.forEach { f ->
                val rel = f.relativeTo(sharedFolderDir).path.replace('\\', '/')
                writeFileToUsb(fs, f, rel, log)
            }
        }
        for (path in selectedApkPaths) {
            val f = File(path)
            if (!f.exists()) return StageRunResult.Failed("Файл не скачан: $path")
            val dest = if (apksDestSubdir.isNotBlank()) "$apksDestSubdir/${f.name}" else f.name
            writeFileToUsb(fs, f, dest, log)
        }
        StageRunResult.Success
    } catch (e: Exception) {
        StageRunResult.Failed("${e.javaClass.simpleName}: ${e.message}")
    }
}
