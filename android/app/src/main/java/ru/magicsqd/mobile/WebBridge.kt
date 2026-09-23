package ru.magicsqd.mobile

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import android.webkit.JavascriptInterface
import android.webkit.WebView
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONArray
import org.json.JSONObject
import ru.magicsqd.mobile.usb.AdbConsoleFormat
import ru.magicsqd.mobile.usb.AdbEchoStats
import ru.magicsqd.mobile.usb.AdbHandshakeResult
import ru.magicsqd.mobile.usb.AdbPermissions
import ru.magicsqd.mobile.usb.AdbSession
import ru.magicsqd.mobile.usb.ApkOperationProgress
import ru.magicsqd.mobile.usb.AdbShellResult
import ru.magicsqd.mobile.usb.AskInputBroker
import ru.magicsqd.mobile.usb.InstallEngine
import ru.magicsqd.mobile.usb.MdnsResolve
import ru.magicsqd.mobile.usb.NetworkScan
import ru.magicsqd.mobile.usb.StageRunResult
import ru.magicsqd.mobile.usb.UsbFlashSession
import ru.magicsqd.mobile.usb.readQrAdbBugreportZip
import ru.magicsqd.mobile.usb.scanUsbStageItems
import ru.magicsqd.mobile.usb.writeQrAdbFlag
import ru.magicsqd.mobile.usb.writeQrAdbPrepFlag
import ru.magicsqd.mobile.usb.writeUsbStage
import java.io.File

