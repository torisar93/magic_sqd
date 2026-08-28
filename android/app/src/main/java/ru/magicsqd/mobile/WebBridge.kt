package ru.magicsqd.mobile

import android.content.Context
import android.webkit.JavascriptInterface
import android.webkit.WebView
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONArray
import org.json.JSONObject
import ru.magicsqd.mobile.usb.AdbHandshakeResult
import ru.magicsqd.mobile.usb.AdbSession
import ru.magicsqd.mobile.usb.AdbShellResult
import ru.magicsqd.mobile.usb.AskInputBroker
import ru.magicsqd.mobile.usb.InstallEngine
import ru.magicsqd.mobile.usb.MdnsResolve
import ru.magicsqd.mobile.usb.NetworkScan
import ru.magicsqd.mobile.usb.StageRunResult
import ru.magicsqd.mobile.usb.UsbFlashSession
import ru.magicsqd.mobile.usb.writeUsbStage
import java.io.File

/**
 * Мост JS<->Kotlin для мобильного интерфейса (assets/index.html). `call()` —
 * единственная точка входа для БЫСТРЫХ синхронных операций (список машин из
 * уже скачанного локально cars/). Синхронизация с сервером (может занять
 * время — сотни мелких HTTP-запросов, см. content_sync.py) идёт в фоновом
 * потоке и сообщает о завершении через evaluateJavascript(
 * "window.__onBridgeEvent(...)") — тот же принцип событийной шины, что и в
 * desktop-версии (app/web/frontend/js/events.js), источник событий другой.
 */
class WebBridge(private val context: Context, private val webView: WebView) {

    companion object {
        // Тот же адрес, что и в server.json на десктопе — публичный
        // content-сервер, не секрет.
        private const val BASE_URL = "https://magicsqd.ru/content"
    }

    // AdbSession/UsbFlashSession — общие на процесс синглтоны БЕЗ внутренней
    // синхронизации (см. их код): например runAdbCommands() дёргает
    // AdbSession.shell()/push()/installApk() МНОГО раз за один вызов, так что
    // блокировки внутри отдельных методов не спасли бы от гонки, если две
    // такие последовательности стартуют одновременно (двойной тап по
    // "Выполнить"/действию из "actions"-этапа, которые НЕ дизейблятся на
    // время выполнения — специально, они задуманы повторно нажимаемыми) —
    // конкурентные bulkTransfer на одном USB-соединении могут перепутать
    // ответы между двумя логическими потоками команд. Проще всего — не
    // давать двум таким операциям идти одновременно вообще, как и на
    // desktop (один adb.exe-процесс блокирующе выполняет этап за этапом).
    private val operationBusy = java.util.concurrent.atomic.AtomicBoolean(false)

    private fun runExclusive(onBusy: () -> Unit, body: () -> Unit) {
        if (!operationBusy.compareAndSet(false, true)) {
            onBusy()
            return
        }
        Thread {
            try {
                body()
            } finally {
                operationBusy.set(false)
            }
        }.start()
    }

    init {
        if (!Python.isStarted()) Python.start(AndroidPlatform(context))
        webView.keepScreenOn = context.getSharedPreferences("settings", Context.MODE_PRIVATE)
            .getBoolean("keep_screen_on", true)
    }

    private val carsDir get() = java.io.File(context.filesDir, "cars").apply { mkdirs() }.absolutePath
    private val apkDir get() = java.io.File(context.filesDir, "apk").apply { mkdirs() }.absolutePath

