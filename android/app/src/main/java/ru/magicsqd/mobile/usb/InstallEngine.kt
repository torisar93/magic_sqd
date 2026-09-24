package ru.magicsqd.mobile.usb

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.SynchronousQueue
import java.util.concurrent.TimeUnit

// Сколько ждём возвращения магнитолы после обрыва связи посреди установки.
private const val LINK_RECOVERY_TIMEOUT_MS = 45_000L

sealed class StageRunResult {
    object Success : StageRunResult()
    data class Failed(val reason: String) : StageRunResult()
}

/**
 * Мост для команды "#ask" (см. wizard_spec.py:parse_adb_line) — исполнение
 * команд идёт на фоновом потоке (см. InstallEngine.runAdbCommands) и должно
 * ДОЖДАТЬСЯ ответа техника, который вводится через WebView-диалог (JS) и
 * приходит обратно через WebBridge.adb_ask_input_response на другом потоке.
 * SynchronousQueue — самый простой способ передать один ответ между двумя
 * потоками без гонок.
 */
object AskInputBroker {
    private val pending = ConcurrentHashMap<String, SynchronousQueue<String>>()

    fun register(requestId: String): SynchronousQueue<String> {
        val q = SynchronousQueue<String>()
        pending[requestId] = q
        return q
    }

    fun resolve(requestId: String, value: String) {
        pending[requestId]?.offer(value)
    }

    fun unregister(requestId: String) {
        pending.remove(requestId)
    }
}

/**
 * Интерпретатор мини-DSL ADB-команд из _wizard_spec.json (см. wizard_spec.py)
 * поверх уже установленного AdbSession — заменяет исполнение stages.py/
 * install.py на десктопе для моделей, созданных мастером "Добавить машину...".
 * Работает на фоновом потоке, вызывающий код (WebBridge) отвечает за это.
 */
