package ru.magicsqd.mobile.usb

import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

// НАЙДЕНО ДИАГНОСТИКОЙ: WRTE больше нашего же заявленного в CNXN maxdata=4096
// (см. performCnxnHandshake — arg1=0x1000) виснет без ответа (проверено: 77 байт
// и 3000 байт проходят мгновенно, 10000 и 16384+ виснут на таймауте). Похоже,
// adbd на этом устройстве трактует наш заявленный maxdata буквально и не читает
// больше, чем мы сами объявили способны принять. 3500 — с запасом под 4096 с
// учётом оверхеда заголовков SEND/DATA/DONE, которые могут попасть в один WRTE.
private const val MAX_CHUNK = 3500

sealed class AdbInstallResult {
    data class Success(val pmOutput: String) : AdbInstallResult()
    data class Failed(val reason: String) : AdbInstallResult()
}

sealed class AdbPushResult {
    object Success : AdbPushResult()
    data class Failed(val reason: String) : AdbPushResult()
}

/**
 * sync-push произвольных байт в remotePath (протокол SYNC: SEND, затем DATA
 * несколько раз, DONE, OKAY-или-FAIL — отдельный под-протокол поверх уже
 * открытого ADB-потока "sync:", НЕ то же самое, что shell). Общая часть
 * installApkOverAdb (там же за ней ещё и `pm install`) и голого #push —
 * вынесена отдельно, т.к. оба нужны интерпретатору _wizard_spec.json
 * (см. InstallEngine.kt). Формат SYNC взят из публичной спецификации AOSP
 * (system/core/adb/SYNC.TXT).
 */