    @JavascriptInterface
    fun call(method: String, argsJson: String): String {
        val args = JSONObject(argsJson)
        return try {
            when (method) {
                // versionName из PackageManager (см. build.gradle.kts) — само
                // приложение раньше нигде не показывало свою версию, только
                // Google Play/RuStore её знали (см. аудит проекта).
                "app_version" -> JSONObject().put(
                    "version",
                    context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "?"
                ).toString()
                "settings_info" -> settingsInfo().toString()
                "settings_preferences" -> settingsPreferences().toString()
                "settings_clear_cache" -> clearCache().toString()
                "settings_set_preferences" -> setSettingsPreferences(args).toString()
                "settings_sync_now" -> { startSync(); "{}" }
                "start_sync" -> { startSync(); "{}" }
                "app_update_check" -> { startUpdateCheck(); "{}" }
                "get_sync_progress" -> pyModule("mobile_bridge").callAttr("get_sync_progress").toString()
                "scanner_list_cars" -> pyModule("mobile_bridge").callAttr("list_cars", carsDir).toString()
                "scanner_select_model" -> pyModule("mobile_bridge").callAttr(
                    "select_model",
                    args.getString("key"),
                    args.getString("brand"),
                    args.getString("name"),
                    args.optString("modification", ""),
                ).toString()
                "sync_model_payload" -> { syncModelPayload(args.getString("model_key")); "{}" }
                "scanner_list_apks" -> { listApks(); "{}" }
                "install_load_stages" -> pyModule("mobile_bridge").callAttr(
                    "load_install_stages", args.getString("model_key"), context.filesDir.absolutePath
                ).toString()
                "adb_is_connected" -> JSONObject().put("connected", AdbSession.isConnected).toString()
                "adb_connect" -> { adbConnect(); "{}" }
                "adb_connect_wifi" -> { adbConnectWifi(args.getString("host"), args.optInt("port", 5555)); "{}" }
                "adb_disconnect" -> { AdbSession.disconnect(); "{}" }
                "adb_run_stage" -> { adbRunStage(args); "{}" }
                "adb_install_apks" -> { adbInstallApks(args); "{}" }
                "telnet_run_stage" -> { telnetRunStage(args); "{}" }
                "adb_shell_command" -> { adbShellCommand(args.getString("command")); "{}" }
                "scan_hosts" -> { scanHosts(args.optInt("port", 5555)); "{}" }
                "adb_ask_input_response" -> {
                    AskInputBroker.resolve(args.getString("requestId"), args.getString("value"))
                    "{}"
                }
                "usb_is_mounted" -> JSONObject().put("mounted", UsbFlashSession.isMounted).toString()
                "usb_connect" -> { usbConnect(); "{}" }
                "usb_disconnect" -> { UsbFlashSession.disconnect(); "{}" }
                "usb_format" -> { usbFormat(args); "{}" }
                "usb_run_stage" -> { usbRunStage(args); "{}" }
                else -> JSONObject().put("error", "Неизвестный метод: $method").toString()
            }
        } catch (e: Exception) {
            JSONObject().put("error", (e.message ?: "неизвестная ошибка")).toString()
        }
    }

    private fun pyModule(name: String) = Python.getInstance().getModule(name)

    private fun settingsInfo(): JSONObject {
        val preferences = settingsPreferences()
        val cacheBytes = cacheTargets().sumOf(::directorySize) + directorySize(context.cacheDir)
        val appBytes = File(context.applicationInfo.sourceDir).length() + directorySize(context.filesDir) + directorySize(context.cacheDir)
        return JSONObject()
            .put("app_bytes", appBytes)
            .put("cache_bytes", cacheBytes)
            .put("server_configured", true)
            .put("preferences", preferences)
    }

