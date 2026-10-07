package ru.magicsqd.mobile.usb

import android.content.Context
import com.chaquo.python.Python
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.SynchronousQueue
import java.util.concurrent.TimeUnit

// Сколько ждём возвращения магнитолы после обрыва связи посреди установки.
private const val LINK_RECOVERY_TIMEOUT_MS = 45_000L

// Способ «в системную папку» (system_app): метка «поставлено программой» в /system/app/<пакет>/ (по ней кнопки
// «Доп. действий» отличают такие приложения от штатных — AdbPermissions), имя пакета, подпапки библиотек.
const val SYSTEM_APP_MARKER = ".magicsqd"
private val PACKAGE_NAME = Regex("[A-Za-z0-9_]+(\\.[A-Za-z0-9_]+)+")
private val ABI_TO_ISA = mapOf("arm64-v8a" to "arm64", "armeabi-v7a" to "arm", "armeabi" to "arm", "x86" to "x86",
    "x86_64" to "x86_64", "mips" to "mips", "mips64" to "mips64")

/** Свободное место из ответа df на магнитоле: toybox — «Filesystem 1K-blocks Used Available Use% Mounted on»
 * (числа в КБ), старый toolbox — «Filesystem Size Used Free Blksize» (1.9G, 120.5M). null — формат не узнан.
 * Как ПК: install_context.parse_df_free_bytes. */
internal fun parseDfFreeBytes(text: String): Long? {
    val rows = text.trim().lines().filter { it.isNotBlank() }.map { it.trim().split(Regex("\\s+")) }
    if (rows.size < 2) return null
    val header = rows[0].map { it.lowercase() }
    val column = listOf("available", "avail", "free").firstNotNullOfOrNull { key -> header.indexOf(key).takeIf { it >= 0 } }
        ?: return null
    val value = rows.last().getOrNull(column) ?: return null
    val match = Regex("(\\d+(?:\\.\\d+)?)([KMGTkmgt]?)").matchEntire(value) ?: return null
    val number = match.groupValues[1].toDouble()
    val unit = match.groupValues[2].uppercase()
    val multiplier = if (unit.isEmpty()) 1024.0 else Math.pow(1024.0, ("KMGT".indexOf(unit) + 1).toDouble())
    return (number * multiplier).toLong()
}

sealed class StageRunResult {
    object Success : StageRunResult()
    data class Failed(val reason: String) : StageRunResult()
    /** Очередь приложений пройдена, но часть не встала (пропущена, остальные установлены) — как на ПК
     * (runner.py: «Установка завершена, но не всё встало — пропущено: …»). message — этот итог для техника. */
    data class Partial(val message: String) : StageRunResult()
}

