package ru.magicsqd.mobile.usb

import me.jahnen.libaums.core.fs.FileSystem
import me.jahnen.libaums.core.fs.UsbFile
import java.io.File
import java.io.IOException
import java.nio.ByteBuffer

// Потоково, НЕ через readBytes() целиком в память — usb_files реально
// содержат прошивки под гигабайт (см. Haval M6 "до 04.2026": firmware ISO
// ~700МБ, подтверждено на реальном синке в этой сессии), а heap приложения
// на телефоне такое не выдержит одним ByteArray.
private const val WRITE_CHUNK_SIZE = 4 * 1024 * 1024

// libaums сам ретраит SCSI-команды внутри себя и, только исчерпав СВОИ
// попытки, бросает IOException "MAX_RECOVERY_ATTEMPTS Exceeded ... please
// reattach device and try again" — реальный случай (см. install_logs на
// сервере, 2026-09): 4 таких сбоя подряд на одном большом файле
// (com.google.android.webview.apk, ~211МБ), включая один случай, где файл
// ДОПИСАЛСЯ ДО КОНЦА и всё равно сразу поймал ту же ошибку на следующей
// операции — похоже на переходный сбой шины/таймаута, а не на жёсткую
// несовместимость с конкретным файлом. Раньше при этом весь usb-этап падал
// сразу и техник должен был форматировать флешку и начинать всё заново
// (минуты на 200+МБ файл) — теперь перезаписываем файл с нуля до
// WRITE_RETRY_ATTEMPTS раз, без физического переподключения устройства
// (сама рекомендация "reattach" — это то, что ПОМОГАЕТ вручную, а не то,
// что строго обязательно: повторная попытка с нуля даёт шине шанс
// восстановиться без ручного вмешательства).
// ДОБАВЛЕНО ПОЗЖЕ: реальный случай на клиенте (install_logs, v1.0.0) —
// файл 211МБ упал с этой же ошибкой, и весь usb-этап провалился даже
// после нескольких попыток БЕЗ паузы между ними. UsbFlashFormat.kt (см.
// writeWithRetry там же) для точно такой же ошибки уже делает
// Thread.sleep(300) между попытками — "дать устройству прийти в себя" — а
// здесь этой паузы не было вовсе, упущение при переносе того же приёма в
// отдельный файл. Добавили ту же паузу и подняли число попыток — на
// большом файле шанс словить временный сбой шины выше, чем на команде
// форматирования, которая работает с одним сектором.
private const val WRITE_RETRY_ATTEMPTS = 5
private const val WRITE_RETRY_DELAY_MS = 300L

// ДОБАВЛЕНО ПОЗЖЕ (install_logs на сервере, 2026-09): реальные клиентские
// падения — "IllegalArgumentException: newLimit > capacity: (65536 >
// 32768)" и т.п. — это баг самой libaums 0.10.0, а не наш: ClusterChain.
// write() (me/jahnen/libaums/core/fs/fat32/ClusterChain.kt, метод write())
// объединяет до 4 подряд идущих кластеров в одну запись ради скорости
// (source.limit(source.position() + clusterSize * maxConsecutiveClusters)),
// но не проверяет, что переданный ему буфер реально содержит столько
// байт — если ЧУЖОЙ вызов той же ClusterChain (например, внутренняя
// запись самой директории при createFile()/delete() — см. FatDirectory,
// буфер там размером ровно в один кластер) попадает на директорию, чьи
// собственные кластеры выделились подряд (обычное дело на свежесделанной
// FAT32-флешке), — вылетает ровно это исключение. В upstream не
// исправлено (последний тег repo — 0.10.0, https://github.com/magnusja/
// libaums/blob/v0.10.0/libaums/src/main/java/me/jahnen/libaums/core/fs/
// fat32/ClusterChain.kt#L230). Патчить/форкать бинарную зависимость —
// намного больше риска, чем просто повторить попытку: на новом
// createFile()/delete() ниже директория почти наверняка переиспользует
// кластеры по-другому и обойдёт то же самое совпадение. IOException
// ловится тут же ниже отдельно — этот класс исключений НЕ является его
// подклассом, поэтому раньше падал с первой же попытки, вообще без ретрая.

// Разбор логов 2026-09-23 (#577/#578): если все попытки не помогли, техник видел
// голый английский текст libaums («newLimit > capacity: (1037 > 1024)», «MAX_
// RECOVERY_ATTEMPTS Exceeded … please reattach device») и не знал, что делать.
// Баг ClusterChain (см. выше) зависит от раскладки кластеров на конкретной
// флешке — на свежеотформатированной пустой FAT32 запись проходит.
private const val LIBAUMS_CLUSTER_BUG = "newLimit > capacity"
private const val LIBAUMS_RECOVERY_FAILED = "MAX_RECOVERY_ATTEMPTS"
private const val FORMAT_HINT = "отформатируйте флешку («Параметры флешки» → «Форматировать флешку») и запишите файлы заново"