/** Public methods are called synchronously by Chaquopy while reading HTTP chunks. */
class ApkDownloadProgress(private val emit: (String, Long, Long) -> Unit,
                          private val cancelled: () -> Boolean) {
    fun update(path: String, done: Long, total: Long) = emit(path, done, total)
    fun checkCancelled() {
        if (cancelled()) error("Очередь остановлена пользователем")
    }
}

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
        // ИИ-чат (см. chat_bridge.py, server/backend.py: POST /chat) — тот же
        // сервер/ключ, что и в submit.json на десктопе (X-Submit-Key — не
        // настоящий секрет, анти-спам заглушка, лежит в каждом установленном
        // клиенте, см. server/backend.py докстринг).
        private const val CHAT_URL = "https://magicsqd.ru/chat"
        private const val CHAT_KEY = "61e6801f05e4c1a86d9e7175bfd64b1b3d02fa35d20618b5"
        // Аккаунт техника (см. auth_bridge.py, server/backend.py: протокол
        // /auth/*) — голый хост, auth_bridge.py сам достраивает конкретные
        // пути. Отдельно от BASE_URL/CHAT_URL просто для читаемости.
        private const val AUTH_BASE_URL = "https://magicsqd.ru"
        // Лог одной попытки установки (см. installLogSend, server/backend.py:
        // POST /install_log) — тот же ключ-заглушка от спама, что и у чата,
        // не отдельный секрет (см. комментарий про CHAT_KEY выше).
        private const val INSTALL_LOG_URL = "https://magicsqd.ru/install_log"
        // «Сообщить о проблеме» (см. reportSend, server/backend.py: POST /report) — тот же ключ-заглушка.
        private const val REPORT_URL = "https://magicsqd.ru/report"
        // Пульс (см. startHeartbeat, ping_bridge.py, desktop app/ping_client.py) — счётчик пользователей и
        // распределение по версиям в админке; тот же интервал, что на desktop (sync_api.PING_INTERVAL_SECONDS).
        private const val PING_URL = "https://magicsqd.ru/ping"
        private const val PING_INTERVAL_MS = 3L * 60 * 1000
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

    /** Устанавливается из MainActivity сразу после создания моста — сам
     * ActivityResultLauncher должен быть зарегистрирован в Activity ДО
     * onStart (см. registerForActivityResult), поэтому мост не может завести
     * его сам, только запросить у Activity через эту функцию (см.
     * pickPersonalApks/onApksPicked ниже — "Добавить свой APK..." на
     * apps/usb-этапах, тот же порт desktop stage_wizard.js:pickPersonalApks,
     * но там native-диалог pywebview, здесь — Storage Access Framework). */
    var pickApksLauncher: ((Array<String>) -> Unit)? = null

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
        // Если техник уже входил раньше — сразу подтягиваем его заявки на
        // модерации в фоне, без отдельного действия (см. app/web/api/
        // auth_api.py:status на desktop — та же идея, тут ещё проще: сама
        // cookie либо ещё валидна 30 дней, либо auth_bridge.py тихо
        // вернёт ok:false, и JS просто ничего не покажет).
        authSyncMyCars()
        startHeartbeat()
        startInstallLogRecovery()
    }

    /** Пульс раз в PING_INTERVAL_MS, пока процесс жив: client_id, версия, platform=android. Раньше Android
     * не пинговал вовсе — в админке «Новые установки» считались только компьютеры. Ошибки сети глотаются. */
    private fun startHeartbeat() {
        val version = try {
            context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: ""
        } catch (_: Exception) { "" }
        Thread {
            while (true) {
                try {
                    pyModule("ping_bridge").callAttr("send_ping", getOrCreateClientId(), version, PING_URL, CHAT_KEY)
                } catch (_: Exception) {
                    // пульс необязателен
                }
                try { Thread.sleep(PING_INTERVAL_MS) } catch (_: InterruptedException) { return@Thread }
            }
        }.apply { isDaemon = true; name = "magicsqd-ping" }.start()
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
                // Google Play/RuStore её знали (см. аудит проекта). client_id —
                // тот же id, что уходит в install-логи (getOrCreateClientId) —
                // чтобы диагностическая шапка лога установки (см. app.js:
                // openModel) могла показать и версию, и id одним вызовом, как
                // на десктопе (app/web/bridge.py: app_get_info).
                "app_version" -> JSONObject()
                    .put("version", context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: "?")
                    .put("client_id", getOrCreateClientId())
                    .toString()
                "settings_info" -> settingsInfo().toString()
                "settings_preferences" -> settingsPreferences().toString()
                "settings_clear_cache" -> clearCache().toString()
                "settings_set_preferences" -> setSettingsPreferences(args).toString()
                "settings_sync_now" -> { startSync(); "{}" }
                "start_sync" -> { startSync(); "{}" }
                "app_update_check" -> { startUpdateCheck(); "{}" }
                "supporters_load" -> { startSupportersLoad(); "{}" }
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
                "lab_apk_icon" -> { loadLabApkIcon(args.optString("path")); "{}" }
                "install_load_stages" -> pyModule("mobile_bridge").callAttr(
                    "load_install_stages", args.getString("model_key"), context.filesDir.absolutePath
                ).toString()
                "adb_is_connected" -> JSONObject().put("connected", AdbSession.isConnected).toString()
                "adb_connect" -> { adbConnect(); "{}" }
                "adb_connect_wifi" -> { adbConnectWifi(args.getString("host"), args.optInt("port", 5555)); "{}" }
                "adb_disconnect" -> { AdbSession.disconnect(); "{}" }
                "adb_run_stage" -> { adbRunStage(args); "{}" }
                "adb_prefetch_files" -> { adbPrefetchFiles(args); "{}" }
                "adb_install_apks" -> { adbInstallApks(args); "{}" }
                "adb_download_apks" -> { adbDownloadApks(args); "{}" }
                "adb_cancel_install" -> { labCancelInstall = true; pushAdbLog("Остановка очереди после текущего приложения…"); "{}" }
                "telnet_run_stage" -> { telnetRunStage(args); "{}" }
                "adb_shell_command" -> { adbShellCommand(args.getString("command")); "{}" }
                "chat_send" -> {
                    chatSend(args.getString("history"), args.getString("recent_log"), args.optString("provider", ""))
                    "{}"
                }
                "chat_confirm_command" -> { chatConfirmCommand(args.getString("command")); "{}" }
                "report_send" -> {
                    reportSend(
                        args.optString("brand", ""), args.optString("model", ""),
                        args.getString("reason"), args.optString("description", ""),
                    )
                    "{}"
                }
                "install_log_send" -> {
                    installLogSend(
                        args.getString("brand"), args.getString("model"), args.optString("modification", ""),
                        args.getBoolean("success"), args.getString("log"),
                    )
                    "{}"
                }
                // Прочный журнал сессии на диске (см. InstallLogQueue.kt) —
                // переживает и обрыв сети (Wi-Fi ADB — телефон подключён к
                // магнитоле, без интернета), и вылет процесса. session_start
                // зовётся из app.js:openWizard() на каждое открытие модели;
                // append — на каждую строку, которую видит техник в панели.
                "install_log_session_start" -> {
                    InstallLogQueue.startSession(
                        context.filesDir, args.optString("brand", ""), args.optString("model", ""),
                        args.optString("modification", ""),
                    )
                    "{}"
                }
                "install_log_append" -> {
                    // Синхронно, прямо здесь — НЕ в фоновом потоке: порядок
                    // вызовов из JS гарантирован только пока это так (см.
                    // InstallLogQueue.kt докстринг про однопоточность JS и
                    // синхронность Bridge.call()).
                    InstallLogQueue.appendCurrent(
                        context.filesDir, args.optString("line", ""), args.optBoolean("activity", false),
                    )
                    "{}"
                }
                // Необработанные JS-ошибки (см. bridge.js: window.onerror/
                // unhandledrejection) — на Android такого перехвата не было
                // вообще, поэтому падение на чистом JS (не дошедшее до
                // нативного Kotlin-кода) сегодня не оставляет никакого следа,
                // даже локально.
                "client_log_error" -> {
                    clientLogError(args.optString("message", ""), args.optString("stack", ""))
                    "{}"
                }
                "auth_status" -> authStatus().toString()
                "auth_register" -> { authRegister(args.getString("email"), args.getString("password")); "{}" }
                "auth_login" -> { authLogin(args.getString("email"), args.getString("password")); "{}" }
                "auth_logout" -> { authLogout(); "{}" }
                "auth_refresh_subscriber" -> { authRefreshSubscriber(); "{}" }
                "auth_forgot_password" -> { authForgotPassword(args.getString("email")); "{}" }
                "scan_hosts" -> { scanHosts(args.optInt("port", 5555)); "{}" }
                "scan_adb_service" -> { scanAdbService(); "{}" }
                "adb_ask_input_response" -> {
                    AskInputBroker.resolve(args.getString("requestId"), args.getString("value"))
                    "{}"
                }
                "usb_is_mounted" -> JSONObject().put("mounted", UsbFlashSession.isMounted).toString()
                "usb_connect" -> { usbConnect(); "{}" }
                "usb_disconnect" -> { UsbFlashSession.disconnect(); "{}" }
                "usb_format" -> { usbFormat(args); "{}" }
                "usb_list_items" -> usbListItems(args)
                "usb_run_stage" -> { usbRunStage(args); "{}" }
                "qr_adb_write_prep_flag" -> { qrAdbWritePrepFlag(); "{}" }
                "qr_adb_write_flag" -> { qrAdbWriteFlag(); "{}" }
                "qr_adb_get_password" -> { qrAdbGetPassword(); "{}" }
                // "actions"-этап с kind=grant_permissions/mock_location (см.
                // app/car_generator.py: ActionSpec.kind) — техник выбирает
                // установленное приложение (ask_choice на desktop), дальше
                // AdbPermissions.kt (портовая копия cars/_shared/
                // adb_permissions.py). disable_app/enable_app пока не
                // портированы — там же в app.js остаётся "не поддерживается".
                "actions_list_packages" -> { actionsListPackages(args.optBoolean("thirdPartyOnly", true)); "{}" }
                "actions_grant_permissions" -> { actionsGrantPermissions(args.getString("pkg")); "{}" }
                "actions_mock_location" -> { actionsMockLocation(args.getString("pkg")); "{}" }
                "actions_launch_activity" -> { actionsLaunchActivity(args.getString("pkg")); "{}" }
                "actions_uninstall_app" -> { actionsUninstallApp(args.getString("pkg")); "{}" }
                "actions_disable_app" -> { actionsDisableApp(args.getString("pkg")); "{}" }
                "actions_enable_app" -> { actionsEnableApp(args.getString("pkg")); "{}" }
                "pick_personal_apks" -> { pickPersonalApks(); "{}" }
                // Видео-кнопка нав-бара мастера (см. app.js: playStageVideo) —
                // тот же общий "докачай, чего нет" хелпер, что и перед
                // adb_install_apks/usb_run_stage (см. ensureApksDownloaded),
                // но АСИНХРОННО (см. syncModelPayload выше) — файл до 150 МБ
                // (см. instruction_html.MAX_VIDEO_BYTES на desktop), Bridge.call
                // синхронный и заблокировал бы JS-поток на всё время скачивания.
                "ensure_video_downloaded" -> { ensureVideoDownloaded(args.getString("path")); "{}" }
                else -> JSONObject().put("error", "Неизвестный метод: $method").toString()
            }
        } catch (e: Exception) {
            JSONObject().put("error", (e.message ?: "неизвестная ошибка")).toString()
        }
    }

    private fun pyModule(name: String) = Python.getInstance().getModule(name)

    // -- Аккаунт техника (см. auth_bridge.py, server/backend.py: /auth/*) --
    // Отдельный SharedPreferences-файл (не "settings" — это про UI-настройки,
    // а не про сессию), только email + уже выданный сервером session-токен
    // (НЕ пароль — логаут на сервере/истечение делают его бесполезным).
    private fun authPrefs() = context.getSharedPreferences("auth", Context.MODE_PRIVATE)
    private fun authEmail(): String? = authPrefs().getString("email", null)
    private fun authUserCookie(): String? = authPrefs().getString("user_cookie", null)
    private fun saveAuthSession(email: String, userCookie: String) {
        authPrefs().edit().putString("email", email).putString("user_cookie", userCookie).apply()
    }
    private fun clearAuthSession() { authPrefs().edit().clear().apply() }

    private fun authStatus(): JSONObject =
        JSONObject().put("email", authEmail()).put("subscriber", authPrefs().getBoolean("subscriber", false))

    /** Свежий статус «подписчик Boosty» (владелец ставит его вручную и он может меняться) — для цвета
     * значка аккаунта. Результат — событием auth_subscriber_result; при сбое связи остаётся прежний. */
    private fun authRefreshSubscriber() {
        val cookie = authUserCookie() ?: return
        Thread {
            try {
                val obj = JSONObject(pyModule("auth_bridge").callAttr("me", AUTH_BASE_URL, cookie).toString())
                if (obj.optBoolean("ok")) {
                    val subscriber = obj.optBoolean("subscriber", false)
                    authPrefs().edit().putBoolean("subscriber", subscriber).apply()
                    pushEvent(JSONObject().put("kind", "auth_subscriber_result").put("subscriber", subscriber))
                }
            } catch (e: Exception) { /* нет связи — остаётся прежний цвет */ }
        }.start()
    }

    private fun authRegister(email: String, password: String) {
        Thread {
            val resultJson = try {
                pyModule("auth_bridge").callAttr("register", AUTH_BASE_URL, email, password).toString()
            } catch (e: Exception) {
                JSONObject().put("ok", false).put("error", (e.message ?: "неизвестная ошибка")).toString()
            }
            pushEvent(JSONObject().put("kind", "auth_register_result").put("result", JSONObject(resultJson)))
        }.start()
    }

    private fun authLogin(email: String, password: String) {
        Thread {
            val resultJson = try {
                pyModule("auth_bridge").callAttr("login", AUTH_BASE_URL, email, password).toString()
            } catch (e: Exception) {
                JSONObject().put("ok", false).put("error", (e.message ?: "неизвестная ошибка")).toString()
            }
            val result = JSONObject(resultJson)
            if (result.optBoolean("ok")) {
                saveAuthSession(result.getString("email"), result.getString("user_cookie"))
                authPrefs().edit().putBoolean("subscriber", result.optBoolean("subscriber", false)).apply()
            }
            pushEvent(JSONObject().put("kind", "auth_login_result").put("result", result))
            if (result.optBoolean("ok")) authSyncMyCars()
        }.start()
    }

    private fun authForgotPassword(email: String) {
        Thread {
            val resultJson = try {
                pyModule("auth_bridge").callAttr("forgot_password", AUTH_BASE_URL, email).toString()
            } catch (e: Exception) {
                JSONObject().put("ok", false).put("error", (e.message ?: "неизвестная ошибка")).toString()
            }
            pushEvent(JSONObject().put("kind", "auth_forgot_password_result").put("result", JSONObject(resultJson)))
        }.start()
    }

    private fun authLogout() {
        val cookie = authUserCookie()
        clearAuthSession()
        Thread {
            if (cookie != null) {
                try { pyModule("auth_bridge").callAttr("logout", AUTH_BASE_URL, cookie) } catch (e: Exception) { /* локально уже вышли — не критично */ }
            }
            pushEvent(JSONObject().put("kind", "auth_logout_result").put("result", JSONObject().put("ok", true)))
        }.start()
    }

    /** Тянет+распаковывает свои заявки на модерации прямо в cars/<Марка>/
     * <Модель>/ (см. auth_bridge.py:sync_my_cars) — вызывается и при старте
     * (см. init выше, если сессия уже была), и сразу после успешного входа. */
    private fun authSyncMyCars() {
        val cookie = authUserCookie() ?: return
        Thread {
            val resultJson = try {
                pyModule("auth_bridge").callAttr("sync_my_cars", AUTH_BASE_URL, cookie, carsDir).toString()
            } catch (e: Exception) {
                JSONObject().put("ok", false).put("error", (e.message ?: "неизвестная ошибка")).toString()
            }
            pushEvent(JSONObject().put("kind", "auth_sync_finished").put("result", JSONObject(resultJson)))
        }.start()
    }

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
            .put("chat_enabled", prefs.getBoolean("chat_enabled", true))
    }

    private fun setSettingsPreferences(args: JSONObject): JSONObject {
        val prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE)
        val editor = prefs.edit()
        listOf("auto_sync", "reduced_motion", "compact_log", "keep_screen_on", "chat_enabled").forEach { key ->
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

    /** Список «Спасибо вам» для окна «Всё готово» (см. mobile_bridge.supporters_fetch) — в фоне,
     * сбой сети молча даёт пустой ответ (приложение покажет ранее сохранённый список). */
    private fun startSupportersLoad() {
        Thread {
            val resultJson = try {
                pyModule("mobile_bridge").callAttr("supporters_fetch", BASE_URL).toString()
            } catch (e: Exception) {
                "{}"
            }
            pushEvent(JSONObject().put("kind", "supporters_result").put("result", JSONObject(resultJson)))
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
    private val labIconExecutor = java.util.concurrent.Executors.newSingleThreadExecutor()
    private val labIconCache = android.util.LruCache<String, String>(160)
    private var labServerIcons = JSONObject()
    private var labServerIconsUntil = 0L

    private fun serverApkIcon(path: String): String? {
        return try {
            val root = context.filesDir.canonicalFile.toPath()
            val file = File(path).canonicalFile.toPath()
            if (!file.startsWith(root)) return null
            val relative = root.relativize(file).toString().replace('\\', '/')
            if (!relative.startsWith("apk/") && !relative.startsWith("cars/")) return null
            if (System.currentTimeMillis() > labServerIconsUntil) {
                val connection = java.net.URL("$BASE_URL/manifest.json").openConnection()
                connection.connectTimeout = 8000
                connection.readTimeout = 8000
                val manifest = connection.getInputStream().bufferedReader().use { JSONObject(it.readText()) }
                labServerIcons = manifest.optJSONObject("apk_icons") ?: JSONObject()
                labServerIconsUntil = System.currentTimeMillis() + 300000
            }
            val icon = labServerIcons.optString(relative)
            if (Regex("icons/[0-9a-f]{64}\\.png").matches(icon)) "$BASE_URL/$icon" else null
        } catch (_: Exception) {
            labServerIconsUntil = System.currentTimeMillis() + 20000
            null
        }
    }

    private fun loadLabApkIcon(path: String) {
        labIconExecutor.execute {
            val file = File(path)
            val key = "$path:${file.length()}:${file.lastModified()}"
            val icon = try {
                serverApkIcon(path) ?: labIconCache.get(key) ?: run {
                    if (!file.isFile || file.extension.lowercase() != "apk") return@run null
                    val manager = context.packageManager
                    val info = manager.getPackageArchiveInfo(path, 0)?.applicationInfo ?: return@run null
                    info.sourceDir = path
                    info.publicSourceDir = path
                    val drawable = info.loadIcon(manager)
                    val bitmap = android.graphics.Bitmap.createBitmap(96, 96, android.graphics.Bitmap.Config.ARGB_8888)
                    val canvas = android.graphics.Canvas(bitmap)
                    drawable.setBounds(0, 0, 96, 96)
                    drawable.draw(canvas)
                    val output = java.io.ByteArrayOutputStream()
                    bitmap.compress(android.graphics.Bitmap.CompressFormat.PNG, 100, output)
                    bitmap.recycle()
                    val value = "data:image/png;base64," + android.util.Base64.encodeToString(output.toByteArray(), android.util.Base64.NO_WRAP)
                    labIconCache.put(key, value)
                    value
                }
            } catch (_: Exception) { null }
            pushEvent(JSONObject().put("kind", "apk_icon").put("path", path).put("icon", icon ?: JSONObject.NULL))
        }
    }

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
    private fun ensureApksDownloaded(paths: List<String>, progress: ApkDownloadProgress? = null) {
        if (paths.isEmpty()) return
        val pathsArr = JSONArray(paths)
        val resultJson = try {
            pyModule("mobile_bridge").callAttr(
                "ensure_apks_downloaded", apkDir, carsDir, BASE_URL, pathsArr.toString(), progress
            ).toString()
        } catch (e: Exception) {
            if (progress != null) throw e
            return
        }
        val result = JSONObject(resultJson)
        val logLines = result.optJSONArray("log") ?: JSONArray()
        for (i in 0 until logLines.length()) pushAdbLog(logLines.getString(i))
    }

    /** Докачивает один файл видео-кнопки нав-бара (см. wizard_spec.py:
     * video_file/video_url), если его ещё нет на диске, в фоновом потоке —
     * "video_ready" сообщает JS, что можно переходить на video_url (см.
     * ensureApksDownloaded/apk_library.ensure_apks_downloaded, тот же общий
     * резолвер путей apk_dir/cars_dir, что и для APK). */
    private fun ensureVideoDownloaded(path: String) {
        Thread {
            ensureApksDownloaded(listOf(path))
            pushEvent(JSONObject().put("kind", "video_ready").put("path", path))
        }.start()
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
                    .put("no_device", result.noDevice)
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

    /** Ход переписки с ИИ-чатом (см. assets/js/app.js — рендерится прямо в
     * логе, как и мини-консоль) — сервер (Gemini/Groq) отвечает текстом или
     * предлагает ОДНУ shell-команду; техник подтверждает её перед выполнением
     * (см. chatConfirmCommand). historyJson/recentLogJson уже сериализованы
     * JS-стороной — Chaquopy строкам доверяет проще, чем вложенным объектам. */
    /** Персистентный случайный id (тот же смысл, что и get_or_create_client_id
     * на десктопе, см. app/ping_client.py) — в отличие от desktop, на Android
     * ничего подобного раньше не было вовсе (chat_bridge.py всегда слал
     * client_id="" — см. историю), заводим здесь то же самое хранилище, где
     * уже лежит keep_screen_on. */
    private fun getOrCreateClientId(): String {
        val prefs = context.getSharedPreferences("settings", Context.MODE_PRIVATE)
        prefs.getString("client_id", null)?.let { return it }
        val id = java.util.UUID.randomUUID().toString()
        prefs.edit().putString("client_id", id).apply()
        return id
    }

    /** Лог одной попытки установки (см. server/backend.py: POST
     * /install_log) — вызывается из app.js по завершении/уходу из мастера,
     * только если лог не пустой (была реальная активность). Best-effort,
     * фоновым потоком — ошибка сети тут не должна ничего показывать
     * технику, поэтому результат никуда не пробрасывается событием.
     *
     * Запечатываем в прочную очередь на диске (см. InstallLogQueue.kt)
     * ДО попытки отправки — сбой сети (Wi-Fi ADB — телефон подключён к
     * магнитоле, без интернета) или вылет процесса после этого момента
     * больше не теряет лог целиком, только откладывает его до следующего
     * запуска (см. init{}/startInstallLogRecovery). drainQueue, а не
     * попытка отправить только эту одну запись — заодно опустошает и то,
     * что накопилось раньше, даром: этот поток и так фоновый. */
    private fun installLogSend(brand: String, model: String, modification: String,
                                success: Boolean, logText: String) {
        InstallLogQueue.finalizeToQueue(context.filesDir, "android", brand, model, modification, success, logText)
        Thread {
            InstallLogQueue.drainQueue(context.filesDir, ::sendInstallLogViaChaquopy)
        }.start()
    }

    /** Сама отправка одной записи (см. InstallLogQueue.kt — принимает эту
     * функцию как параметр, чтобы не знать про Chaquopy вообще) — тонкая
     * обёртка над install_log_bridge.send_install_log, платформа внутри него
     * жёстко "android" (первый параметр здесь просто для единообразия сигнатуры
     * с desktop-версией, install_log_bridge его не принимает — не нужен). */
    private fun sendInstallLogViaChaquopy(platform: String, brand: String, model: String, modification: String,
                                           success: Boolean, logText: String): String {
        return try {
            pyModule("install_log_bridge").callAttr(
                "send_install_log", brand, model, modification, success, logText,
                getOrCreateClientId(), INSTALL_LOG_URL, CHAT_KEY,
            ).toString()
        } catch (_: Exception) {
            "{\"ok\":false}"
        }
    }

    /** Необработанные JS-ошибки (см. bridge.js) — пишем в локальный
     * js_errors.log ВСЕГДА (не только при активной сессии установки — как и
     * на desktop, см. app/web/bridge.py:client_log_error), и ДОПОЛНИТЕЛЬНО, с
     * явным маркером, в прочный журнал ТЕКУЩЕЙ сессии, если она сейчас есть
     * (см. InstallLogQueue.appendCurrent) — иначе падение на чистом JS прямо
     * во время установки осталось бы только в локальном файле, недоступном
     * обычному технику, вместо того чтобы уйти на сервер вместе с остальным
     * логом сессии. Если сессии сейчас нет — appendCurrent создаст файл без
     * meta, который просто тихо уберётся следующим startSession/recoverStaleCurrent
     * (см. их докстринги) — ничего не отправится, но и не сломается. */
    private fun clientLogError(message: String, stack: String) {
        try {
            val file = java.io.File(context.filesDir, "js_errors.log")
            val timestamp = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US).format(java.util.Date())
            file.appendText("$timestamp $message\n")
            if (stack.isNotEmpty()) file.appendText("$stack\n")
            file.appendText("---\n")
        } catch (_: Exception) {
        }
        InstallLogQueue.appendCurrent(
            context.filesDir,
            "${InstallLogQueue.CRASH_MARKER}\n$message" + (if (stack.isNotEmpty()) "\n$stack" else ""),
            true,
        )
    }

    /** Вызывается ОДИН раз при старте (см. init{}) — если прошлый запуск не
     * дошёл до штатного installLogSend (вылет процесса/принудительное
     * закрытие) или не смог отправить лог (офлайн), досылаем/дозапоминаем его
     * именно сейчас. Фоновым daemon-потоком — не должно задерживать запуск. */
    private fun startInstallLogRecovery() {
        Thread {
            InstallLogQueue.recoverAndDrainAtStartup(context.filesDir, "android", ::sendInstallLogViaChaquopy)
        }.apply { isDaemon = true; name = "magicsqd-install-log-recovery" }.start()
    }

    /** Обращение («Сообщить о проблеме»): brand/model пустые — к работе приложения в целом. Результат уходит
     * событием report_result — в отличие от install_log_send, техник ждёт ответа в окне. */
    private fun reportSend(brand: String, model: String, reason: String, description: String) {
        val appVersion = try {
            context.packageManager.getPackageInfo(context.packageName, 0).versionName ?: ""
        } catch (_: Exception) { "" }
        Thread {
            val resultJson = try {
                pyModule("report_bridge").callAttr(
                    "send_report", brand, model, reason, description, appVersion,
                    getOrCreateClientId(), REPORT_URL, CHAT_KEY,
                ).toString()
            } catch (e: Exception) {
                JSONObject().put("ok", false).put("error", (e.message ?: "неизвестная ошибка")).toString()
            }
            pushEvent(JSONObject().put("kind", "report_result").put("result", JSONObject(resultJson)))
        }.start()
    }

    private fun chatSend(historyJson: String, recentLogJson: String, provider: String = "") {
        Thread {
            val resultJson = try {
                pyModule("chat_bridge").callAttr(
                    "send_chat_turn", historyJson, recentLogJson, CHAT_URL, CHAT_KEY, provider
                ).toString()
            } catch (e: Exception) {
                JSONObject().put("ok", false).put("error", (e.message ?: "неизвестная ошибка")).toString()
            }
            pushEvent(JSONObject().put("kind", "chat_reply").put("reply", JSONObject(resultJson)))
        }.start()
    }

    /** Техник подтвердил команду, предложенную ИИ-чатом — выполняется тем же
     * путём, что и свободный ввод в мини-консоли (adbShellCommand), результат
     * же идёт отдельным событием "chat_command_result" в ленту чата, а не
     * только в общий лог. */
    private fun chatConfirmCommand(command: String) {
        val trimmed = AdbConsoleFormat.stripRedundantPrefix(command)
        if (trimmed.isEmpty()) return
        if (!AdbSession.isConnected) {
            pushAdbLog("ADB не подключён — команда не выполнена: $trimmed")
            pushEvent(JSONObject().put("kind", "chat_command_result")
                .put("command", trimmed).put("output", "ADB не подключён").put("ok", false))
            return
        }
        Thread {
            val (text, ok) = when (val r = AdbSession.shell(trimmed, ::pushAdbLog)) {
                is AdbShellResult.Output -> {
                    val out = r.text.trim()
                    (AdbConsoleFormat.translateError(out) ?: AdbConsoleFormat.formatOutput(trimmed, out) ?: out) to true
                }
                is AdbShellResult.Rejected ->
                    (AdbConsoleFormat.translateError(r.reason) ?: "Команда отклонена устройством: ${r.reason}") to false
                is AdbShellResult.Failed ->
                    (AdbConsoleFormat.translateError(r.reason) ?: "Ошибка: ${r.reason}") to false
            }
            pushEvent(JSONObject().put("kind", "chat_command_result")
                .put("command", trimmed).put("output", text).put("ok", ok))
        }.start()
    }

    /** Ручной ввод произвольной shell-команды из развёрнутой карточки лога
     * (см. app.js log-overlay) — не часть DSL-этапа, просто удобство для
     * техника при диагностике на месте. Требует уже установленного
     * ADB-соединения (USB или Wi-Fi — без разницы, AdbSession сам решает). */
    private fun adbShellCommand(command: String) {
        // Эхо введённой команды теперь красиво показывает сама JS-сторона
        // ДО вызова этого моста (см. app.js: onLogCmdRun/logConsoleCommand,
        // тот же приём, что и в desktop-версии) — раньше здесь же дублировался
        // сырой "$ команда", это привело бы к двойному эху.
        val trimmed = AdbConsoleFormat.stripRedundantPrefix(command)
        if (trimmed.isEmpty()) return
        runExclusive(::onBusy) {
            if (!AdbSession.isConnected) {
                pushAdbLog("ADB не подключён — команда не выполнена: $trimmed")
                return@runExclusive
            }
            when (val r = AdbSession.shell(trimmed, ::pushAdbLog)) {
                is AdbShellResult.Output -> if (r.text.isNotBlank()) {
                    val text = r.text.trim()
                    pushAdbLog(AdbConsoleFormat.translateError(text)
                        ?: AdbConsoleFormat.formatOutput(trimmed, text)
                        ?: text)
                }
                is AdbShellResult.Rejected -> pushAdbLog(
                    AdbConsoleFormat.translateError(r.reason) ?: "Команда отклонена устройством: ${r.reason}")
                is AdbShellResult.Failed -> pushAdbLog(
                    AdbConsoleFormat.translateError(r.reason) ?: "Ошибка: ${r.reason}")
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

    /**
     * Ищет актуальный порт "Беспроводной отладки" по mDNS (см.
     * MdnsResolve.resolveAdbTlsConnectEndpoints) — отдельно от scanHosts
     * выше, т.к. не привязан к конкретному "предполагаемому" порту вообще
     * (в отличие от scanSubnetForPort, который проверяет ОДИН заданный
     * порт на всех хостах). Найденные тут host:port подсвечиваются в
     * promptHostPicker отдельно от обычного списка хостов — с уже готовым
     * своим портом, ничего вводить руками не нужно (тот самый "bugjaeger
     * умеет сам перебирать динамические порты" — только не перебором, а
     * через официальный mDNS-анонс этой службы). Не через runExclusive —
     * та же причина, что и у scanHosts.
     */
    private fun scanAdbService() {
        Thread {
            val event = JSONObject().put("kind", "adb_service_scan_result")
            val endpoints = try {
                MdnsResolve.resolveAdbTlsConnectEndpoints(context)
            } catch (e: Exception) {
                pushAdbLog("Скан сети (Беспроводная отладка, mDNS): ${e.message ?: "неизвестная ошибка"}")
                emptyList()
            }
            pushAdbLog("Скан сети (Беспроводная отладка, mDNS): найдено ${endpoints.size}.")
            event.put("endpoints", JSONArray(endpoints.map {
                JSONObject().put("host", it.host).put("port", it.port)
            }))
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
        // Wi-Fi: файлы уже скачаны заранее (adbPrefetchFiles) — в сети магнитолы интернета нет, сверка с
        // сервером только ждала бы таймауты.
        val skipDownload = args.optBoolean("skipDownload", false)
        runExclusive(::onBusy) {
            val result = try {
                if (!AdbSession.isConnected) {
                    StageRunResult.Failed("ADB не подключён — сначала подключись к устройству")
                } else {
                    // Прикреплённые к команде файлы (files/adb_N/... или
                    // files/actions_i_j/..., см. #push/#install) качаются
                    // точечно прямо здесь — модель больше не докачивается
                    // целиком при открытии (см. mobile_bridge.sync_payload).
                    if (!skipDownload) ensureApksDownloaded(filesByName.values.toList())
                    installEngine().runAdbCommands(commands, filesByName)
                }
            } catch (e: Exception) {
                StageRunResult.Failed((e.message ?: "неизвестная ошибка"))
            }
            pushStageResult(stageIndex, result)
        }
    }

    /** Wi-Fi: заранее (при показе этапа adb/actions) докачать прикреплённые к нему файлы — при запуске телефон
     * уже в сети магнитолы без интернета. Результат — событие files_prefetched; ошибки не показываются
     * (при запуске обычный путь докачает, что сможет). */
    private fun adbPrefetchFiles(args: JSONObject) {
        val stageIndex = args.optInt("index", -1)
        val arr = args.optJSONArray("paths") ?: JSONArray()
        val paths = (0 until arr.length()).map { arr.getString(it) }
        if (paths.isEmpty()) return
        Thread {
            val ok = try {
                ensureApksDownloaded(paths)
                paths.all { File(it).exists() }
            } catch (_: Exception) { false }
            pushEvent(JSONObject().put("kind", "files_prefetched").put("index", stageIndex).put("ok", ok).put("paths", JSONArray(paths)))
        }.apply { isDaemon = true }.start()
    }

    /** Устанавливает список APK ("apps"-этап — обязательные + отмеченные
     * техником необязательные, JS сам считает итоговый список путей). */
    @Volatile private var labCancelInstall = false
    private fun adbInstallApks(args: JSONObject) {
        val stageIndex = args.optInt("index", -1)
        val pathsArr = args.getJSONArray("apkPaths")
        val paths = (0 until pathsArr.length()).map { pathsArr.getString(it) }
        val preferredMethod = args.optString("appsInstallMethod", "")
        val modelDir = File(args.getString("modelKey"))
        // Путь единственного выбранного GPS-приложения с пометкой «выдавать
        // фиктивное местоположение» (JS передаёт только когда такое ровно одно).
        val mockLocationPath = args.optString("mockLocationPath", "").ifEmpty { null }
        // Wi-Fi ADB: файлы уже скачаны отдельно (adbDownloadApks) — у телефона в сети магнитолы интернета
        // обычно нет, повторная сверка с сервером только ждала бы таймаут.
        val skipDownload = args.optBoolean("skipDownload", false)
        labCancelInstall = false
        AdbEchoStats.reset()
        runExclusive({ pushStageResult(stageIndex, StageRunResult.Failed("Другая операция ещё выполняется")) }) {
            val result = try {
                if (!AdbSession.isConnected) {
                    StageRunResult.Failed("ADB не подключён — сначала подключись к устройству")
                } else {
                    if (!skipDownload) ensureApksDownloaded(paths, ApkDownloadProgress({ path, done, total ->
                        pushApkProgress(stageIndex, path, 0, paths.size, "running", ApkOperationProgress("download", done, total))
                    }, { labCancelInstall }))
                    installEngine().installApksWithProgress(paths, preferredMethod, modelDir, { labCancelInstall },
                        onProgress = { path, completed, total, state ->
                            pushApkProgress(stageIndex, path, completed, total, state)
                        },
                        onDetail = { path, completed, total, detail ->
                            pushApkProgress(stageIndex, path, completed, total, "running", detail)
                        },
                        mockLocationPath = mockLocationPath)
                }
            } catch (e: Exception) {
                StageRunResult.Failed((e.message ?: "неизвестная ошибка"))
            }
            // Штатные «эхо» закрытия каналов от магнитолы (см. readMessageForStream) — одной строкой вместо
            // десятков нечитаемых («Пропускаю чужое сообщение…», лог #374).
            val echoes = AdbEchoStats.take()
            if (echoes > 0) pushAdbLog("Магнитола $echoes раз подтвердила закрытие служебных каналов ADB (CLSE) — это штатно, не ошибка.")
            pushStageResult(stageIndex, result)
        }
    }

    /** Wi-Fi ADB на этапе приложений: сначала СКАЧАТЬ выбранное (пока у телефона есть интернет), и только
     * потом подключаться к магнитоле — в её сети интернета обычно нет. Успех — событие apk_download_done,
     * сбой/остановка — обычный adb_stage_result (тот же путь, что у неудачной установки). */
    private fun adbDownloadApks(args: JSONObject) {
        val stageIndex = args.optInt("index", -1)
        val pathsArr = args.getJSONArray("apkPaths")
        val paths = (0 until pathsArr.length()).map { pathsArr.getString(it) }
        labCancelInstall = false
        runExclusive({ pushStageResult(stageIndex, StageRunResult.Failed("Другая операция ещё выполняется")) }) {
            try {
                pushAdbLog("Wi-Fi ADB: сначала скачиваю приложения — пока есть интернет, потом подключимся к магнитоле.")
                ensureApksDownloaded(paths, ApkDownloadProgress({ path, done, total ->
                    pushApkProgress(stageIndex, path, 0, paths.size, "running", ApkOperationProgress("download", done, total))
                }, { labCancelInstall }))
                // ensure_apks_downloaded при сбое скачивания только пишет строку в лог — проверяем сами.
                val missing = paths.filter { !File(it).exists() }.map { File(it).name }
                if (missing.isNotEmpty()) {
                    pushStageResult(stageIndex, StageRunResult.Failed(
                        "Не удалось скачать: ${missing.joinToString(", ")}. Проверьте интернет (телефон не должен " +
                            "быть в Wi-Fi магнитолы без интернета) и повторите."))
                } else {
                    pushAdbLog("Приложения скачаны. Теперь подключитесь к Wi-Fi магнитолы.")
                    pushEvent(JSONObject().put("kind", "apk_download_done").put("index", stageIndex))
                }
            } catch (e: Exception) {
                pushStageResult(stageIndex, StageRunResult.Failed(e.message ?: "неизвестная ошибка"))
            }
        }
    }

    private fun pushApkProgress(stageIndex: Int, path: String, completed: Int, total: Int,
                                state: String, detail: ApkOperationProgress? = null) {
        val event = JSONObject().put("kind", "apk_progress").put("stage_index", stageIndex)
            .put("path", path).put("completed", completed).put("total", total).put("state", state)
        if (detail != null) {
            event.put("phase", detail.phase).put("determinate", detail.determinate)
            detail.bytesDone?.let { event.put("bytes_done", it) }
            detail.bytesTotal?.let { event.put("bytes_total", it) }
        }
        pushEvent(event)
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

    /** Список файлов, которые запишутся на флешку — вызывается ДО
     * usb_run_stage, чтобы app.js успел показать очередь в кольце прогресса
     * (openStageRun/LabUI.busy) раньше, чем начнётся сама запись. Быстрый,
     * синхронный (только File.length() уже докачанных файлов — см.
     * ensureApksDownloaded в usbRunStage ниже), ничего не запускает —
     * аналог desktop UsbApi.list_items (app/web/api/usb_api.py). Разбор
     * аргументов сознательно продублирован с usbRunStage — общей функции
     * сборки sharedFolderDir/apksDest не заводили ради одной короткой пары
     * строк, а не большого куска логики. */
    private fun usbListItems(args: JSONObject): String {
        val filesArr = args.getJSONArray("files")
        val files = (0 until filesArr.length()).map { filesArr.getString(it) }
        val sharedFolder = args.optString("sharedFolder", "")
        val sharedFolderDir = if (sharedFolder.isNotBlank()) File(carsDir, "_shared/$sharedFolder") else null
        val selectedArr = args.optJSONArray("selectedApks") ?: JSONArray()
        val selectedApks = (0 until selectedArr.length()).map { selectedArr.getString(it) }
        val items = JSONArray()
        scanUsbStageItems(files, sharedFolderDir, selectedApks).forEach { item ->
            items.put(JSONObject().put("name", item.name).put("path", item.path).put("size", item.size))
        }
        return JSONObject().put("ok", true).put("items", items).toString()
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
                    // "transfer" — та же фаза, что уже использует установка
                    // приложений для байтов, переданных по проводу (см.
                    // ApkOperationProgress) — для записи на флешку это
                    // ближайший существующий аналог байтового прогресса
                    // ВНУТРИ файла; отдельной фазы заводить не стали.
                    writeUsbStage(files, sharedFolderDir, selectedApks, apksDest, ::pushAdbLog,
                        onProgress = { path, bytesDone, bytesTotal, filesDone, filesTotal, state ->
                            pushApkProgress(stageIndex, path, filesDone, filesTotal, state,
                                ApkOperationProgress("transfer", bytesDone, bytesTotal))
                        })
                }
            } catch (e: Exception) {
                StageRunResult.Failed((e.message ?: "неизвестная ошибка"))
            }
            pushStageResult(stageIndex, result)
        }
    }

    /** Доп. шаг ПЕРЕД qrAdbWriteFlag — только для этапов с qr_adb_engineering_menu=true
     * (см. car_generator.py: StepSpec.qr_adb_engineering_menu, сейчас только Haval Jolion
     * 2026/Desay x9h): пишет svengmode.flag, который открывает инженерное меню магнитолы
     * — техник вручную доходит в нём до раздела с QR-кодом, и только тогда обычный
     * svlog.flag (см. qrAdbWriteFlag ниже) срабатывает. */
    private fun qrAdbWritePrepFlag() = runExclusive(::onBusy) {
        val event = try {
            if (!UsbFlashSession.isMounted) {
                JSONObject().put("ok", false).put("error", "Флешка не подключена — сначала подключите её сверху.")
            } else {
                val flagFile = File(carsDir, "_shared/svengmode.flag")
                if (!flagFile.exists()) {
                    JSONObject().put("ok", false).put(
                        "error", "svengmode.flag не найден — обновите каталог (Настройки → Проверить обновления) и попробуйте снова."
                    )
                } else {
                    writeQrAdbPrepFlag(UsbFlashSession.requireFs(), flagFile, ::pushAdbLog).fold(
                        onSuccess = { JSONObject().put("ok", true) },
                        onFailure = { e -> JSONObject().put("ok", false).put("error", e.message) },
                    )
                }
            }
        } catch (e: Exception) {
            JSONObject().put("ok", false).put("error", e.message ?: "неизвестная ошибка")
        }
        pushEvent(JSONObject().put("kind", "qr_adb_prep_write_result").put("result", event))
    }

    /** Портовая версия "Пароль ADB по QR-коду" (desktop: app/qr_adb_password.py,
     * app/web/api/qr_adb_api.py) — та же флешка/сессия, что и у "usb"-этапа
     * (см. USB_STAGE_TYPES в app.js — qr_adb добавлен туда же, чтобы техник
     * подключал флешку тем же самым баром сверху). Шаг 1 процедуры (или шаг 2 —
     * после qrAdbWritePrepFlag, для qr_adb_engineering_menu=true): */
    private fun qrAdbWriteFlag() = runExclusive(::onBusy) {
        val event = try {
            if (!UsbFlashSession.isMounted) {
                JSONObject().put("ok", false).put("error", "Флешка не подключена — сначала подключите её сверху.")
            } else {
                val flagFile = File(carsDir, "_shared/svlog.flag")
                if (!flagFile.exists()) {
                    JSONObject().put("ok", false).put(
                        "error", "svlog.flag не найден — обновите каталог (Настройки → Проверить обновления) и попробуйте снова."
                    )
                } else {
                    writeQrAdbFlag(UsbFlashSession.requireFs(), flagFile, ::pushAdbLog).fold(
                        onSuccess = { JSONObject().put("ok", true) },
                        onFailure = { e -> JSONObject().put("ok", false).put("error", e.message) },
                    )
                }
            }
        } catch (e: Exception) {
            JSONObject().put("ok", false).put("error", e.message ?: "неизвестная ошибка")
        }
        pushEvent(JSONObject().put("kind", "qr_adb_write_result").put("result", event))
    }

    /** Шаг 4 процедуры: та же флешка (уже вставленная обратно после «QNX
     * OK» на магнитоле) читается напрямую через libaums (см.
     * readQrAdbBugreportZip), сам расчёт кода — в Python (см.
     * android/.../python/qr_adb_password.py — порт desktop-алгоритма). */
    private fun qrAdbGetPassword() = runExclusive(::onBusy) {
        val event = try {
            if (!UsbFlashSession.isMounted) {
                JSONObject().put("ok", false).put("error", "Флешка не подключена — сначала подключите её сверху.")
            } else {
                readQrAdbBugreportZip(UsbFlashSession.requireFs()).fold(
                    onSuccess = { bytes ->
                        val zipB64 = android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP)
                        // debug_dir: пока формула не подтверждена 100%-но
                        // надёжной (жалобы клиентов на неверный пароль,
                        // 2026-09-21) — сохраняем исходный bugreport-zip
                        // целиком, см. android/.../python/qr_adb_password.py:
                        // _save_debug_copy (порт desktop save_debug_copy).
                        val debugDir = File(context.filesDir, "qr_adb_debug").absolutePath
                        val resultJson = pyModule("qr_adb_password")
                            .callAttr("get_password_from_zip_b64", zipB64, debugDir).toString()
                        JSONObject(resultJson)
                    },
                    onFailure = { e -> JSONObject().put("ok", false).put("error", (e.message ?: "неизвестная ошибка")) },
                )
            }
        } catch (e: Exception) {
            JSONObject().put("ok", false).put("error", e.message ?: "неизвестная ошибка")
        }
        // Раньше вся эта попытка была невидима в постоянном журнале сессии —
        // жалобы «пароль неверный» нельзя было разобрать без доступа к
        // самому телефону техника. Логируем тем же каналом, что и остальные
        // ADB-действия (см. pushAdbLog/app.js: onAdbLog — тот же вызов
        // взводит sessionHasActivity и пишет в install_log_append).
        if (event.optBoolean("ok", false)) {
            val debugNote = if (event.isNull("debug_copy") || event.optString("debug_copy", "").isEmpty()) "" else ", копия дампа сохранена"
            pushAdbLog("QR ADB: пароль получен — код ${event.optString("code")}, SN ${event.optString("sn")}$debugNote.")
        } else {
            pushAdbLog("QR ADB: не удалось получить пароль — ${event.optString("error", "неизвестная ошибка")}")
        }
        pushEvent(JSONObject().put("kind", "qr_adb_password_result").put("result", event))
    }

    /** Список установленных пакетов — для модалки выбора приложения перед
     * actionsGrantPermissions/actionsMockLocation ниже (см. app.js:
     * renderActionsStage). Отдельным событием, а не синхронным возвратом из
     * call() — pm list packages идёт через тот же единственный ADB-транспорт,
     * что и всё остальное (runExclusive), не должен блокировать JS-поток. */
    private fun actionsListPackages(thirdPartyOnly: Boolean) = runExclusive(::onBusy) {
        val packages = if (!AdbSession.isConnected) {
            pushAdbLog("ADB не подключён — команда не выполнена.")
            emptyList()
        } else {
            AdbPermissions.listInstalledPackages(thirdPartyOnly, ::pushAdbLog)
        }
        pushEvent(JSONObject().put("kind", "actions_packages_result").put("packages", JSONArray(packages)))
    }

    private fun actionsGrantPermissions(pkg: String) = runExclusive(::onBusy) {
        if (!AdbSession.isConnected) { pushAdbLog("ADB не подключён — команда не выполнена."); return@runExclusive }
        AdbPermissions.grantAllPermissions(pkg, ::pushAdbLog)
    }

    private fun actionsMockLocation(pkg: String) = runExclusive(::onBusy) {
        if (!AdbSession.isConnected) { pushAdbLog("ADB не подключён — команда не выполнена."); return@runExclusive }
        AdbPermissions.setMockLocationApp(pkg, ::pushAdbLog)
    }

    private fun actionsLaunchActivity(pkg: String) = runExclusive(::onBusy) {
        if (!AdbSession.isConnected) { pushAdbLog("ADB не подключён — команда не выполнена."); return@runExclusive }
        AdbPermissions.launchMainActivity(pkg, ::pushAdbLog)
    }

    private fun actionsUninstallApp(pkg: String) = runExclusive(::onBusy) {
        if (!AdbSession.isConnected) { pushAdbLog("ADB не подключён — команда не выполнена."); return@runExclusive }
        AdbPermissions.uninstallApp(pkg, ::pushAdbLog)
    }

    private fun actionsDisableApp(pkg: String) = runExclusive(::onBusy) {
        if (!AdbSession.isConnected) { pushAdbLog("ADB не подключён — команда не выполнена."); return@runExclusive }
        AdbPermissions.disableApp(pkg, ::pushAdbLog)
    }

    private fun actionsEnableApp(pkg: String) = runExclusive(::onBusy) {
        if (!AdbSession.isConnected) { pushAdbLog("ADB не подключён — команда не выполнена."); return@runExclusive }
        AdbPermissions.enableApp(pkg, ::pushAdbLog)
    }

    /** "Добавить свой APK..." на apps/usb-этапах (см. app.js: renderApkTree) —
     * портовый эквивалент desktop stage_wizard.js:pickPersonalApks, но через
     * системный выбор документов (SAF) вместо нативного диалога pywebview:
     * у Android-приложения нет прямого доступа к произвольному пути на
     * диске без разрешения на конкретный файл, поэтому сам выбор идёт через
     * MainActivity (см. pickApksLauncher), а результат (список content://
     * Uri) возвращается сюда через onApksPicked. */
    private fun pickPersonalApks() {
        val launcher = pickApksLauncher
        if (launcher == null) {
            pushEvent(JSONObject().put("kind", "personal_apks_picked").put("apks", JSONArray()))
            return
        }
        launcher(arrayOf("application/vnd.android.package-archive", "application/octet-stream"))
    }

    /** Копирует выбранные через SAF файлы в приватное хранилище приложения
     * (content:// Uri нельзя напрямую передать в File.readBytes(), которым
     * пользуется InstallEngine.installApks) и сообщает JS готовые локальные
     * пути — та же роль, что и у {path, name} из car_pick_files на desktop. */
    fun onApksPicked(uris: List<Uri>) {
        Thread {
            val destDir = File(context.filesDir, "personal_apks").apply { mkdirs() }
            val apks = JSONArray()
            for (uri in uris) {
                val name = queryDisplayName(uri) ?: continue
                val dest = File(destDir, name)
                try {
                    context.contentResolver.openInputStream(uri)?.use { input ->
                        dest.outputStream().use { output -> input.copyTo(output) }
                    } ?: continue
                } catch (e: Exception) {
                    pushAdbLog("Не удалось прочитать $name: ${e.message}")
                    continue
                }
                apks.put(JSONObject().put("path", dest.absolutePath).put("name", name))
            }
            pushEvent(JSONObject().put("kind", "personal_apks_picked").put("apks", apks))
        }.start()
    }

    private fun queryDisplayName(uri: Uri): String? {
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { cursor ->
            val col = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (col >= 0 && cursor.moveToFirst()) return cursor.getString(col)
        }
        return uri.lastPathSegment
    }

    private fun pushStageResult(stageIndex: Int, result: StageRunResult) {
        val resultJson = when (result) {
            StageRunResult.Success -> JSONObject().put("success", true)
            is StageRunResult.Failed -> JSONObject().put("success", false).put("reason", result.reason)
        }
        pushEvent(JSONObject().put("kind", "adb_stage_result").put("index", stageIndex).put("result", resultJson))
    }
}