fun syncPushBytes(
    transport: AdbTransport,
    bytes: ByteArray,
    remotePath: String,
    log: (String) -> Unit,
): AdbPushResult {
    // Null-терминатор ОБЯЗАТЕЛЕН (в отличие от shell:, где мы обходились без него) —
    // "sync:" без \0 у некоторых adbd теряет последний байт при разборе как C-строки
    // и перестаёт совпадать с "sync:", falling through в отказ (см. эталонную
    // реализацию adb_shell._open: destination + b'\0').
    val syncService = "sync:".toByteArray(Charsets.UTF_8) + byteArrayOf(0)
    val localId = newLocalStreamId()
    if (!sendMessage(transport, AdbProtocol.A_OPEN, localId, 0, syncService)) {
        return AdbPushResult.Failed("Не удалось отправить OPEN для sync:")
    }
    val (openResp, _) = readMessageForStream(transport, localId, log)
    if (openResp.command != AdbProtocol.A_OKAY) {
        return AdbPushResult.Failed("sync: не открылся (0x${openResp.command.toUInt().toString(16)})")
    }
    val remoteId = openResp.arg0
    log("sync-поток открыт (remoteId=$remoteId), пушу $remotePath (${bytes.size} байт)...")

    val SYNC_WRITE_TIMEOUT_MS = 20000

    fun syncWrite(chunkBytes: ByteArray): Boolean {
        if (!sendMessage(transport, AdbProtocol.A_WRTE, localId, remoteId, chunkBytes, SYNC_WRITE_TIMEOUT_MS)) return false
        val (ack, _) = readMessageForStream(transport, localId, log, SYNC_WRITE_TIMEOUT_MS)
        return ack.command == AdbProtocol.A_OKAY
    }

    // КЛЮЧЕВОЙ ФИКС: диагностика показала, что виснет именно голый SEND,
    // отправленный ОДИН, в ожидании ack, ПЕРЕД первым DATA. Эталонная реализация
    // (adb_shell._filesync_send) так не делает — она БУФЕРИЗУЕТ SEND-заголовок и
    // последующие DATA-чанки ВМЕСТЕ и шлёт одним WRTE, флашя только когда буфер
    // заполнится. Похоже, редко используемый classic-путь на этом adbd не готов
    // к тому, что SEND придёт "голым", без сразу следующих за ним данных.
    val pending = ByteArrayOutputStream()
    fun flushPending(): Boolean {
        if (pending.size() == 0) return true
        val ok = syncWrite(pending.toByteArray())
        pending.reset()
        return ok
    }
    fun appendSyncPacket(chunkBytes: ByteArray): Boolean {
        pending.write(chunkBytes)
        if (pending.size() >= MAX_CHUNK) return flushPending()
        return true
    }

    // --- "SEND" + len32(path,mode) + "path,mode" (mode=33204=0100644, обычный файл rw-r--r--) ---
    val header = "$remotePath,33204"
    val sendReq = ByteBuffer.allocate(8 + header.length).order(ByteOrder.LITTLE_ENDIAN)
    sendReq.put("SEND".toByteArray(Charsets.US_ASCII)).putInt(header.length).put(header.toByteArray(Charsets.US_ASCII))
    if (!appendSyncPacket(sendReq.array())) return AdbPushResult.Failed("Не подтверждён SEND-заголовок")

    var offset = 0
    var lastLoggedMb = -1
    while (offset < bytes.size) {
        val chunkLen = minOf(MAX_CHUNK, bytes.size - offset)
        val chunk = ByteBuffer.allocate(8 + chunkLen).order(ByteOrder.LITTLE_ENDIAN)
        chunk.put("DATA".toByteArray(Charsets.US_ASCII)).putInt(chunkLen).put(bytes, offset, chunkLen)
        if (!appendSyncPacket(chunk.array())) return AdbPushResult.Failed("Обрыв передачи данных на offset=$offset/${bytes.size}")
        offset += chunkLen
        val mb = offset / (1024 * 1024)
        if (mb != lastLoggedMb) { // логируем прогресс раз в мегабайт — с 16КБ-чанками их сотни
            log("...передано ${mb}MB/${bytes.size / 1024 / 1024}MB")
            lastLoggedMb = mb
        }
    }
    log("Данные переданы (${bytes.size} байт), финализирую (DONE) — ЕЩЁ НЕ КОНЕЦ, не отключай провод...")

    val mtime = (System.currentTimeMillis() / 1000).toInt()
    val doneReq = ByteBuffer.allocate(8).order(ByteOrder.LITTLE_ENDIAN)
    doneReq.put("DONE".toByteArray(Charsets.US_ASCII)).putInt(mtime)
    pending.write(doneReq.array())
    if (!flushPending()) return AdbPushResult.Failed("Не подтверждён DONE")
    log("DONE подтверждён устройством.")

    val (statusMsg, statusPayload) = readMessageForStream(transport, localId, log)
    if (statusMsg.command != AdbProtocol.A_WRTE || statusPayload.size < 4) {
        return AdbPushResult.Failed("Не пришёл статус SYNC после DONE (0x${statusMsg.command.toUInt().toString(16)})")
    }
    sendMessage(transport, AdbProtocol.A_OKAY, localId, remoteId, ByteArray(0))
    val statusCode = String(statusPayload, 0, 4, Charsets.US_ASCII)
    sendMessage(transport, AdbProtocol.A_CLSE, localId, remoteId, ByteArray(0))
    return if (statusCode == "OKAY") {
        AdbPushResult.Success
    } else {
        val errMsg = if (statusPayload.size > 8) String(statusPayload, 8, statusPayload.size - 8, Charsets.US_ASCII) else statusCode
        AdbPushResult.Failed("Устройство отклонило push: $errMsg")
    }
}

/**
 * Классический `adb install`: syncPushBytes во временный путь на устройстве,
 * затем `pm install -r <path>` через уже проверенный shell-транспорт, и
 * подчистка временного файла.
 */
fun installApkOverAdb(
    transport: AdbTransport,
    apkBytes: ByteArray,
    remotePath: String = "/data/local/tmp/magicsqd_push_${System.currentTimeMillis()}.apk",
    log: (String) -> Unit,
): AdbInstallResult {
    when (val pushResult = syncPushBytes(transport, apkBytes, remotePath, log)) {
        is AdbPushResult.Failed -> return AdbInstallResult.Failed(pushResult.reason)
        AdbPushResult.Success -> {}
    }
    log("Файл записан на устройство. Запускаю pm install -r $remotePath ...")

    // pm install для крупного APK (dexopt/верификация) может занимать заметно
    // больше 5с по умолчанию — на Redmi Note 7 не уложился, вис ровно на ~5с.
    val installResult = runAdbShellCommand(
        transport, "pm install -r $remotePath", log, timeoutMs = 120000
    )
    val pmOutput = when (installResult) {
        is AdbShellResult.Output -> installResult.text
        is AdbShellResult.Rejected -> return AdbInstallResult.Failed("pm install отклонён: ${installResult.reason}")
        is AdbShellResult.Failed -> return AdbInstallResult.Failed("pm install ошибка: ${installResult.reason}")
    }

    runAdbShellCommand(transport, "rm -f $remotePath", log) // best effort, на результат не влияет

    return if (pmOutput.contains("Success", ignoreCase = true)) {
        AdbInstallResult.Success(pmOutput.trim())
    } else {
        AdbInstallResult.Failed("pm install не вернул Success: ${pmOutput.trim()}")
    }
}