class InstallEngine(
    private val context: Context,
    private val log: (String) -> Unit,
    private val requestAskInput: (requestId: String, prompt: String) -> Unit,
) {
    /**
     * commands — JSONArray объектов {"kind": ..., ...}, как возвращает
     * wizard_spec.parse_commands на Python-стороне. filesByName — basename ->
     * абсолютный локальный путь (adb_files этапа, уже скачанные
     * sync_model_payload на Python-стороне).
     */
    fun runAdbCommands(commands: JSONArray, filesByName: Map<String, String>): StageRunResult {
        var lastAsk: String? = null
        for (i in 0 until commands.length()) {
            val cmd = commands.getJSONObject(i)
            when (cmd.getString("kind")) {
                "skip" -> {}

                "sleep" -> {
                    val seconds = cmd.getDouble("seconds")
                    log("Пауза ${seconds}с...")
                    Thread.sleep((seconds * 1000).toLong())
                }

                "reboot" -> {
                    log("Перезагружаю устройство и жду его возвращения (до 90с)...")
                    AdbSession.service("reboot:", log)
                    AdbSession.disconnect()
                    val result = AdbSession.waitForDeviceAndReconnect(context, 90_000, log)
                    if (result !is AdbHandshakeResult.Connected) {
                        return StageRunResult.Failed("Устройство не вернулось после перезагрузки: ${(result as? AdbHandshakeResult.Failed)?.reason}")
                    }
                    log("Устройство снова на связи.")
                }

                "reboot_nowait" -> {
                    log("Перезагружаю устройство (без ожидания)...")
                    AdbSession.service("reboot:", log)
                    AdbSession.disconnect()
                }

                "wait_device" -> {
                    val timeoutMs = ((cmd.optDouble("timeout", 60.0)) * 1000).toLong()
                    if (!AdbSession.isConnected) {
                        val result = AdbSession.waitForDeviceAndReconnect(context, timeoutMs, log)
                        if (result !is AdbHandshakeResult.Connected) {
                            return StageRunResult.Failed("Не дождались устройства: ${(result as? AdbHandshakeResult.Failed)?.reason}")
                        }
                    }
                }

                "ask" -> {
                    val prompt = cmd.getString("prompt")
                    val requestId = UUID.randomUUID().toString()
                    val queue = AskInputBroker.register(requestId)
                    requestAskInput(requestId, prompt)
                    val value = try {
                        queue.poll(5, TimeUnit.MINUTES)
                    } finally {
                        AskInputBroker.unregister(requestId)
                    }
                    if (value == null) return StageRunResult.Failed("Не дождались ответа техника на вопрос: $prompt")
                    lastAsk = value
                }

                "root" -> logResult(AdbSession.service("root:", log))
                "disable_verity" -> logResult(AdbSession.service("disable-verity:", log))
                "remount" -> logResult(AdbSession.service("remount:", log))

                "push" -> {
                    val name = cmd.getString("file")
                    val localPath = filesByName[name]
                        ?: return StageRunResult.Failed("Файл не найден для #push: $name")
                    val remote = cmd.getString("remote")
                    // С диска потоком, не целиком в память (файлы бывают по сотни МБ, см. PushSource).
                    when (val r = AdbSession.push(PushSource.of(File(localPath)), remote, log)) {
                        is AdbPushResult.Failed -> return StageRunResult.Failed(r.reason)
                        AdbPushResult.Success -> log("Файл $name записан на устройство ($remote).")
                    }
                }

                // install_stream (desktop: cat file | pm install -S <size>) — обходной
                // путь для adbd, где обычный `adb install` не работает (см.
                // AdbInstall.kt:installApkStreamOverAdb).
                "install", "install_stream" -> {
                    val name = cmd.getString("file")
                    val localPath = filesByName[name]
                        ?: return StageRunResult.Failed("Файл не найден для установки: $name")
                    val apk = PushSource.of(File(localPath))
                    // Лямбда, а не ссылка на метод: у installApk*/PmStream появился 3-й параметр stagedPath
                    // (со значением по умолчанию), и ссылка-метода уже не подходит под тип (PushSource,(String)->Unit).
                    val install: (PushSource, (String) -> Unit) -> AdbInstallResult =
                        if (cmd.getString("kind") == "install_stream") { a, l -> AdbSession.installApkPmStream(a, l) }
                        else { a, l -> AdbSession.installApk(a, l) }
                    when (val r = install(apk, log)) {
                        is AdbInstallResult.Failed -> return StageRunResult.Failed(r.reason)
                        is AdbInstallResult.Success -> log("Установлено: $name")
                    }
                }

                "shell" -> {
                    var command = cmd.getString("command")
                    if (lastAsk != null) command = command.replace("{ask}", lastAsk)
                    when (val r = AdbSession.shell(command, log)) {
                        // Сырой вывод устройства на успешную команду больше не
                        // льём в лог — на цепочках из десятков shell-команд
                        // (например выдача разрешений через pm grant/dumpsys)
                        // это превращало лог в стену технического текста без
                        // единой понятной строки об итоге (тот же фикс, что и
                        // в desktop-версии, см. app/adb_utils.py: Adb.run()).
                        is AdbShellResult.Output -> {}
                        is AdbShellResult.Rejected -> log("Команда отклонена устройством: $command (${r.reason})")
                        is AdbShellResult.Failed -> return StageRunResult.Failed("'$command': ${r.reason}")
                    }
                }

                // "#log <команда>" (см. wizard_spec.py:parse_adb_line,
                // app/car_generator.py:_ADB_LOG_RE) — та же shell-команда,
                // что и "shell" выше, но ВЫВОД сознательно попадает в лог:
                // для диагностических кнопок ("actions"-этап), где технику
                // нужно просто нажать и отправить лог целиком (см.
                // InstallContext.shell_log на десктопе, тот же порт).
                "shell_log" -> {
                    var command = cmd.getString("command")
                    if (lastAsk != null) command = command.replace("{ask}", lastAsk)
                    log("$ $command")
                    when (val r = AdbSession.shell(command, log)) {
                        is AdbShellResult.Output -> log(r.text.ifBlank { "(пусто)" })
                        is AdbShellResult.Rejected -> log("Команда отклонена устройством: $command (${r.reason})")
                        is AdbShellResult.Failed -> return StageRunResult.Failed("'$command': ${r.reason}")
                    }
                }

                else -> log("Неизвестный тип команды в _wizard_spec.json: ${cmd.getString("kind")}")
            }
        }
        return StageRunResult.Success
    }

    /** "telnet"-этап: каждая строка commands — отдельный вызов
     * enableAdbViaTelnet с этой строкой как командой (см. TelnetAdb.kt,
     * порт cars/_shared/telnet_adb.py:enable_adb_via_telnet) — НЕ через
     * мини-DSL, raw-строки как есть (см. wizard_spec.py). */
    fun runTelnetCommands(host: String, commands: List<String>): StageRunResult {
        for (command in commands) {
            if (command.isBlank()) continue
            when (val r = enableAdbViaTelnet(context, host, command = command, log = log)) {
                is TelnetResult.Failed -> return StageRunResult.Failed(r.reason)
                TelnetResult.Success -> {}
            }
        }
        return StageRunResult.Success
    }

    // Те же ключи, что INSTALL_METHOD_KEYS в app/install_context.py (desktop)
    // и car_generator.py:StepSpec.apps_install_method — "adb_install" не
    // входит в список ниже: на Android нет отдельного "целиком через adb
    // install" протокола, единственный "классический" путь (push + pm
    // install -r) и есть INSTALL_METHODS[0], тот же, что уже был здесь
    // раньше по умолчанию — так что desktop-спека с "adb_install" просто
    // попадает в default-порядок, что и так правильно.
    // Установленный перед КАЖДЫМ install(apk, log) в installApks() ниже —
    // единственный способ дать методу dex_shell_install (см. ниже) имя
    // устанавливаемого APK: сигнатура методов в списке фиксирована
    // (apk, log) -> результат ещё с pm_install/localinstall, менять её
    // ради одного нового способа не стали — closure просто читает текущее
    // значение поля на момент вызова.
    private var currentApkName: String = "install.apk"
    // Имя пакета текущего APK (из getPackageArchiveInfo) — нужно способу jdwp_whitelist ДО установки.
    private var currentPackageName: String = "" 

    /** Отказ, причина которого не в СПОСОБЕ установки, а в самом APK: перебор остальных
     *  способов бесполезен (на Monji/Geely OneOS они и так закрываются) — сразу понятное
     *  сообщение технику вместо сырого «Failure status=5 …». null — обычный отказ. */
    private fun definitiveRejection(apkName: String, reason: String): String? {
        val upper = reason.uppercase()
        return when {
            "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in upper -> {
                val pkg = Regex("Package (\\S+) signatures").find(reason)?.groupValues?.get(1)
                val what = if (pkg != null) "приложение $pkg" else "приложение с тем же именем пакета"
                "«$apkName» не установилось: на магнитоле уже стоит $what, подписанное другим ключом " +
                    "(другая сборка или другое приложение с тем же пакетом) — поверх обновить нельзя. " +
                    "Удалите его на магнитоле вручную (Настройки → Приложения) и запустите установку заново " +
                    "или не выбирайте такие приложения вместе."
            }
            "INSTALL_FAILED_VERSION_DOWNGRADE" in upper ->
                "На магнитоле уже установлена версия «$apkName» новее (или такая же), чем в этой сборке — " +
                    "Android не позволяет тихо откатить версию назад. Удалите текущую версию приложения " +
                    "на магнитоле вручную (через её диспетчер приложений) и запустите установку заново."
            else -> null
        }
    }

    /** Выбраны разные файлы с ОДНИМ именем пакета (например GLauncher.Link и 3screen — оба
     *  com.maxinf.car): они заменяют друг друга, второй не встанет из-за другой подписи, и
     *  техник остаётся с половиной списка. Останавливаем ДО установки. Одинаковые по
     *  содержимому копии (тот же APK из пакета модели и из общей библиотеки) — не конфликт. */
    private fun duplicatePackageConflict(apkPaths: List<String>): String? {
        val byPackage = linkedMapOf<String, MutableList<File>>()
        for (path in apkPaths.distinct()) {
            val f = File(path)
            if (!f.exists()) continue
            val pkg = try {
                context.packageManager.getPackageArchiveInfo(f.path, 0)?.packageName
            } catch (e: Exception) { null } ?: continue
            byPackage.getOrPut(pkg) { mutableListOf() }.add(f)
        }
        for ((pkg, files) in byPackage) {
            if (files.size < 2) continue
            val digests = files.map { file ->
                val md = java.security.MessageDigest.getInstance("SHA-256")
                file.inputStream().use { input ->
                    val buf = ByteArray(1 shl 16)
                    while (true) { val n = input.read(buf); if (n < 0) break; md.update(buf, 0, n) }
                }
                md.digest().joinToString("") { "%02x".format(it) }
            }.toSet()
            if (digests.size > 1) {
                return "Выбраны приложения с одним и тем же именем пакета ($pkg): " +
                    files.joinToString(", ") { "«${it.name}»" } +
                    ". Они заменяют друг друга и не могут стоять вместе (второе не установится из-за другой " +
                    "подписи). Оставьте что-то одно и запустите этап заново."
            }
        }
        return null
    }

    private fun linkLostAdvice(apkName: String, technical: String?): String =
        "Связь с магнитолой оборвалась во время установки «$apkName». Проверьте Wi-Fi или кабель, " +
            "что магнитола не ушла в сон, и запустите этап заново. Техническая причина: ${technical ?: "нет ответа от устройства"}"

    // (apk, stagedPath, log): stagedPath — путь уже залитого на устройство APK (движок заливает его один
    // раз перед перебором, см. installApksWithProgress) либо null — тогда способ заливает сам, как раньше.
    private val INSTALL_METHODS: List<Pair<String, (PushSource, String?, (String) -> Unit) -> AdbInstallResult>> = listOf(
        "pm_install" to { apk, staged, methodLog -> AdbSession.installApk(apk, methodLog, staged) },
        "pm_install_stream" to { apk, staged, methodLog -> AdbSession.installApkPmStream(apk, methodLog, staged) },
        "pm_install_spoofed" to { apk, staged, methodLog -> AdbSession.installApkSpoofed(apk, methodLog, staged) },
        "localinstall" to { apk, staged, methodLog ->
            val helper = File(context.filesDir, "cars/_shared/chery_localinstall.apk")
            if (!helper.exists()) {
                AdbInstallResult.Failed("chery_localinstall.apk не найден в cars/_shared (ещё не синхронизирован?)")
            } else {
                AdbSession.installApkLocalinstall(apk, helper.readBytes(), methodLog, staged)
            }
        },
        "dex_shell_install" to { apk, staged, methodLog ->
            val helper = File(context.filesDir, "cars/_shared/dex_shell_helper.dex")
            if (!helper.exists()) {
                AdbInstallResult.Failed("dex_shell_helper.dex не найден в cars/_shared (ещё не синхронизирован?)")
            } else {
                AdbSession.installApkDexShell(apk, currentApkName, helper.readBytes(), methodLog, staged)
            }
        },
        "adb_install_haval_revived" to { apk, staged, methodLog -> AdbSession.installApkHavalRevived(apk, methodLog, staged) },
        "jdwp_whitelist" to { apk, staged, methodLog ->
            AdbSession.installApkJdwpWhitelist(apk, currentPackageName, methodLog, staged, currentApkName)
        },
    )

    /** Устанавливает список APK (по абсолютным локальным путям) по очереди,
     * автоматически подбирая рабочий способ — как desktop (см.
     * app/install_context.py:install_apk_auto), но своим набором методов
     * (см. INSTALL_METHODS выше). preferredMethod — подсказка "начни
     * перебор с этого способа" (car_generator.py:StepSpec.apps_install_method,
     * см. wizard_spec.py), не жёсткая привязка — если он не сработает,
     * пробуются остальные по обычному порядку. Способ, сработавший на
     * первом APK, запоминается на весь остаток списка — не имеет смысла
     * заново перебирать на каждом следующем файле. */
    fun installApks(apkPaths: List<String>, preferredMethod: String = "", modelDir: File? = null,
        cancelled: () -> Boolean = { false }, onProgress: (String, Int, Int, String) -> Unit = { _, _, _, _ -> }): StageRunResult =
        installApksWithProgress(apkPaths, preferredMethod, modelDir, cancelled, onProgress)

    fun installApksWithProgress(apkPaths: List<String>, preferredMethod: String = "", modelDir: File? = null,
        cancelled: () -> Boolean = { false }, onProgress: (String, Int, Int, String) -> Unit = { _, _, _, _ -> },
        onDetail: (String, Int, Int, ApkOperationProgress) -> Unit = { _, _, _, _ -> },
        mockLocationPath: String? = null): StageRunResult {
        duplicatePackageConflict(apkPaths)?.let { return StageRunResult.Failed(it) }
        var confirmedMethod: Int? = null
        val baseOrder = INSTALL_METHODS.indices.let { indices ->
            val preferredIndex = INSTALL_METHODS.indexOfFirst { it.first == preferredMethod }
            if (preferredIndex >= 0) listOf(preferredIndex) + indices.filter { it != preferredIndex } else indices.toList()
        }
        // Память «какой способ сработал на ЭТОЙ магнитоле» (по ro.product.model из баннера подключения) —
        // впереди даже подсказки модели: реальный опыт с этим устройством важнее настройки в каталоге
        // (Monjaro SE, лог #360: в каталоге стоял pm_install_spoofed, работал только dex_shell — 5 заливок
        // по 246 МБ). Только порядок: если запомненный способ не сработает, перебор идёт дальше.
        val deviceModel = AdbSession.deviceModel
        val methodPrefs = context.getSharedPreferences("install_methods", Context.MODE_PRIVATE)
        val remembered = deviceModel?.let { methodPrefs.getString(it, null) }
        val rememberedIndex = INSTALL_METHODS.indexOfFirst { it.first == remembered }
        val order = if (rememberedIndex >= 0) listOf(rememberedIndex) + baseOrder.filter { it != rememberedIndex } else baseOrder
        if (rememberedIndex >= 0) log("Для этой магнитолы ($deviceModel) в прошлый раз сработал способ «$remembered» — начинаю с него.")
        val certDir = modelDir?.let { resignCertDirForModel(it) }
        // Раньше отсутствие сертификата было немым: потерянный resign_cert Changan
        // выглядел в логе как «переподпись не нужна» (логи #361/#362/#365).
        if (certDir == null) log("Переподпись APK для этой модели не используется (сертификата files/resign_cert нет).")

        for ((index, path) in apkPaths.withIndex()) {
            if (cancelled()) return StageRunResult.Failed("Очередь остановлена пользователем")
            val startedAt = System.currentTimeMillis()
            onProgress(path, index, apkPaths.size, "running")
            onDetail(path, index, apkPaths.size, ApkOperationProgress("install"))
            fun failed(reason: String): StageRunResult.Failed {
                onProgress(path, index, apkPaths.size, "error")
                return StageRunResult.Failed(reason)
            }
            // Обрыв связи посреди передачи (Wi-Fi/кабель моргнули, магнитола на миг
            // замолчала) — один раз ждём возвращения устройства, переподключаемся тем
            // же транспортом и повторяем ТОТ ЖЕ способ. Если не помогло — понятное
            // сообщение вместо технического «получили -1 байт».
            // APK заливается на устройство ОДИН раз на все способы (stagedPath), а не каждым способом заново.
            // Переменные объявлены до perform: после обрыва связи файл на устройстве считаем потерянным.
            var stagedValid = false
            var stagingFailed = false
            var stagedRemote = ""
            // Связь не вернулась — соединение закрываем: «не подключено» на экране и сразу окно «Магнитола не
            // подключена» при следующем запуске, а не новые попытки писать в мёртвую связь.
            fun giveUp(apkName: String, technical: String?): Nothing {
                AdbSession.disconnect()
                throw AdbLinkLostException(linkLostAdvice(apkName, technical))
            }
            fun perform(install: (PushSource, String?, (String) -> Unit) -> AdbInstallResult, apk: PushSource,
                        staged: () -> String?): AdbInstallResult {
                val apkName = File(path).name
                var reconnected = false
                while (true) {
                    try {
                        val result = AdbInstallProgress.observe({ onDetail(path, index, apkPaths.size, it) }, cancelled) {
                            install(apk, staged(), log)
                        }
                        // Способ не сработал, потому что запись в магнитолу не прошла: связь умерла, и остальные
                        // способы упрутся в то же самое (лог #788: «Не удалось отправить OPEN для sync» у всех 7
                        // способов, 4 запуска подряд). Тот же путь, что при обрыве чтения: ждём магнитолу и
                        // повторяем этот способ, не вернулась — останавливаемся.
                        if (result is AdbInstallResult.Failed && AdbSession.linkLost) throw AdbLinkLostException(result.reason)
                        return result
                    } catch (e: AdbLinkLostException) {
                        onProgress(path, index, apkPaths.size, "error")
                        if (reconnected || cancelled()) giveUp(apkName, e.message)
                        reconnected = true
                        stagedValid = false  // после обрыва файл на устройстве мог не долиться — зальём заново
                        log("Связь с магнитолой оборвалась во время установки ${apkName} — жду её возвращения и повторяю...")
                        val back = try {
                            AdbSession.waitForDeviceAndReconnect(context, LINK_RECOVERY_TIMEOUT_MS, log)
                        } catch (r: Exception) {
                            AdbHandshakeResult.Failed(r.message ?: r.javaClass.simpleName)
                        }
                        if (back !is AdbHandshakeResult.Connected) {
                            giveUp(apkName, (back as AdbHandshakeResult.Failed).reason)
                        }
                        log("Связь восстановлена, повторяю установку ${apkName}.")
                        onProgress(path, index, apkPaths.size, "running")
                    } catch (e: Exception) {
                        onProgress(path, index, apkPaths.size, "error")
                        throw e
                    }
                }
            }
            val file = File(path)
            if (!file.exists()) return failed("Файл не скачан: $path")
            log("Устанавливаю ${file.name}...")
            var signedFile = file
            if (certDir != null) {
                log("Переподписываю ${file.name} сертификатом магнитолы (обязательно для этой модели)...")
                signedFile = File(file.parentFile, "${file.nameWithoutExtension}_resigned.apk")
                try {
                    resignApkFile(file, certDir, signedFile)
                } catch (e: Exception) {
                    return failed("Не удалось переподписать ${file.name}: ${e.message}")
                }
                log("Подписано: ${file.name}")
            }
            // Сам APK в память не читаем — он заливается с диска потоком (PushSource): APK бывают по 500 МБ
            // и больше, а здесь был readBytes() — и OutOfMemoryError на 166-мегабайтном MonjaroMOD (логи #449, #660).
            if (!signedFile.canRead()) return failed("Не удалось прочитать ${file.name}")
            val apk = PushSource.of(signedFile)
            // Имя файла на устройстве — без пробелов и кавычек: pm install получает путь без экранирования.
            // dex-хелпер работает с /data/local/tmp/<currentApkName> — тем же файлом.
            val stagedName = file.name.replace(Regex("[^A-Za-z0-9._-]"), "_")
            stagedRemote = "/data/local/tmp/$stagedName"
            currentApkName = stagedName
            currentPackageName = try {
                context.packageManager.getPackageArchiveInfo(path, 0)?.packageName ?: ""
            } catch (_: Exception) { "" }
            fun stagedPath(): String? {
                if (stagedValid) return stagedRemote
                if (stagingFailed) return null   // заранее залить не вышло — способы заливают сами, как раньше
                log("Заливаю ${file.name} на устройство один раз для всех способов установки...")
                AdbInstallProgress.beginTransfer(apk.size)
                return when (val r = AdbSession.push(apk, stagedRemote, log)) {
                    is AdbPushResult.Failed -> {
                        // Связь умерла ещё до установки — не «каждый способ зальёт сам», а обрыв (см. perform).
                        if (AdbSession.linkLost) throw AdbLinkLostException(r.reason)
                        stagingFailed = true
                        log("  ↳ не удалось залить заранее: ${r.reason} — каждый способ будет заливать сам")
                        null
                    }
                    AdbPushResult.Success -> {
                        AdbSession.shell("chmod 644 $stagedRemote", log)
                        stagedValid = true
                        stagedRemote
                    }
                }
            }
            fun dropStaged() {
                if (stagedValid) {
                    try { AdbSession.shell("rm -f $stagedRemote", log) } catch (_: Exception) { /* best effort */ }
                    stagedValid = false
                }
            }

            // После КАЖДОЙ успешной установки (любым способом) — все разрешения, а
            // помеченному GPS-приложению (см. mockLocationPath) ещё и фиктивное
            // местоположение. Имя пакета — из самого APK, а не из вывода способа
            // установки (у большинства способов имени нет). Сбой выдачи не должен
            // срывать установку — приложение уже стоит.
            fun afterInstall() {
                val pkg = try {
                    context.packageManager.getPackageArchiveInfo(signedFile.path, 0)?.packageName
                } catch (e: Exception) { null }
                if (pkg == null) {
                    log("Не удалось определить имя пакета ${file.name} — разрешения автоматически не выданы.")
                    return
                }
                if (!AdbPermissions.grantedSince(pkg, startedAt)) {
                    try {
                        AdbPermissions.grantAllPermissions(pkg, log)
                    } catch (e: Exception) {
                        log("Не удалось выдать разрешения $pkg: ${e.message}. Приложение установлено — разрешения можно выдать вручную на этапе «Доп. действия».")
                    }
                }
                if (path == mockLocationPath) {
                    try {
                        AdbPermissions.setMockLocationApp(pkg, log)
                    } catch (e: Exception) {
                        log("Не удалось выдать фиктивное местоположение $pkg: ${e.message}.")
                    }
                }
            }

            if (confirmedMethod != null) {
                val (_, install) = INSTALL_METHODS[confirmedMethod]
                when (val r = perform(install, apk, { stagedPath() })) {
                    is AdbInstallResult.Failed -> {
                        dropStaged()
                        return failed(definitiveRejection(file.name, r.reason) ?: "${file.name}: ${r.reason}")
                    }
                    is AdbInstallResult.Success -> {
                        log("Установлено: ${file.name}")
                        afterInstall()
                        onProgress(path, index + 1, apkPaths.size, "done")
                    }
                }
                dropStaged()
                continue
            }

            val errors = mutableListOf<String>()
            var installed = false
            for (methodIndex in order) {
                val (label, install) = INSTALL_METHODS[methodIndex]
                when (val r = perform(install, apk, { stagedPath() })) {
                    is AdbInstallResult.Failed -> {
                        errors.add("$label: ${r.reason}")
                        // Причина каждого отказа сразу в лог — итоговая ошибка идёт только в окно этапа.
                        log("  ↳ не сработало ($label): ${r.reason.split(Regex("\\s+")).joinToString(" ").take(300)}")
                        definitiveRejection(file.name, r.reason)?.let { dropStaged(); return failed(it) }
                    }
                    is AdbInstallResult.Success -> {
                        confirmedMethod = methodIndex
                        if (methodIndex != 0) {
                            log("Сработал способ установки APK: $label — дальше буду использовать его же для остальных приложений.")
                        }
                        if (deviceModel != null && methodPrefs.getString(deviceModel, null) != label) {
                            methodPrefs.edit().putString(deviceModel, label).apply()
                            log("Запомнил: для магнитолы $deviceModel работает способ «$label» — в следующий раз начну с него.")
                        }
                        log("Установлено: ${file.name}")
                        installed = true
                        afterInstall()
                        onProgress(path, index + 1, apkPaths.size, "done")
                    }
                }
                if (installed) break
            }
            dropStaged()
            if (!installed) {
                return failed(
                    "Не удалось установить ${file.name} ни одним из способов:\n" + errors.joinToString("\n")
                )
            }
        }
        return StageRunResult.Success
    }

    private fun logResult(result: AdbShellResult) {
        when (result) {
            is AdbShellResult.Output -> if (result.text.isNotBlank()) log(result.text.trim())
            is AdbShellResult.Rejected -> log("Сервис отклонён устройством: ${result.reason}")
            is AdbShellResult.Failed -> log("Ошибка: ${result.reason}")
        }
    }
}