/** Все попытки записи файла не удались — текст уже для техника (см. usbWriteFailureMessage). */
class UsbWriteFailedException(message: String, cause: Exception) : IOException(message, cause)

/** Что делать технику после WRITE_RETRY_ATTEMPTS неудачных попыток; исходная ошибка
 * libaums — в скобках, для разбора логов. errors — ошибки всех попыток по порядку. */
internal fun usbWriteFailureMessage(fileName: String, errors: List<Exception>): String {
    val messages = errors.map { it.message.orEmpty() }
    val last = messages.lastOrNull { it.isNotBlank() } ?: "неизвестная ошибка"
    return when {
        messages.any { LIBAUMS_CLUSTER_BUG in it } ->
            "Не удалось записать $fileName: сбой файловой системы флешки — $FORMAT_HINT. ($last)"
        messages.any { LIBAUMS_RECOVERY_FAILED in it } ->
            "Не удалось записать $fileName: флешка перестала отвечать. Выньте и снова вставьте флешку " +
                "(и OTG-переходник) и повторите запись; если не помогло — $FORMAT_HINT или возьмите другую флешку. ($last)"
        else -> "Не удалось записать $fileName: $last"
    }
}

/** Файл в очереди записи — тот же {name, path, size}, что и desktop
 * _scan_usb_items (app/web/api/usb_api.py), path — ЛОКАЛЬНЫЙ (исходный) путь,
 * не путь назначения на флешке: именно им помечены строки очереди в
 * progress08.js (LabUI.busy/LabUI.progress ищут по item.path/e.path), и им
 * же должны совпадать события onProgress ниже. */
data class UsbFileItem(val name: String, val path: String, val size: Long)

/**
 * Список файлов, которые ЦЕЛИКОМ будут записаны за один запуск "usb"-этапа —
 * та же структура и порядок обхода, что и сама запись в writeUsbStage ниже
 * (files -> общая папка _shared -> выбранные APK), но посчитанная ЗАРАНЕЕ,
 * до старта записи (аналог desktop _scan_usb_items) — нужно показать
 * технику очередь и общий счётчик файлов в кольце прогресса ДО первого
 * события (см. WebBridge.kt: usbListItems).
 */
fun scanUsbStageItems(files: List<String>, sharedFolderDir: File?, selectedApkPaths: List<String>): List<UsbFileItem> {
    val items = mutableListOf<UsbFileItem>()
    for (path in files) {
        val f = File(path)
        if (f.isFile) items.add(UsbFileItem(f.name, f.path, f.length()))
    }
    if (sharedFolderDir != null && sharedFolderDir.exists()) {
        sharedFolderDir.walkTopDown().filter { it.isFile }.forEach { f ->
            items.add(UsbFileItem(f.name, f.path, f.length()))
        }
    }
    for (path in selectedApkPaths) {
        val f = File(path)
        if (f.isFile) items.add(UsbFileItem(f.name, f.path, f.length()))
    }
    return items
}

/**
 * Пишет один локальный файл на смонтированную флешку по относительному
 * пути (создавая недостающие подпапки) — аналог desktop UsbContext.copy_file
 * (app/usb_context.py), но поверх штатного libaums UsbFile API (то же самое,
 * что уже использует writeAndVerifyTestFile в UsbFlashSpike.kt — ничего
 * самодельного, в отличие от форматирования).
 *
 * onProgress(path, bytesDone, bytesTotal, filesDone, filesTotal, state) —
 * тот же набор параметров, что и у desktop UsbContext._on_progress: path —
 * ЛОКАЛЬНЫЙ путь (localFile), не destRelativePath; state="running" на
 * каждый чанк текущего файла (offset уже посчитан ниже для лога), "done"
 * один раз по завершении файла целиком. filesDone/filesTotal — счётчик
 * очереди целиком, общий на весь запуск writeUsbStage (см. её докстринг),
 * поэтому передаются СНАРУЖИ, а не считаются здесь.
 */