    private fun settingsPreferences(): JSONObject {
        val prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE)
        return JSONObject()
            .put("auto_sync", prefs.getBoolean("auto_sync", true))
            .put("reduced_motion", prefs.getBoolean("reduced_motion", false))
            .put("compact_log", prefs.getBoolean("compact_log", true))
            .put("keep_screen_on", prefs.getBoolean("keep_screen_on", true))
    }

    private fun setSettingsPreferences(args: JSONObject): JSONObject {
        val prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE)
        val editor = prefs.edit()
        listOf("auto_sync", "reduced_motion", "compact_log", "keep_screen_on").forEach { key ->
            if (args.has(key)) editor.putBoolean(key, args.getBoolean(key))
        }
        editor.apply()
        if (args.has("keep_screen_on")) webView.keepScreenOn = args.getBoolean("keep_screen_on")
        return settingsInfo().getJSONObject("preferences")
    }

    private fun clearCache(): JSONObject {
        val before = cacheTargets().sumOf(::directorySize) + directorySize(context.cacheDir)
        cacheTargets().forEach(::deleteRecursively)
        context.cacheDir.listFiles()?.forEach(::deleteRecursively)
        val remaining = cacheTargets().sumOf(::directorySize) + directorySize(context.cacheDir)
        return JSONObject().put("freed_bytes", before - remaining).put("remaining_bytes", remaining)
    }

    private fun cacheTargets(): List<File> {
        val targets = mutableListOf(File(apkDir))
        val root = File(carsDir)
        if (root.isDirectory) root.walkTopDown().filter { it.isDirectory && (it.name == "files" || it.name == "usb_files") }.forEach(targets::add)
        return targets
    }

    private fun directorySize(file: File): Long = when {
        file.isFile -> file.length()
        file.isDirectory -> file.listFiles()?.sumOf(::directorySize) ?: 0L
        else -> 0L
    }

    private fun deleteRecursively(file: File) { if (file.exists()) file.deleteRecursively() }

    private fun startSync() {
        Thread {
            val resultJson = try {
                pyModule("mobile_bridge").callAttr("sync_cars", carsDir, BASE_URL).toString()
            } catch (e: Exception) {
                JSONObject().put("error", (e.message ?: "неизвестная ошибка")).toString()
            }
            val event = JSONObject().put("kind", "sync_finished").put("result", JSONObject(resultJson))
            pushEvent(event)
        }.start()
    }

    /** Проверка новой версии приложения на GitHub (см. mobile_bridge.
     * check_update) — только ссылка на apk, БЕЗ автоустановки: в отличие от
     * desktop-версии (app/web/api/update_api.py), у обычного приложения нет
     * прав тихо заменить себя без root. Техник открывает ссылку сам, дальше
     * системный установщик пакетов. */
    private fun startUpdateCheck() {
        val currentVersion = context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "0"
        Thread {
            val resultJson = try {
                pyModule("mobile_bridge").callAttr("check_update", currentVersion).toString()
            } catch (e: Exception) {
                "{\"available\":false}"
            }
            pushEvent(JSONObject().put("kind", "update_check_result").put("result", JSONObject(resultJson)))
        }.start()
    }

    private fun pushEvent(event: JSONObject) {
        val js = "window.__onBridgeEvent(${JSONObject.quote(event.toString())})"
        webView.post { webView.evaluateJavascript(js, null) }
    }

    private fun pushAdbLog(line: String) {
        pushEvent(JSONObject().put("kind", "adb_log").put("line", line))
    }

    /** Точечно скачивает files/usb_files выбранной модели (см.
     * content_sync.sync_model_payload) — вызывается перед открытием мастера
     * установки, до install_load_stages. */
    private fun syncModelPayload(modelKey: String) {
        Thread {
            val resultJson = try {
                pyModule("mobile_bridge").callAttr("sync_payload", carsDir, BASE_URL, modelKey).toString()
            } catch (e: Exception) {
                JSONObject().put("error", (e.message ?: "неизвестная ошибка")).toString()
            }
            pushEvent(JSONObject().put("kind", "model_sync_finished").put("result", JSONObject(resultJson)))
        }.start()
    }

    /** Общая библиотека приложений (apk/, см. apk_library.py) — список
     * приходит сразу (локальные + известные с сервера, но ещё не скачанные),
     * сами .apk байты качаются только для отмеченных техником (см.
     * ensureApksDownloaded ниже, перед adb_install_apks/usb_run_stage). */
    private fun listApks() {
        Thread {
            val resultJson = try {
                pyModule("mobile_bridge").callAttr("list_apks", apkDir, BASE_URL).toString()
            } catch (e: Exception) {
                "[]"
            }
            pushEvent(JSONObject().put("kind", "apk_library_result").put("apks", org.json.JSONArray(resultJson)))
        }.start()
    }

    /** Докачивает те файлы из paths, которых ещё нет на диске — общую
     * библиотеку (apk/) И "свои" файлы конкретной модели (APK apps-этапа,
     * файлы usb-этапа, прикреплённые к adb/actions команды) одинаково, см.
     * apk_library.ensure_apks_downloaded — вызывается перед реальным
     * использованием отмеченных техником/прикреплённых в этапе путей
     * (модель больше не докачивается целиком при открытии, см.
     * mobile_bridge.sync_payload). */
    private fun ensureApksDownloaded(paths: List<String>) {
        if (paths.isEmpty()) return
        val pathsArr = JSONArray(paths)
        val resultJson = try {
            pyModule("mobile_bridge").callAttr(
                "ensure_apks_downloaded", apkDir, carsDir, BASE_URL, pathsArr.toString()
            ).toString()
        } catch (e: Exception) {
            return
        }
        val result = JSONObject(resultJson)
        val logLines = result.optJSONArray("log") ?: JSONArray()
        for (i in 0 until logLines.length()) pushAdbLog(logLines.getString(i))
    }

    /** cars/_shared/<folderName>/ целиком (см. mobile_bridge.sync_shared_folder_for)
     * — вызывается прямо перед записью usb-этапа с usb_shared_folder, а не
     * при открытии модели (см. usbRunStage). */
    private fun syncSharedFolder(folderName: String) {
        if (folderName.isBlank()) return
        val resultJson = try {
            pyModule("mobile_bridge").callAttr("sync_shared_folder_for", carsDir, BASE_URL, folderName).toString()
        } catch (e: Exception) {
            return
        }
        val result = JSONObject(resultJson)
        val logLines = result.optJSONArray("log") ?: JSONArray()
        for (i in 0 until logLines.length()) pushAdbLog(logLines.getString(i))
    }

    private fun onBusy() = pushAdbLog("Уже выполняется другая операция — дождись её завершения.")

    /** Ищет ADB-устройство среди подключённых по USB и устанавливает
     * соединение (CNXN+AUTH) — держится в AdbSession между этапами мастера. */
    private fun adbConnect() = runExclusive(::onBusy) {
        val result = AdbSession.connectBlocking(context, ::pushAdbLog)
        val event = when (result) {
            is AdbHandshakeResult.Connected ->
                JSONObject().put("connected", true).put("banner", result.bannerFromDevice)
            is AdbHandshakeResult.Failed ->
                JSONObject().put("connected", false).put("reason", result.reason)
        }
        pushEvent(JSONObject().put("kind", "adb_connect_result").put("result", event))
    }

    /** `adb connect host:port`-аналог (Wi-Fi ADB, см. AdbSession.
     * connectWifiBlocking) — для моделей с `wifi: true` в _wizard_spec.json
     * (см. cars/_shared/wifi_adb.py на desktop). Автопоиск IP (шлюз/скан
     * подсети) НЕ портирован (Windows-специфичный PowerShell) — техник
     * вводит адрес вручную (см. app.js: запрашивает перед подключением). */
    private fun adbConnectWifi(host: String, port: Int) = runExclusive(::onBusy) {
        val result = AdbSession.connectWifiBlocking(host, port, context, ::pushAdbLog)
        val event = when (result) {
            is AdbHandshakeResult.Connected ->
                JSONObject().put("connected", true).put("banner", result.bannerFromDevice)
            is AdbHandshakeResult.Failed ->
                JSONObject().put("connected", false).put("reason", result.reason)
        }
        pushEvent(JSONObject().put("kind", "adb_connect_result").put("result", event))
    }

    /** "telnet"-этап: включает ADB-отладку на магнитоле по telnet (см.
     * TelnetAdb.kt) — НЕ требует предварительного AdbSession.connect,
     * отдельное TCP-соединение на каждую команду. */
    private fun telnetRunStage(args: JSONObject) {
        val stageIndex = args.optInt("index", -1)
        val host = withIpv6Zone(args.getString("host"))
        val commandsArr = args.getJSONArray("commands")
        val commands = (0 until commandsArr.length()).map { commandsArr.getString(it) }
        runExclusive(::onBusy) {
            val result = try {
                installEngine().runTelnetCommands(host, commands)
            } catch (e: Exception) {
                StageRunResult.Failed((e.message ?: "неизвестная ошибка"))
            }
            pushStageResult(stageIndex, result)
        }
    }

    /** Ручной ввод произвольной shell-команды из развёрнутой карточки лога
     * (см. app.js log-overlay) — не часть DSL-этапа, просто удобство для
     * техника при диагностике на месте. Требует уже установленного
     * ADB-соединения (USB или Wi-Fi — без разницы, AdbSession сам решает). */
    private fun adbShellCommand(command: String) {
        val trimmed = command.trim()
        if (trimmed.isEmpty()) return
        runExclusive(::onBusy) {
            if (!AdbSession.isConnected) {
                pushAdbLog("ADB не подключён — команда не выполнена: $trimmed")
                return@runExclusive
            }
            pushAdbLog("\$ $trimmed")
            when (val r = AdbSession.shell(trimmed, ::pushAdbLog)) {
                is AdbShellResult.Output -> if (r.text.isNotBlank()) pushAdbLog(r.text.trim())
                is AdbShellResult.Rejected -> pushAdbLog("Команда отклонена устройством: ${r.reason}")
                is AdbShellResult.Failed -> pushAdbLog("Ошибка: ${r.reason}")
            }
        }
    }

    /** Список устройств в сети — НЕ фильтр по шаблону (открытый порт), а
     * реальный список того, что откликнулось (см. NetworkScan.pingSweep /
     * MdnsResolve.scanIpv6Neighbors); mDNS-резолв "android.local" (см.
     * MdnsResolve — реально используемое имя, подтвердил техник: "telnet
     * android.local" из Termux) даёт только РЕКОМЕНДОВАННЫЙ кандидат внутри
     * этого списка, промптHostPicker в app.js сам его подсвечивает. port==23
     * (telnet) — IPv6-соседи по mDNS (там же и zone id "%wlan0", если нужен,
     * см. MdnsResolve), иначе (Wi-Fi ADB) — IPv4 ping-скан + кто держит порт
     * открытым (объединение, а не пересечение — некоторые хосты фильтруют
     * ICMP, но отвечают на TCP, и наоборот). Не через runExclusive — не
     * трогает AdbSession/UsbFlashSession, можно спокойно гонять параллельно
     * с чем угодно ещё. */
    private fun scanHosts(port: Int) {
        Thread {
            val event = JSONObject().put("kind", "network_scan_result").put("port", port)
            if (NetworkScan.wifiNetwork(context) == null) {
                pushAdbLog("Скан сети: не нашёл активное Wi-Fi подключение (телефон подключён к сети магнитолы?).")
            }
            if (port == 23) {
                val ipv6 = try {
                    MdnsResolve.resolveAndroidLocal(context).ipv6
                } catch (e: Exception) {
                    pushAdbLog("Скан сети (android.local, IPv6): ${e.message ?: "неизвестная ошибка"}"); null
                }
                val neighbors = try {
                    MdnsResolve.scanIpv6Neighbors(context)
                } catch (e: Exception) {
                    pushAdbLog("Скан сети (IPv6-соседи): ${e.message ?: "неизвестная ошибка"}"); emptyList()
                }
                val hosts = (listOfNotNull(ipv6) + neighbors).distinct()
                pushAdbLog("Скан сети (telnet, IPv6): найдено ${hosts.size}.")
                event.put("hosts", JSONArray(hosts))
                if (ipv6 != null) event.put("recommended", ipv6)
            } else {
                val mdns = try {
                    MdnsResolve.resolveAndroidLocal(context)
                } catch (e: Exception) {
                    pushAdbLog("Скан сети (android.local, IPv4): ${e.message ?: "неизвестная ошибка"}"); MdnsResolve.Result(null, null)
                }
                val ping = try {
                    NetworkScan.pingSweep(context)
                } catch (e: Exception) {
                    pushAdbLog("Скан сети (ping): ${e.message ?: "неизвестная ошибка"}"); emptyList()
                }
                val portOpen = try {
                    NetworkScan.scanSubnetForPort(context, port)
                } catch (e: Exception) {
                    pushAdbLog("Скан сети (порт $port): ${e.message ?: "неизвестная ошибка"}"); emptyList()
                }
                val hosts = (listOfNotNull(mdns.ipv4) + ping + portOpen).distinct()
                pushAdbLog("Скан сети (Wi-Fi ADB, IPv4): найдено ${hosts.size}.")
                event.put("hosts", JSONArray(hosts))
                if (mdns.ipv4 != null) event.put("recommended", mdns.ipv4)
            }
            pushEvent(event)
        }.start()
    }

    /** Link-local IPv6 (fe80::...) без zone id ("%wlan0") сокет не
     * подключить — техник может вставить голый адрес прямо из вывода
     * "ping6 android.local" в Termux, не зная про зону (см.
     * MdnsResolve.kt — там она уже подставлена автоматически, это же —
     * подстраховка на случай ручного ввода). */
    private fun withIpv6Zone(host: String): String {
        val trimmed = host.trim()
        if (!trimmed.startsWith("fe80", ignoreCase = true) || "%" in trimmed) return trimmed
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? android.net.ConnectivityManager
        val wifiNetwork = NetworkScan.wifiNetwork(context) ?: return trimmed
        val ifaceName = cm?.getLinkProperties(wifiNetwork)?.interfaceName ?: return trimmed
        return "$trimmed%$ifaceName"
    }

    private fun installEngine() = InstallEngine(context, ::pushAdbLog) { requestId, prompt ->
        pushEvent(JSONObject().put("kind", "adb_ask_input").put("requestId", requestId).put("prompt", prompt))
    }

    /** Исполняет команды одного "adb"-этапа (args.commands — как вернул
     * wizard_spec.parse_commands, args.filesByName — basename -> локальный
     * путь) на фоновом потоке, стримит лог через adb_log, в конце шлёт
     * adb_stage_result. */
    private fun adbRunStage(args: JSONObject) {
        val stageIndex = args.optInt("index", -1)
        val commands = args.getJSONArray("commands")
        val filesByNameObj = args.optJSONObject("filesByName") ?: JSONObject()
        val filesByName = filesByNameObj.keys().asSequence().associateWith { filesByNameObj.getString(it) }
        runExclusive(::onBusy) {
            val result = try {
                if (!AdbSession.isConnected) {
                    StageRunResult.Failed("ADB не подключён — сначала подключись к устройству")
                } else {
                    // Прикреплённые к команде файлы (files/adb_N/... или
                    // files/actions_i_j/..., см. #push/#install) качаются
                    // точечно прямо здесь — модель больше не докачивается
                    // целиком при открытии (см. mobile_bridge.sync_payload).
                    ensureApksDownloaded(filesByName.values.toList())
                    installEngine().runAdbCommands(commands, filesByName)
                }
            } catch (e: Exception) {
                StageRunResult.Failed((e.message ?: "неизвестная ошибка"))
            }
            pushStageResult(stageIndex, result)
        }
    }

    /** Устанавливает список APK ("apps"-этап — обязательные + отмеченные
     * техником необязательные, JS сам считает итоговый список путей). */
    private fun adbInstallApks(args: JSONObject) {
        val stageIndex = args.optInt("index", -1)
        val pathsArr = args.getJSONArray("apkPaths")
        val paths = (0 until pathsArr.length()).map { pathsArr.getString(it) }
        runExclusive(::onBusy) {
            val result = try {
                if (!AdbSession.isConnected) {
                    StageRunResult.Failed("ADB не подключён — сначала подключись к устройству")
                } else {
                    ensureApksDownloaded(paths) // общая библиотека — байты качаются только на этом шаге
                    installEngine().installApks(paths)
                }
            } catch (e: Exception) {
                StageRunResult.Failed((e.message ?: "неизвестная ошибка"))
            }
            pushStageResult(stageIndex, result)
        }
    }

    /** Ищет USB mass storage устройство, запрашивает разрешение и монтирует
     * (БЕЗ форматирования — см. UsbFlashSession) — держится живым между
     * этапами мастера, как AdbSession для ADB. */
    private fun usbConnect() = runExclusive(::onBusy) {
        val result = UsbFlashSession.connectBlocking(context, ::pushAdbLog)
        val event = result.fold(
            onSuccess = { fs ->
                JSONObject().put("mounted", true)
                    .put("label", fs.volumeLabel ?: "")
                    .put("capacity", fs.capacity)
            },
            onFailure = { e -> JSONObject().put("mounted", false).put("reason", e.message) },
        )
        pushEvent(JSONObject().put("kind", "usb_connect_result").put("result", event))
    }

    /** Полное форматирование в FAT32 (см. UsbFlashFormat.kt) — ЗАТИРАЕТ
     * содержимое, отдельное явное действие техника, не часть автоматического
     * потока "usb"-этапа (см. UsbFlashSession.format). */
    private fun usbFormat(args: JSONObject) {
        val label = args.optString("label", "MAGICSQD")
        runExclusive(::onBusy) {
            val event = try {
                val capacity = UsbFlashSession.capacityBytes()
                UsbFlashSession.format(capacity, label, ::pushAdbLog).fold(
                    onSuccess = { JSONObject().put("success", true) },
                    onFailure = { e -> JSONObject().put("success", false).put("reason", e.message) },
                )
            } catch (e: Exception) {
                JSONObject().put("success", false).put("reason", e.message)
            }
            pushEvent(JSONObject().put("kind", "usb_format_result").put("result", event))
        }
    }

    /** Исполняет "usb"-этап: файлы модели (в корень флешки), опционально
     * общая папка cars/_shared/<sharedFolder>/ и выбранные техником
     * необязательные APK (в apksDest) — см. writeUsbStage. */
    private fun usbRunStage(args: JSONObject) {
        val stageIndex = args.optInt("index", -1)
        val filesArr = args.getJSONArray("files")
        val files = (0 until filesArr.length()).map { filesArr.getString(it) }
        val sharedFolder = args.optString("sharedFolder", "")
        val sharedFolderDir = if (sharedFolder.isNotBlank()) File(carsDir, "_shared/$sharedFolder") else null
        val selectedArr = args.optJSONArray("selectedApks") ?: JSONArray()
        val selectedApks = (0 until selectedArr.length()).map { selectedArr.getString(it) }
        val apksDest = args.optString("apksDest", "")
        runExclusive(::onBusy) {
            val result = try {
                if (!UsbFlashSession.isMounted) {
                    StageRunResult.Failed("Флешка не подключена — сначала подключись к ней")
                } else {
                    // Файлы этапа (files/usb_files/step_N/...) и отмеченные
                    // техником APK качаются точечно прямо здесь — модель
                    // больше не докачивается целиком при открытии (см.
                    // mobile_bridge.sync_payload).
                    ensureApksDownloaded(files + selectedApks)
                    if (sharedFolder.isNotBlank()) syncSharedFolder(sharedFolder)
                    writeUsbStage(files, sharedFolderDir, selectedApks, apksDest, ::pushAdbLog)
                }
            } catch (e: Exception) {
                StageRunResult.Failed((e.message ?: "неизвестная ошибка"))
            }
            pushStageResult(stageIndex, result)
        }
    }

    private fun pushStageResult(stageIndex: Int, result: StageRunResult) {
        val resultJson = when (result) {
            StageRunResult.Success -> JSONObject().put("success", true)
            is StageRunResult.Failed -> JSONObject().put("success", false).put("reason", result.reason)
        }
        pushEvent(JSONObject().put("kind", "adb_stage_result").put("index", stageIndex).put("result", resultJson))
    }
}