/**
 * Тот же push, что и installApkOverAdb, но `pm install -i
 * com.android.packageinstaller -t -g -r <path>` — подмена "личности"
 * установщика под системный Package Installer, портировано 1:1 из
 * собственного deploy-скрипта MonGuard для платформы Geely OneOS/NewEra
 * (desktop-версия: app/install_context.py: install_apk_pm_spoofed). Часть
 * сборок на этой платформе иначе не ставит APK тихо через голый adb —
 * либо блокирует, либо всплывает системный диалог подтверждения на самой
 * магнитоле. Если флаг -i отклонён (Unknown option/INVALID_INSTALLER —
 * старые сборки pm его не знают), откатывается на обычный
 * "pm install -t -g -r" без подмены.
 */
fun installApkSpoofedOverAdb(
    transport: AdbTransport,
    apkBytes: ByteArray,
    remotePath: String = "/data/local/tmp/magicsqd_push_${System.currentTimeMillis()}.apk",
    log: (String) -> Unit,
): AdbInstallResult {
    when (val pushResult = syncPushBytes(transport, apkBytes, remotePath, log)) {
        is AdbPushResult.Failed -> return AdbInstallResult.Failed(pushResult.reason)
        AdbPushResult.Success -> {}
    }
    log("Файл записан на устройство. Запускаю pm install -i (подмена установщика) $remotePath ...")

    var installResult = runAdbShellCommand(
        transport, "pm install -i com.android.packageinstaller -t -g -r $remotePath", log, timeoutMs = 120000
    )
    var pmOutput = when (installResult) {
        is AdbShellResult.Output -> installResult.text
        is AdbShellResult.Rejected -> return AdbInstallResult.Failed("pm install отклонён: ${installResult.reason}")
        is AdbShellResult.Failed -> return AdbInstallResult.Failed("pm install ошибка: ${installResult.reason}")
    }
    val lower = pmOutput.lowercase()
    if ("unknown option" in lower || "invalid_installer" in lower || "invalid installer" in lower) {
        log("Флаг -i отклонён этой прошивкой — пробую pm install без подмены установщика")
        installResult = runAdbShellCommand(transport, "pm install -t -g -r $remotePath", log, timeoutMs = 120000)
        pmOutput = when (installResult) {
            is AdbShellResult.Output -> installResult.text
            is AdbShellResult.Rejected -> return AdbInstallResult.Failed("pm install отклонён: ${installResult.reason}")
            is AdbShellResult.Failed -> return AdbInstallResult.Failed("pm install ошибка: ${installResult.reason}")
        }
    }

    runAdbShellCommand(transport, "rm -f $remotePath", log) // best effort, на результат не влияет

    return if (pmOutput.contains("Success", ignoreCase = true)) {
        AdbInstallResult.Success(pmOutput.trim())
    } else {
        AdbInstallResult.Failed("pm install не вернул Success: ${pmOutput.trim()}")
    }
}

/**
 * Тот же push, что и installApkOverAdb, но установка через
 * "cat <файл> | pm install -S <размер>" вместо обычного "pm install -r
 * <файл>" — на части adbd/pm обычный путь не срабатывает, а потоковый
 * работает (порт desktop app/install_context.py: install_apk_stream).
 */