/** Приложение, поставленное в этом запуске этапа — для «Откатить в сток» в окне итога (владелец, 2026-09-25). */
data class InstalledApp(val packageName: String, val name: String, val path: String)

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
    /** Для «#py модуль.функция» — общий Python-код каталога (PyCtxBridge, py_runner.py); null — команда недоступна. */
    private val pyBridge: PyCtxBridge? = null,
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

                // Способ «в системную папку»: разрешения всем приложениям, записанным программой в /system/app,
                // — отдельным этапом после перезагрузки (см. AdbPermissions.grantSystemApps).
                "grant_system_apps" -> AdbPermissions.grantSystemApps(log)?.let { return StageRunResult.Failed(it) }

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

                // "#py <модуль>.<функция> [аргументы]" (wizard_spec.py, app/car_generator.py: _ADB_PY_RE) — та же
                // функция из cars/_shared, что исполняет ПК: py_runner.py поверх PyCtxBridge.
                "py" -> {
                    val bridge = pyBridge
                        ?: return StageRunResult.Failed("Команда #py в этом месте приложения недоступна — обновите приложение.")
                    val raw = cmd.optJSONArray("args") ?: JSONArray()
                    val args = JSONArray()
                    for (i in 0 until raw.length()) {
                        val arg = raw.optString(i)
                        args.put(if (lastAsk != null) arg.replace("{ask}", lastAsk) else arg)
                    }
                    val r = bridge.call(cmd.getString("module"), cmd.getString("function"), args, files = filesByName)
                    if (!r.ok) return StageRunResult.Failed(r.error.ifBlank { "${cmd.getString("module")}.${cmd.getString("function")}: ошибка" })
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
    // Файл текущего APK на телефоне — способу system_app, чтобы достать из него библиотеки.
    private var currentApkFile: File? = null
    // Способ system_app: системный раздел уже открыт на запись в этом запуске (до перезагрузки).
    private var systemPartitionReady = false

    /** Отказ, причина которого не в СПОСОБЕ установки, а в самом APK: перебор остальных
     *  способов бесполезен (на Monji/Geely OneOS они и так закрываются) — сразу понятное
     *  сообщение технику вместо сырого «Failure status=5 …». null — обычный отказ.
     *  «Версия новее уже стоит» сюда не входит — это пропуск (см. newerVersionInstalled). */
    /** «Сам файл не годится» (не APK, XAPK/APKS, другой процессор, нужен Android новее) — общий Python-модуль
     *  apk_check, та же копия, что на ПК: строки журнала одни. Перебор остальных способов бесполезен — приложение
     *  пропускается с понятной причиной (владелец, 2026-10-01: «сразу сообщать вместо перебора способов»). */
    private val apkCheck by lazy { Python.getInstance().getModule("apk_check") }

    private fun fileProblem(path: String, name: String): String? =
        try { apkCheck.callAttr("file_problem", path, name)?.toString() } catch (_: Exception) { null }

    private fun unsuitableFile(name: String, reason: String): String? =
        try { apkCheck.callAttr("rejection_message", name, reason)?.toString() } catch (_: Exception) { null }

    /** «Файл не годится» с поправкой: «нет подписи» на уже стоящем приложении — не плохой файл. На Geely G426 так
     *  кончаются повтор и обновление поверх через dex-хелпер (логи №3750, №3988) — говорим, что приложение уже стоит,
     *  вместо «нужна другая сборка». Как ПК: install_context._rejection. */
    private fun rejection(name: String, pkg: String, reason: String, log: (String) -> Unit): String? {
        if ("INSTALL_PARSE_FAILED_NO_CERTIFICATES" in reason.uppercase() && packageInstalled(pkg, log)) {
            val text = try { apkCheck.callAttr("already_installed_message", name, pkg)?.toString() } catch (_: Exception) { null }
            if (text != null) return text
        }
        return unsuitableFile(name, reason)
    }

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
            else -> null
        }
    }

    /** На магнитоле уже стоит версия новее. Раньше это останавливало всю очередь, и после перезапуска
     *  техник заново ставил всё, что уже встало (лог #968: 19 приложений по второму кругу). Владелец
     *  (2026-09-25): «если стоит более новая — пропускаем» — приложение уже есть, идём дальше. */
    private fun newerVersionInstalled(reason: String): Boolean =
        "INSTALL_FAILED_VERSION_DOWNGRADE" in reason.uppercase()

    /** Что поставлено в последнем запуске installApksWithProgress (пропущенные «уже стоит новее» — не наши). */
    @Volatile var lastInstalled: List<InstalledApp> = emptyList()
        private set

    private fun sha256Hex(file: File): String {
        val md = java.security.MessageDigest.getInstance("SHA-256")
        file.inputStream().use { input ->
            val buf = ByteArray(1 shl 16)
            while (true) { val n = input.read(buf); if (n < 0) break; md.update(buf, 0, n) }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }

    /** На магнитоле уже стоит РОВНО этот файл: SHA-256 base.apk установленного пакета совпадает с нашим. Раньше
     * после «Файл не скачан» или обрыва связи этап повторяли, и все уже поставленные приложения ставились заново
     * (лог #1347: WiFi Manager и Back Button — по три раза). Файл, а не versionCode: у модов номер версии обычно как
     * у оригинала, и совпадение по версии оставило бы не ту сборку. Любая неясность — ставим, как раньше. Хеш файла
     * считаем, только если пакет на магнитоле есть, — новые установки не замедляются. Как ПК:
     * install_context._same_apk_already_installed. */
    private fun sameApkInstalled(pkg: String, apk: File, log: (String) -> Unit): Boolean {
        if (pkg.isEmpty()) return false
        return try {
            val installed = installedBaseApk(pkg, log)
            if (installed.isNullOrEmpty()) return false
            val out = (AdbSession.shell("sha256sum $installed", log, 120_000) as? AdbShellResult.Output)?.text.orEmpty()
            val remote = out.trim().split(Regex("\\s+")).firstOrNull()?.lowercase().orEmpty()
            remote.length == 64 && remote == sha256Hex(apk)
        } catch (_: Exception) {
            false  // обрыв связи и прочее — дальше обычная установка сама разберётся (см. perform)
        }
    }

    /** base.apk уже стоящего пакета: `pm path`, а если он пути не дал — `dumpsys package` (apk_check.installed_base_apk:
     *  на Geely G426 `pm path` до сверки не доводил, лог №3988). Пусто/null — сверять не с чем. Как ПК:
     *  install_context._installed_base_apk. */
    private fun installedBaseApk(pkg: String, log: (String) -> Unit): String? {
        val listed = (AdbSession.shell("pm path $pkg", log) as? AdbShellResult.Output)?.text.orEmpty()
        val found = apkCheck.callAttr("installed_base_apk", listed, null, pkg)?.toString()
        if (found != null) return found
        val dump = (AdbSession.shell("dumpsys package $pkg", log, 60_000) as? AdbShellResult.Output)?.text.orEmpty()
        return apkCheck.callAttr("installed_base_apk", listed, dump, pkg)?.toString()
    }

    /** Пакет уже стоит на магнитоле: `pm list packages` (на Geely G426 работает — по нему dex-хелпер проверяет успех)
     *  или путь к нему. Любая неясность — false. Как ПК: install_context._package_present. */
    private fun packageInstalled(pkg: String, log: (String) -> Unit): Boolean {
        if (pkg.isEmpty()) return false
        return try {
            val listed = (AdbSession.shell("pm list packages $pkg", log) as? AdbShellResult.Output)?.text.orEmpty()
            listed.lineSequence().any { it.trim() == "package:$pkg" } || !installedBaseApk(pkg, log).isNullOrEmpty()
        } catch (_: Exception) {
            false  // обрыв связи — следующий шаг установки сам разберётся (см. perform)
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
            val digests = files.map(::sha256Hex).toSet()
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
                AdbSession.installApkLocalinstall(apk, helper.readBytes(), methodLog, staged, currentPackageName)
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
        "jdwp_whitelist" to { apk, staged, methodLog ->
            AdbSession.installApkJdwpWhitelist(apk, currentPackageName, methodLog, staged, currentApkName)
        },
        "system_app" to { apk, staged, methodLog -> installSystemApp(apk, staged, methodLog) },
    )

    /** Способы только по выбору модели, без перебора и без запасных (как ПК: install_context._EXCLUSIVE_METHODS):
     *  на других магнитолах программа не должна открывать системный раздел на запись. */
    private val EXCLUSIVE_METHODS = setOf("system_app")

    /** Свой способ установки модели — apps_install_method "py:<модуль>.<функция>" (как ПК:
     * install_context.parse_py_install_method): функция(ctx, apk, remote) из cars/_shared ставит APK сама через
     * общий Python-код (PyCtxBridge, py_runner.py); remote — путь файла, уже залитого движком на магнитолу. Новый
     * способ для новой магнитолы приходит с каталогом, без выпуска приложения. Только он, без перебора и без памяти
     * способа по магнитоле. */
    private val PY_METHOD = Regex("^py:([A-Za-z]\\w*)\\.([A-Za-z]\\w*)$")

    private fun pyInstallMethod(module: String, function: String): (PushSource, String?, (String) -> Unit) -> AdbInstallResult =
        { _, staged, _ ->
            val bridge = pyBridge
            val local = currentApkFile
            when {
                bridge == null -> AdbInstallResult.Failed("свой способ установки модели здесь недоступен — обновите приложение")
                local == null -> AdbInstallResult.Failed("нет файла APK на телефоне")
                else -> {
                    val args = JSONArray().put(local.absolutePath).put(staged ?: JSONObject.NULL)
                    val r = bridge.call(module, function, args)
                    if (r.ok) AdbInstallResult.Success("$module.$function")
                    else AdbInstallResult.Failed(r.error.ifBlank { "$module.$function не сработал" })
                }
            }
        }

    /** BAIC U5 Plus (владелец, 2026-09-27): приложение кладётся в системную папку — adb root, disable-verity,
     * remount, файл в /system/app, chmod 644. Своя папка /system/app/<пакет>/<пакет>.apk (повторная установка
     * заменяет её целиком), сжатые нативные библиотеки — рядом в lib/<arm|arm64>/ (системному приложению Android их
     * из APK не распаковывает), метка .magicsqd — «поставлено программой». APK уже залит движком в /data/local/tmp —
     * копируем его на магнитоле, без второй передачи. Android увидит приложение только после перезагрузки —
     * разрешения выдаёт отдельный этап (AdbPermissions.grantSystemApps). Как ПК: install_context.install_apk_system_app. */
    private fun installSystemApp(apk: PushSource, staged: String?, log: (String) -> Unit): AdbInstallResult {
        val pkg = currentPackageName
        val file = currentApkFile ?: return AdbInstallResult.Failed("нет файла APK на телефоне")
        if (!PACKAGE_NAME.matches(pkg)) {
            return AdbInstallResult.Failed("не удалось прочитать имя пакета ${file.name} — без него некуда положить файл в /system/app")
        }
        if (!systemPartitionReady) {
            AdbSession.openSystemPartition(context, log)?.let { return AdbInstallResult.Failed(it) }
            systemPartitionReady = true
        }
        val folder = "/system/app/$pkg"
        log("Установка APK в системную папку: ${file.name} → $folder")
        val libs = try {
            extractNativeLibs(file, log)
        } catch (e: Exception) {
            return AdbInstallResult.Failed(e.message ?: "не удалось прочитать библиотеки из «${file.name}»")
        }
        try {
            // Старая копия этого пакета, положенная раньше вручную прямо в /system/app (так ставили .bat-файлом).
            val flat = flatSystemCopy(pkg, log)
            shellText("rm -rf $folder" + (flat?.let { " '$it'" } ?: ""), log, 60_000)
            val need = file.length() + (libs?.third ?: 0L)
            val free = parseDfFreeBytes(shellText("df /system/app", log, 30_000))
            if (free != null && free < need + (1L shl 20)) {
                return AdbInstallResult.Failed("на системном разделе магнитолы не хватает места для «${file.name}»: " +
                    "нужно ${need shr 20} МБ, свободно ${free shr 20} МБ")
            }
            val target = "$folder/$pkg.apk"
            if (staged != null) {
                val out = shellText("mkdir -p $folder && cat $staged > $target && echo MSQD_OK", log, 300_000)
                if (!out.contains("MSQD_OK")) return AdbInstallResult.Failed(systemWriteError(file.name, out))
            } else {
                AdbInstallProgress.beginTransfer(apk.size)
                when (val r = AdbSession.push(apk, target, log)) {
                    is AdbPushResult.Failed -> return AdbInstallResult.Failed(systemWriteError(file.name, r.reason))
                    AdbPushResult.Success -> {}
                }
            }
            val dirs = mutableListOf(folder)
            val files = mutableListOf(target)
            if (libs != null) {
                val (isa, libDir, _) = libs
                dirs += listOf("$folder/lib", "$folder/lib/$isa")
                for (so in libDir.listFiles().orEmpty().sortedBy { it.name }) {
                    val remote = "$folder/lib/$isa/${so.name}"
                    when (val r = AdbSession.push(PushSource.of(so), remote, log)) {
                        is AdbPushResult.Failed -> return AdbInstallResult.Failed(systemWriteError(so.name, r.reason))
                        AdbPushResult.Success -> files += remote
                    }
                }
            }
            val marker = "$folder/$SYSTEM_APP_MARKER"
            val out = shellText("chmod 755 ${dirs.joinToString(" ")} && chmod 644 ${files.joinToString(" ")} && " +
                "echo magicsqd > $marker && chmod 644 $marker && echo MSQD_OK", log, 60_000)
            if (!out.contains("MSQD_OK")) {
                return AdbInstallResult.Failed("не удалось выставить права файлам в $folder: ${out.ifBlank { "нет ответа" }}")
            }
            log("Записано в $folder" + (libs?.let { " (с библиотеками lib/${it.first})" } ?: "") + ", права 644.")
            return AdbInstallResult.Success("system_app")
        } finally {
            libs?.second?.parentFile?.deleteRecursively()
        }
    }

    private fun shellText(command: String, log: (String) -> Unit, timeoutMs: Int): String =
        when (val r = AdbSession.shell(command, log, timeoutMs)) {
            is AdbShellResult.Output -> r.text
            is AdbShellResult.Rejected -> "команда отклонена: ${r.reason}"
            is AdbShellResult.Failed -> "ошибка: ${r.reason}"
        }

    /** «No space left» и «Read-only file system» здесь — про память магнитолы, а не про флешку (окно «что
     *  сделать» узнаёт эти фразы, см. user_errors.js). */
    private fun systemWriteError(name: String, reason: String): String {
        val short = reason.split(Regex("\\s+")).joinToString(" ").trim().take(200)
        return if (short.contains("no space left", ignoreCase = true)) "на системном разделе магнитолы не хватает места для «$name» ($short)"
        else "не удалось записать в системный раздел «$name»: ${short.ifBlank { "нет ответа" }}"
    }

    /** Путь уже стоящего этого пакета, если это файл прямо в /system/app (не наша папка и не штатная). */
    private fun flatSystemCopy(pkg: String, log: (String) -> Unit): String? {
        val paths = shellText("pm path $pkg", log, 30_000).lines().map { it.trim() }
            .filter { it.startsWith("package:") }.map { it.removePrefix("package:") }
        return paths.singleOrNull()?.takeIf { Regex("/system/app/[^/\\s']+\\.apk").matches(it) }
    }

    private var deviceAbis: List<String>? = null

    private fun deviceAbiList(log: (String) -> Unit): List<String> {
        deviceAbis?.let { return it }
        var abis = shellText("getprop ro.product.cpu.abilist", log, 30_000).trim().split(",").map { it.trim() }
            .filter { it.isNotEmpty() && !it.contains(" ") }
        if (abis.isEmpty()) {
            abis = listOf(shellText("getprop ro.product.cpu.abi", log, 30_000).trim()).filter { it.isNotEmpty() && !it.contains(" ") }
        }
        return abis.ifEmpty { listOf("arm64-v8a", "armeabi-v7a", "armeabi") }.also { deviceAbis = it }
    }

    /** Сжатые .so из APK под процессор магнитолы → (isa, папка на телефоне, размер). null — библиотек нет или все
     *  лежат несжатыми и выровненными (их Android грузит прямо из APK). Набор — как выбирает сам Android: первый из
     *  ro.product.cpu.abilist, который есть в APK. */
    private fun extractNativeLibs(apk: File, log: (String) -> Unit): Triple<String, File, Long>? {
        java.util.zip.ZipFile(apk).use { zip ->
            val byAbi = linkedMapOf<String, MutableList<java.util.zip.ZipEntry>>()
            for (entry in zip.entries()) {
                val parts = entry.name.split("/")
                if (parts.size == 3 && parts[0] == "lib" && parts[2].endsWith(".so")) byAbi.getOrPut(parts[1]) { mutableListOf() }.add(entry)
            }
            if (byAbi.isEmpty()) return null
            val abis = deviceAbiList(log)
            val abi = abis.firstOrNull { it in byAbi }
                ?: throw IllegalStateException("в «${apk.name}» нет библиотек под процессор магнитолы (${abis.joinToString(", ")}), " +
                    "есть только ${byAbi.keys.sorted().joinToString(", ")}")
            val entries = byAbi.getValue(abi)
            if (entries.all { storedPageAligned(apk, it) }) return null
            val isa = ABI_TO_ISA[abi] ?: abi
            val root = File(context.cacheDir, "system_app_libs").apply { deleteRecursively() }
            val libDir = File(root, isa).apply { mkdirs() }
            var total = 0L
            for (entry in entries) {
                val target = File(libDir, entry.name.substringAfterLast('/'))
                zip.getInputStream(entry).use { input -> target.outputStream().use { input.copyTo(it, 1 shl 16) } }
                total += target.length()
            }
            return Triple(isa, libDir, total)
        }
    }

    /** .so лежит в APK несжатым и с данными на границе страницы 4096 — Android загрузит его прямо из APK. */
    private fun storedPageAligned(apk: File, entry: java.util.zip.ZipEntry): Boolean {
        if (entry.method != java.util.zip.ZipEntry.STORED) return false
        return try {
            java.io.RandomAccessFile(apk, "r").use { raf ->
                val offset = localHeaderOffset(raf, entry.name) ?: return false
                raf.seek(offset)
                val header = ByteArray(30)
                raf.readFully(header)
                if (header[0] != 0x50.toByte() || header[1] != 0x4b.toByte() || header[2] != 3.toByte() || header[3] != 4.toByte()) return false
                val nameLen = (header[26].toInt() and 0xff) or ((header[27].toInt() and 0xff) shl 8)
                val extraLen = (header[28].toInt() and 0xff) or ((header[29].toInt() and 0xff) shl 8)
                (offset + 30 + nameLen + extraLen) % 4096 == 0L
            }
        } catch (_: Exception) {
            false
        }
    }

    /** Смещение локального заголовка файла из центрального каталога zip (java.util.zip его не отдаёт). */
    private fun localHeaderOffset(raf: java.io.RandomAccessFile, name: String): Long? {
        val len = raf.length()
        val tailSize = minOf(len, 65_557L).toInt()
        val tail = ByteArray(tailSize)
        raf.seek(len - tailSize)
        raf.readFully(tail)
        fun u16(b: ByteArray, i: Int) = (b[i].toInt() and 0xff) or ((b[i + 1].toInt() and 0xff) shl 8)
        fun u32(b: ByteArray, i: Int) = u16(b, i).toLong() or (u16(b, i + 2).toLong() shl 16)
        var eocd = -1
        for (i in tailSize - 22 downTo 0) {
            if (tail[i] == 0x50.toByte() && tail[i + 1] == 0x4b.toByte() && tail[i + 2] == 5.toByte() && tail[i + 3] == 6.toByte()) { eocd = i; break }
        }
        if (eocd < 0) return null
        val count = u16(tail, eocd + 10)
        val cdSize = u32(tail, eocd + 12)
        val cdOffset = u32(tail, eocd + 16)
        if (cdOffset + cdSize > len) return null
        val cd = ByteArray(cdSize.toInt())
        raf.seek(cdOffset)
        raf.readFully(cd)
        var pos = 0
        repeat(count) {
            if (pos + 46 > cd.size || u32(cd, pos) != 0x02014b50L) return null
            val nameLen = u16(cd, pos + 28)
            val extraLen = u16(cd, pos + 30)
            val commentLen = u16(cd, pos + 32)
            val entryName = String(cd, pos + 46, nameLen, Charsets.UTF_8)
            if (entryName == name) return u32(cd, pos + 42)
            pos += 46 + nameLen + extraLen + commentLen
        }
        return null
    }

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
        cancelled: () -> Boolean = { false }, onProgress: (String, Int, Int, String) -> Unit = { _, _, _, _ -> },
        preStaged: Map<String, String> = emptyMap()): StageRunResult =
        installApksWithProgress(apkPaths, preferredMethod, modelDir, cancelled, onProgress, preStaged = preStaged)

    fun installApksWithProgress(apkPaths: List<String>, preferredMethod: String = "", modelDir: File? = null,
        cancelled: () -> Boolean = { false }, onProgress: (String, Int, Int, String) -> Unit = { _, _, _, _ -> },
        onDetail: (String, Int, Int, ApkOperationProgress) -> Unit = { _, _, _, _ -> },
        mockLocationPath: String? = null,
        // Файлы, уже лежащие на магнитоле (локальный путь → путь на ней, см. stagedRemoteFor): не заливаются заново,
        // способы берут их с её диска (MotionOptimize: снимаем приложение, только когда новая версия уже там).
        preStaged: Map<String, String> = emptyMap()): StageRunResult {
        val installed = mutableListOf<InstalledApp>()
        lastInstalled = installed
        duplicatePackageConflict(apkPaths)?.let { return StageRunResult.Failed(it) }
        var confirmedMethod: Int? = null
        // Приложения, которые не встали уже ПОСЛЕ того, как способ подтвердился на этой магнитоле, — пропущены,
        // очередь идёт дальше (см. ветку confirmedMethod ниже).
        val skipped = mutableListOf<String>()
        var okCount = 0  // встало или уже стояло — если 0, итог «Не установлено», а не «остальные установлены»
        // Свой способ модели "py:модуль.функция" (см. PY_METHOD) — в конец списка способов, индексы встроенных те же.
        val pyMethod = PY_METHOD.matchEntire(preferredMethod.trim())
        val methods = if (pyMethod == null) INSTALL_METHODS
            else INSTALL_METHODS + (preferredMethod.trim() to pyInstallMethod(pyMethod.groupValues[1], pyMethod.groupValues[2]))
        // system_app и свой способ модели — только по выбору модели и без перебора остальных (см. EXCLUSIVE_METHODS).
        val exclusive = preferredMethod in EXCLUSIVE_METHODS || pyMethod != null
        systemPartitionReady = false
        var systemAppsWritten = 0
        val baseOrder = methods.indices.filter { methods[it].first !in EXCLUSIVE_METHODS }.let { indices ->
            val preferredIndex = methods.indexOfFirst { it.first == preferredMethod.trim() }
            when {
                exclusive -> listOf(preferredIndex)
                preferredIndex >= 0 -> listOf(preferredIndex) + indices.filter { it != preferredIndex }
                else -> indices
            }
        }
        // Память «какой способ сработал на ЭТОЙ магнитоле» (по ro.product.model из баннера подключения) —
        // впереди даже подсказки модели: реальный опыт с этим устройством важнее настройки в каталоге
        // (Monjaro SE, лог #360: в каталоге стоял pm_install_spoofed, работал только dex_shell — 5 заливок
        // по 246 МБ). Только порядок: если запомненный способ не сработает, перебор идёт дальше.
        val deviceModel = AdbSession.deviceModel
        val methodPrefs = context.getSharedPreferences("install_methods", Context.MODE_PRIVATE)
        val remembered = if (exclusive) null
            else deviceModel?.let { methodPrefs.getString(it, null) }?.takeIf { it !in EXCLUSIVE_METHODS }
        val rememberedIndex = methods.indexOfFirst { it.first == remembered }
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
            val preStagedRemote = preStaged[path]
            var stagedValid = preStagedRemote != null
            var stagingFailed = false
            var stagedRemote = preStagedRemote ?: ""
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
                        // после обрыва файл на устройстве мог не долиться — зальём заново (заранее залитый — целый)
                        if (preStagedRemote == null) stagedValid = false
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
            fun skipUnsuitable(message: String) {
                log(message)
                skipped.add(message)
                onProgress(path, index, apkPaths.size, "error")
            }
            // Файл, который не встанет никаким способом (не APK, XAPK/APKS, повреждён), — сразу понятная причина,
            // без заливки на магнитолу и перебора способов (логи №1986, №2099).
            val problem = fileProblem(path, file.name)
            if (problem != null) { skipUnsuitable(problem); continue }
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
            val stagedName = stagedNameFor(file.name)
            stagedRemote = preStagedRemote ?: "/data/local/tmp/$stagedName"
            currentApkName = stagedName
            currentPackageName = try {
                context.packageManager.getPackageArchiveInfo(path, 0)?.packageName ?: ""
            } catch (_: Exception) { "" }
            currentApkFile = signedFile
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
            fun afterInstall(installedNow: Boolean = true) {
                okCount++
                if (installedNow && confirmedMethod?.let { methods[it].first } == "system_app") {
                    // Приложение из /system/app Android увидит только после перезагрузки, до неё разрешения не выдать
                    // («Unknown package»), а ADB на BAIC U5 Plus перезагрузку не переживает — разрешения выдаёт
                    // отдельный этап после неё (AdbPermissions.grantSystemApps); фиктивное местоположение он возьмёт
                    // из метки. В «Откатить в сток» не попадает: pm uninstall системное не удаляет.
                    systemAppsWritten++
                    if (path == mockLocationPath && PACKAGE_NAME.matches(currentPackageName)) {
                        shellText("echo 'magicsqd mock_location' > /system/app/$currentPackageName/$SYSTEM_APP_MARKER", log, 30_000)
                    }
                    return
                }
                val pkg = try {
                    context.packageManager.getPackageArchiveInfo(signedFile.path, 0)?.packageName
                } catch (e: Exception) { null }
                if (pkg == null) {
                    log("Не удалось определить имя пакета ${file.name} — разрешения автоматически не выданы.")
                    return
                }
                if (installedNow && installed.none { it.packageName == pkg }) installed.add(InstalledApp(pkg, file.name, path))
                if (!AdbPermissions.grantedSince(pkg, startedAt)) {
                    try {
                        // Ход выдачи — в кольцо этой строки (фаза grant), как у выдачи внутри localinstall/dex_shell,
                        // которая идёт ещё в области perform. Остановкой не прерываем: приложение уже стоит.
                        AdbInstallProgress.observe({ onDetail(path, index, apkPaths.size, it) }, { false }) {
                            AdbPermissions.grantAllPermissions(pkg, log)
                        }
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

            // Версия новее уже стоит — установку пропускаем, разрешения выдаём уже стоящей версии.
            fun keepNewer() {
                log("«${file.name}»: на магнитоле уже стоит версия новее — установку пропускаю.")
                afterInstall(installedNow = false)
                onProgress(path, index + 1, apkPaths.size, "done")
            }

            // Этот же файл уже стоит (этап повторяют после «Файл не скачан» или обрыва) — не заливаем и не ставим
            // заново (владелец, 2026-09-27); разрешения выдаём, как после установки.
            if (sameApkInstalled(currentPackageName, signedFile, log)) {
                log("«${file.name}»: на магнитоле уже стоит этот же файл — установку пропускаю.")
                afterInstall(installedNow = false)
                onProgress(path, index + 1, apkPaths.size, "done")
                continue
            }

            if (confirmedMethod != null) {
                val (label, install) = methods[confirmedMethod]
                var result = perform(install, apk, { stagedPath() })
                // localinstall (Chery-хелпер) ставит только новые приложения: поверх установленного PackageManager
                // отказывает («Attempt to re-install … without first uninstalling», в logcat — в выводе пусто). На
                // Haval sa8155 штатный VK Video так пропускался, пока не пошёл dex-хелпер (лог #1670). Уже стоящее
                // приложение обновляем dex-хелпером — у него флаг замены есть. Как ПК: install_context._update_with_dex_shell.
                if (result is AdbInstallResult.Failed && label == "localinstall" && packageInstalled(currentPackageName, log)) {
                    log("«${file.name}»: $currentPackageName уже стоит на магнитоле, а localinstall ставит только новые " +
                        "приложения — обновляю через dex-хелпер.")
                    val dexShell = INSTALL_METHODS.first { it.first == "dex_shell_install" }.second
                    result = when (val update = perform(dexShell, apk, { stagedPath() })) {
                        is AdbInstallResult.Failed -> AdbInstallResult.Failed(
                            "$currentPackageName уже стоит, localinstall поверх не ставит, dex-хелпер не обновил: ${update.reason}")
                        else -> update
                    }
                }
                when (val r = result) {
                    is AdbInstallResult.Failed -> {
                        dropStaged()
                        if (newerVersionInstalled(r.reason)) { keepNewer(); continue }
                        // Отказ из-за самого APK (другая подпись) — стоп, как на ПК (SignatureMismatchError).
                        definitiveRejection(file.name, r.reason)?.let { return failed(it) }
                        // Способ на этой магнитоле уже сработал — не встало только это приложение: пропускаем и ставим
                        // остальные, как ПК (install_context.py: AppInstallFailed). Раньше вся очередь останавливалась
                        // на нём (лог #764: Settings.apk с INSTALL_FAILED_CONFLICTING_PROVIDER, остальное — вторым запуском).
                        val reason = r.reason.split(Regex("\\s+")).joinToString(" ")
                        log("  ↳ не сработало ($label): ${reason.take(300)}")
                        val unsuitable = rejection(file.name, currentPackageName, r.reason, log)
                        if (unsuitable != null) { skipUnsuitable(unsuitable); continue }
                        skipped.add("${file.name}: ${reason.take(150)}")
                        onProgress(path, index, apkPaths.size, "error")
                        continue
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
            var done = false
            var keptNewer = false
            var unsuitable: String? = null
            for (methodIndex in order) {
                val (label, install) = methods[methodIndex]
                var attempt = perform(install, apk, { stagedPath() })
                var updatedByDex = false
                // Запомненный для магнитолы (или заданный моделью) localinstall ставит только новые приложения: уже
                // стоящее сразу обновляем dex-хелпером, как в ветке confirmedMethod выше, и запомненный способ не
                // меняем. Иначе первое же обновление в сеансе перебирало pm-способы и перезапоминало dex_shell_install
                // (логи №2786, №2934, №3031 — обновлённый Haval H3: pm install закрыт, новые ставит только localinstall).
                if (attempt is AdbInstallResult.Failed && label == "localinstall" &&
                    (remembered == "localinstall" || preferredMethod == "localinstall") &&
                    packageInstalled(currentPackageName, log)) {
                    log("«${file.name}»: $currentPackageName уже стоит на магнитоле, а localinstall ставит только новые " +
                        "приложения — обновляю через dex-хелпер.")
                    val dexShell = INSTALL_METHODS.first { it.first == "dex_shell_install" }.second
                    when (val update = perform(dexShell, apk, { stagedPath() })) {
                        is AdbInstallResult.Success -> { attempt = update; updatedByDex = true }
                        is AdbInstallResult.Failed -> log("  ↳ dex-хелпер не обновил: " +
                            update.reason.split(Regex("\\s+")).joinToString(" ").take(300))
                    }
                }
                when (val r = attempt) {
                    is AdbInstallResult.Failed -> {
                        errors.add("$label: ${r.reason}")
                        // Причина каждого отказа сразу в лог — итоговая ошибка идёт только в окно этапа.
                        log("  ↳ не сработало ($label): ${r.reason.split(Regex("\\s+")).joinToString(" ").take(300)}")
                        // До PackageManager способ дошёл, отказ — из-за версии: остальные способы упрутся в то же.
                        if (newerVersionInstalled(r.reason)) { keptNewer = true; break }
                        definitiveRejection(file.name, r.reason)?.let { dropStaged(); return failed(it) }
                        // Отказ из-за самого файла — остальные способы упрутся в то же самое (логи №1942, №2087).
                        unsuitable = rejection(file.name, currentPackageName, r.reason, log)
                        if (unsuitable != null) break
                    }
                    is AdbInstallResult.Success -> {
                        // Обновил dex-хелпер вместо localinstall — способ магнитолы прежний; localinstall подтверждаем на
                        // сеанс, только если он уже работал на ней (запомнен), иначе следующее приложение — снова по порядку.
                        confirmedMethod = if (updatedByDex && remembered != "localinstall") null else methodIndex
                        if (!updatedByDex && methodIndex != 0 && !exclusive) {
                            log("Сработал способ установки APK: $label — дальше буду использовать его же для остальных приложений.")
                        }
                        if (!updatedByDex && deviceModel != null && !exclusive && methodPrefs.getString(deviceModel, null) != label) {
                            methodPrefs.edit().putString(deviceModel, label).apply()
                            log("Запомнил: для магнитолы $deviceModel работает способ «$label» — в следующий раз начну с него.")
                        }
                        log("Установлено: ${file.name}")
                        done = true
                        afterInstall()
                        onProgress(path, index + 1, apkPaths.size, "done")
                    }
                }
                if (done) break
            }
            dropStaged()
            if (keptNewer) { keepNewer(); continue }
            if (unsuitable != null) { skipUnsuitable(unsuitable); continue }
            if (!done) {
                // Один способ без перебора — его причина и есть итог (без списка «ни одним из способов»).
                if (exclusive) return failed("«${file.name}» не установлено: " + errors.last().substringAfter(": "))
                return failed(
                    "Не удалось установить ${file.name} ни одним из способов:\n" + errors.joinToString("\n")
                )
            }
        }
        if (systemAppsWritten > 0) {
            log("Приложения записаны в системную папку. Android увидит их после перезагрузки магнитолы — тогда им " +
                "можно выдать разрешения (кнопка «Выдать разрешения установленным приложениям»).")
        }
        if (skipped.isNotEmpty() && okCount == 0) {
            log("Не установлено: " + skipped.joinToString("; "))
            return StageRunResult.Failed("Не установлено: " + skipped.joinToString("; "))
        }
        if (skipped.isNotEmpty()) {
            log("Не установлено (пропущено, остальные приложения из списка установлены): " + skipped.joinToString("; "))
            return StageRunResult.Partial(
                "Установка завершена, но не всё встало — пропущено: " + skipped.joinToString("; ") +
                    ". Остальные приложения установлены."
            )
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

/** Имя APK на магнитоле (/data/local/tmp/<имя>) — без пробелов и кавычек: pm install получает путь без экранирования,
 *  dex-хелпер берёт тот же файл. Общее с MotionOptimize: он заливает файлы заранее, движок ставит их с этого пути. */
internal fun stagedNameFor(fileName: String): String = fileName.replace(Regex("[^A-Za-z0-9._-]"), "_")
