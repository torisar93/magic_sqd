package ru.magicsqd.mobile.usb

import android.content.Context
import com.chaquo.python.Python
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID
import java.util.concurrent.TimeUnit

/**
 * Kotlin-примитивы для общего Python-кода каталога (py_runner.py, команда «#py модуль.функция»; владелец, 2026-10-06:
 * «чтобы обновлений приложения стало поменьше»). Функции из cars/_shared/<модуль>.py — те же, что исполняет ПК, — получают
 * ctx (py_runner.AndroidCtx), а он ходит сюда: тот же ADB-сеанс (AdbSession), тот же журнал и те же окна вопросов, что
 * у остального приложения. Методы вызывает Python (Chaquopy) на потоке этапа.
 *
 * Ошибка связи (ADB-сеанс оборвался) — строка [FAIL] + причина: py_runner превращает её в понятную ошибку этапа.
 */
class PyCtxBridge(
    private val context: Context,
    private val logFn: (String) -> Unit,
    private val cancelled: () -> Boolean,
    /** Окно вопроса в интерфейсе: requestId, вопрос, заголовок, варианты (пусто — поле ввода), можно ли ввести свой. */
    private val requestAsk: (String, String, String, JSONArray, Boolean) -> Unit,
    private val progressFn: (Int, Int) -> Unit = { _, _ -> },
    /** «Работа в движении» (MotionOptimize) для пакета; null — нет такой возможности в этом месте приложения. */
    private val motion: ((String) -> Unit)? = null,
) {
    fun log(text: String) = logFn(text)

    fun isCancelled(): Boolean = cancelled()

    fun isConnected(): Boolean = AdbSession.isConnected

    fun progress(done: Int, total: Int) = progressFn(done, total)

    /** Вывод команды (stderr вместе с stdout, как в ADB-сеансе); «error: closed» — прошивка сразу закрыла поток
     * (как пишет adb на ПК). */
    fun shell(command: String, timeoutMs: Int): String = linkSafe {
        when (val r = AdbSession.shell(command, logFn, timeoutMs.coerceIn(1_000, 30 * 60_000))) {
            is AdbShellResult.Output -> r.text
            is AdbShellResult.Rejected -> "error: closed"
            is AdbShellResult.Failed -> FAIL + r.reason
        }
    }

    /** Служебная команда adb (root:, remount:, disable-verity: …) — её вывод. */
    fun service(name: String): String = linkSafe {
        when (val r = AdbSession.service(name, logFn, 60_000)) {
            is AdbShellResult.Output -> r.text
            is AdbShellResult.Rejected -> "error: closed"
            is AdbShellResult.Failed -> FAIL + r.reason
        }
    }

    /** null — записано; иначе причина. */
    fun push(localPath: String, remote: String): String? = linkSafeNullable {
        val file = File(localPath)
        if (!file.isFile) return@linkSafeNullable "нет файла ${file.name}"
        when (val r = AdbSession.push(PushSource.of(file), remote, logFn)) {
            AdbPushResult.Success -> null
            is AdbPushResult.Failed -> r.reason
        }
    }

    fun pull(remote: String, localPath: String): String? = linkSafeNullable {
        val dest = File(localPath)
        dest.parentFile?.mkdirs()
        when (val r = AdbSession.pull(remote, dest, logFn)) {
            is AdbPullResult.Success -> null
            is AdbPullResult.Failed -> r.reason
        }
    }

    fun installApk(localPath: String): String? = linkSafeNullable {
        val file = File(localPath)
        if (!file.isFile) return@linkSafeNullable "нет файла ${file.name}"
        when (val r = AdbSession.installApk(PushSource.of(file), logFn)) {
            is AdbInstallResult.Success -> null
            is AdbInstallResult.Failed -> r.reason
        }
    }

    /** null — перезагрузка прошла (и, если wait, магнитола снова на связи). */
    fun reboot(wait: Boolean, timeoutMs: Int): String? = linkSafeNullable {
        logFn(if (wait) "Перезагружаю магнитолу и жду её возвращения (до ${timeoutMs / 1000}с)..." else "Перезагружаю магнитолу...")
        AdbSession.service("reboot:", logFn)
        AdbSession.disconnect()
        if (!wait) return@linkSafeNullable null
        when (val r = AdbSession.waitForDeviceAndReconnect(context, timeoutMs.toLong(), logFn)) {
            is AdbHandshakeResult.Connected -> null
            is AdbHandshakeResult.Failed -> "магнитола не вернулась после перезагрузки: ${r.reason}"
        }
    }

    fun waitForDevice(timeoutMs: Int): Boolean {
        if (AdbSession.isConnected) return true
        return AdbSession.waitForDeviceAndReconnect(context, timeoutMs.toLong(), logFn) is AdbHandshakeResult.Connected
    }

    /** Ответ техника или null (отмена, нет ответа за 10 минут). */
    fun ask(prompt: String, title: String, choicesJson: String, allowManual: Boolean): String? {
        val choices = try { JSONArray(choicesJson) } catch (_: Exception) { JSONArray() }
        val requestId = UUID.randomUUID().toString()
        val queue = AskInputBroker.register(requestId)
        return try {
            requestAsk(requestId, prompt, title, choices, allowManual)
            queue.poll(10, TimeUnit.MINUTES)?.takeIf { it.isNotEmpty() }
        } finally {
            AskInputBroker.unregister(requestId)
        }
    }

    fun uninstallViaHelper(pkg: String): Boolean = try {
        AdbPermissions.removePackageViaHelper(context, pkg, logFn)
    } catch (e: AdbLinkLostException) {
        AdbSession.markLinkLost()
        false
    }

    /** null — сделано (итог MotionOptimize пишет в журнал сам); иначе причина. */
    fun optimizeForMotion(pkg: String): String? {
        val run = motion ?: return "в этом месте приложения «работа в движении» недоступна"
        return linkSafeNullable { run(pkg); null }
    }

    private inline fun linkSafe(block: () -> String): String = try {
        block()
    } catch (e: AdbLinkLostException) {
        AdbSession.markLinkLost()
        FAIL + (e.message ?: "связь с магнитолой потеряна")
    }

    private inline fun linkSafeNullable(block: () -> String?): String? = try {
        block()
    } catch (e: AdbLinkLostException) {
        AdbSession.markLinkLost()
        e.message ?: "связь с магнитолой потеряна"
    }

    /** Вызвать <module>.<function>(ctx, *args) из cars/_shared (py_runner.run). */
    fun call(module: String, function: String, args: JSONArray, modelDir: String = "",
             files: Map<String, String> = emptyMap()): PyCallResult {
        val shared = File(context.filesDir, "cars/_shared").absolutePath
        val raw = try {
            Python.getInstance().getModule("py_runner").callAttr(
                "run", this, shared, module, function, args.toString(), modelDir, JSONObject(files).toString(),
            ).toString()
        } catch (e: Exception) {
            return PyCallResult(false, "${e.javaClass.simpleName}: ${e.message}", false, true, null)
        }
        val json = try { JSONObject(raw) } catch (_: Exception) {
            return PyCallResult(false, "непонятный ответ: ${raw.take(200)}", false, true, null)
        }
        return PyCallResult(json.optBoolean("ok"), json.optString("error"), json.optBoolean("cancelled"),
            json.optBoolean("unavailable"), json.opt("result"))
    }

    companion object {
        /** Начало строки — ADB-сеанс оборвался (py_runner._FAIL). */
        const val FAIL = "\u0000fail:"
    }
}

/** unavailable — функция даже не запускалась (нет модуля, подписи или функции): можно сделать то же встроенным способом. */
data class PyCallResult(val ok: Boolean, val error: String, val cancelled: Boolean, val unavailable: Boolean,
                        val result: Any?)