fun installApkStreamOverAdb(
    transport: AdbTransport,
    apkBytes: ByteArray,
    remotePath: String = "/data/local/tmp/magicsqd_push_${System.currentTimeMillis()}.apk",
    log: (String) -> Unit,
): AdbInstallResult {
    when (val pushResult = syncPushBytes(transport, apkBytes, remotePath, log)) {
        is AdbPushResult.Failed -> return AdbInstallResult.Failed(pushResult.reason)
        AdbPushResult.Success -> {}
    }
    log("Файл записан на устройство. Запускаю pm install -S ${apkBytes.size} (поток) ...")

    val installResult = runAdbShellCommand(
        transport, "cat $remotePath | pm install -S ${apkBytes.size}", log, timeoutMs = 120000
    )
    val pmOutput = when (installResult) {
        is AdbShellResult.Output -> installResult.text
        is AdbShellResult.Rejected -> return AdbInstallResult.Failed("pm install -S отклонён: ${installResult.reason}")
        is AdbShellResult.Failed -> return AdbInstallResult.Failed("pm install -S ошибка: ${installResult.reason}")
    }

    runAdbShellCommand(transport, "rm -f $remotePath", log)

    return if (pmOutput.contains("Success", ignoreCase = true)) {
        AdbInstallResult.Success(pmOutput.trim())
    } else {
        AdbInstallResult.Failed("pm install -S не вернул Success: ${pmOutput.trim()}")
    }
}

private fun installedPackages(transport: AdbTransport, log: (String) -> Unit): Set<String> {
    val output = when (val r = runAdbShellCommand(transport, "pm list packages", log)) {
        is AdbShellResult.Output -> r.text
        else -> ""
    }
    return output.lineSequence()
        .map { it.trim() }
        .filter { it.startsWith("package:") }
        .map { it.removePrefix("package:").trim() }
        .toSet()
}

/**
 * Установка через helper-APK cars/_shared/chery_localinstall.apk — на
 * платформе Chery DesaySV (Jaecoo/Exeed/Chery/Tenet — общий поставщик ГУ)
 * прошивка блокирует обычный "pm install", единственный рабочий способ —
 * запустить helper через app_process от имени shell: он ставит целевой
 * APK через Android API PackageInstaller.Session (см.
 * https://github.com/EvilBorsch/chery-adb-app-install/blob/main/instuction.md,
 * а также desktop-порт: app/install_context.py:install_apk_localinstall).
 * helperBytes — содержимое cars/_shared/chery_localinstall.apk, уже
 * скачанного обычной синхронизацией (см. InstallEngine.kt).
 *
 * APK заранее не подписан известным именем пакета, поэтому оно
 * определяется сравнением списка установленных пакетов до/после — тот же
 * запасной способ, что и на desktop.
 */
fun installApkViaLocalinstall(
    transport: AdbTransport,
    apkBytes: ByteArray,
    helperBytes: ByteArray,
    log: (String) -> Unit,
): AdbInstallResult {
    val remoteApk = "/data/local/tmp/desaysv-install-target.apk"
    val remoteHelper = "/data/local/tmp/desaysv-localinstall.apk"

    val before = installedPackages(transport, log)

    when (val r = syncPushBytes(transport, apkBytes, remoteApk, log)) {
        is AdbPushResult.Failed -> return AdbInstallResult.Failed(r.reason)
        AdbPushResult.Success -> {}
    }
    runAdbShellCommand(transport, "chmod 644 $remoteApk", log)
    when (val r = syncPushBytes(transport, helperBytes, remoteHelper, log)) {
        is AdbPushResult.Failed -> return AdbInstallResult.Failed(r.reason)
        AdbPushResult.Success -> {}
    }
    runAdbShellCommand(transport, "chmod 644 $remoteHelper", log)

    log("Устанавливаю через localinstall.apk (app_process, Chery DesaySV)...")
    val installResult = runAdbShellCommand(
        transport,
        "CLASSPATH=$remoteHelper app_process /system/bin LocalInstall $remoteApk",
        log,
        timeoutMs = 120000,
    )
    Thread.sleep(2000) // helper коммитит сессию установки асинхронно — даём системе время дописать пакет

    val after = installedPackages(transport, log)
    runAdbShellCommand(transport, "rm -f $remoteApk $remoteHelper", log)

    val newPackages = after - before
    if (newPackages.size != 1) {
        val text = when (installResult) {
            is AdbShellResult.Output -> installResult.text
            is AdbShellResult.Rejected -> installResult.reason
            is AdbShellResult.Failed -> installResult.reason
        }
        val candidates = if (newPackages.isEmpty()) "нет" else newPackages.joinToString(", ")
        return AdbInstallResult.Failed(
            "localinstall не подтвердил успех (новых пакетов: $candidates): ${text.trim()}"
        )
    }
    val pkg = newPackages.first()
    AdbPermissions.grantAllPermissions(pkg, log)
    runAdbShellCommand(transport, "am force-stop $pkg", log)
    runAdbShellCommand(transport, "monkey -p $pkg -c android.intent.category.LAUNCHER 1", log)
    return AdbInstallResult.Success("localinstall: $pkg")
}