fun writeFileToUsb(
    fs: FileSystem, localFile: File, destRelativePath: String, log: (String) -> Unit,
    filesDone: Int = 0, filesTotal: Int = 0,
    onProgress: (path: String, bytesDone: Long, bytesTotal: Long, filesDone: Int, filesTotal: Int, state: String) -> Unit =
        { _, _, _, _, _, _ -> },
) {
    val segments = destRelativePath.split("/").filter { it.isNotEmpty() }
    require(segments.isNotEmpty()) { "Пустой путь назначения" }

    var dir: UsbFile = fs.rootDirectory
    for (i in 0 until segments.size - 1) {
        val name = segments[i]
        dir = dir.search(name) ?: dir.createDirectory(name)
    }
    val fileName = segments.last()

    val errors = mutableListOf<Exception>()
    for (attempt in 1..WRITE_RETRY_ATTEMPTS) {
        try {
            // Свежий createFile на каждой попытке — предыдущая могла оставить
            // на флешке частично записанный (битый) файл того же имени.
            // РАНЬШЕ эти два вызова стояли ВНЕ try (см. ниже) — если шина ещё
            // не восстановилась после провала предыдущей попытки, delete()/
            // createFile() сами бросали то же MAX_RECOVERY_ATTEMPTS
            // Exceeded, но уже НЕ через catch блока ниже, а прямо наружу из
            // функции — весь retry молча обрывался после попытки 1, хотя лог
            // перед этим обещал "(попытка 1/5)" (реальный случай, install_logs
            // #501 — 211МБ файл, ни одной строки "попытка 2/5" не появилось).
            dir.search(fileName)?.let { it.delete() }
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
                    onProgress(localFile.path, offset, totalSize, filesDone, filesTotal, "running")
                    val mb = (offset / (1024 * 1024)).toInt()
                    if (mb != lastLoggedMb && totalSize > WRITE_CHUNK_SIZE) {
                        log("...записано ${mb}MB/${totalSize / 1024 / 1024}MB ($destRelativePath)")
                        lastLoggedMb = mb
                    }
                }
            }
            target.close()
            onProgress(localFile.path, offset, totalSize, filesDone + 1, filesTotal, "done")
            log("Записано: $destRelativePath ($offset байт)")
            return
        } catch (e: Exception) {
            if (e !is IOException && e !is IllegalArgumentException) throw e
            errors.add(e)
            if (attempt < WRITE_RETRY_ATTEMPTS) {
                log("Сбой записи $destRelativePath (попытка $attempt/$WRITE_RETRY_ATTEMPTS): " +
                    "${e.message}. Повторяю...")
                Thread.sleep(WRITE_RETRY_DELAY_MS)
            }
        }
    }
    throw UsbWriteFailedException(usbWriteFailureMessage(fileName, errors), errors.last())
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
    onProgress: (path: String, bytesDone: Long, bytesTotal: Long, filesDone: Int, filesTotal: Int, state: String) -> Unit =
        { _, _, _, _, _, _ -> },
): StageRunResult {
    val fs = try {
        UsbFlashSession.requireFs()
    } catch (e: Exception) {
        return StageRunResult.Failed(e.message ?: "Флешка не подключена")
    }
    // Тот же список/порядок, что и сама запись ниже — только чтобы узнать
    // filesTotal к этому моменту (аналог desktop _worker: пересчитывает
    // _scan_usb_items ещё раз прямо перед созданием UsbContext).
    val filesTotal = scanUsbStageItems(files, sharedFolderDir, selectedApkPaths).size
    var filesDone = 0
    return try {
        for (path in files) {
            val f = File(path)
            if (!f.exists()) return StageRunResult.Failed("Файл не скачан: $path")
            writeFileToUsb(fs, f, f.name, log, filesDone, filesTotal, onProgress)
            filesDone++
        }
        if (sharedFolderDir != null && sharedFolderDir.exists()) {
            sharedFolderDir.walkTopDown().filter { it.isFile }.forEach { f ->
                val rel = f.relativeTo(sharedFolderDir).path.replace('\\', '/')
                writeFileToUsb(fs, f, rel, log, filesDone, filesTotal, onProgress)
                filesDone++
            }
        }
        for (path in selectedApkPaths) {
            val f = File(path)
            if (!f.exists()) return StageRunResult.Failed("Файл не скачан: $path")
            val dest = if (apksDestSubdir.isNotBlank()) "$apksDestSubdir/${f.name}" else f.name
            writeFileToUsb(fs, f, dest, log, filesDone, filesTotal, onProgress)
            filesDone++
        }
        StageRunResult.Success
    } catch (e: UsbWriteFailedException) {
        StageRunResult.Failed(e.message.orEmpty())
    } catch (e: Exception) {
        StageRunResult.Failed("${e.javaClass.simpleName}: ${e.message}")
    }
}