/**
 * Тот же приём (app_process + helper через PackageInstaller.Session), что и
 * installApkViaLocalinstall выше, но для платформы Geely OneOS (Atlas/
 * CityRay/Preface) — helper взят буквально из стороннего бесплатного
 * установщика (пользователь подтвердил, что использовать его можно), не
 * переписан с нуля: захвачен и разобран через devtools/fake_adb_device.py
 * (перехвачена точная команда запуска на трёх разных моделях — один и тот
 * же .dex, один и тот же набор флагов), десктоп-версия: app/
 * install_context.py: install_apk_dex_shell. helperBytes — содержимое
 * cars/_shared/dex_shell_helper.dex. ENTRY_CLASS ниже — это РЕАЛЬНОЕ имя
 * входной точки, скомпилированное внутри самого .dex (как есть, не наше) —
 * без него app_process не найдёт нужный класс. --flags 0x116 — это ровно
 * INSTALL_REPLACE_EXISTING(0x2) | INSTALL_ALLOW_TEST(0x4) |
 * INSTALL_INTERNAL(0x10) | INSTALL_GRANT_RUNTIME_PERMISSIONS(0x100), как и
 * в оригинале.
 */
private const val DEX_SHELL_ENTRY_CLASS = "MonjiShellInstaller"

fun installApkViaDexShell(
    transport: AdbTransport,
    apkBytes: ByteArray,
    apkName: String,
    helperBytes: ByteArray,
    log: (String) -> Unit,
): AdbInstallResult {
    val remoteApk = "/data/local/tmp/$apkName"
    val remoteHelper = "/data/local/tmp/dex_shell_helper.dex"

    val before = installedPackages(transport, log)

    when (val r = syncPushBytes(transport, apkBytes, remoteApk, log)) {
        is AdbPushResult.Failed -> return AdbInstallResult.Failed(r.reason)
        AdbPushResult.Success -> {}
    }
    runAdbShellCommand(transport, "chmod 644 $remoteApk", log)
    when (val r = syncPushBytes(transport, helperBytes, remoteHelper, log)) {
        is AdbPushResult.Failed -> return AdbInstallResult.Failed(r.reason)
        AdbPushResult.Success -> {}
    }
    runAdbShellCommand(transport, "chmod 644 $remoteHelper", log)

    log("Устанавливаю через dex-хелпер (app_process, Geely OneOS)...")
    val installResult = runAdbShellCommand(
        transport,
        "CLASSPATH=$remoteHelper app_process /data/local/tmp $DEX_SHELL_ENTRY_CLASS $remoteApk --flags 0x116",
        log,
        timeoutMs = 120000,
    )
    Thread.sleep(2000) // helper коммитит сессию установки асинхронно — даём системе время дописать пакет

    val after = installedPackages(transport, log)
    runAdbShellCommand(transport, "rm -f $remoteApk $remoteHelper", log)

    val newPackages = after - before
    if (newPackages.size != 1) {
        val text = when (installResult) {
            is AdbShellResult.Output -> installResult.text
            is AdbShellResult.Rejected -> installResult.reason
            is AdbShellResult.Failed -> installResult.reason
        }
        val candidates = if (newPackages.isEmpty()) "нет" else newPackages.joinToString(", ")
        return AdbInstallResult.Failed(
            "dex-хелпер не подтвердил успех (новых пакетов: $candidates): ${text.trim()}"
        )
    }
    val pkg = newPackages.first()
    AdbPermissions.grantAllPermissions(pkg, log)
    runAdbShellCommand(transport, "am force-stop $pkg", log)
    runAdbShellCommand(transport, "monkey -p $pkg -c android.intent.category.LAUNCHER 1", log)
    return AdbInstallResult.Success("dex_shell_install: $pkg")
}
