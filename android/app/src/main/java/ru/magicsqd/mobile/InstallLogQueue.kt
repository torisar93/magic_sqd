package ru.magicsqd.mobile

import org.json.JSONObject
import java.io.File
import java.util.UUID

/**
 * Прочный локальный журнал сессии установки — переживает и обрыв сети (лог
 * уходит на сервер при следующем запуске программы, см. recoverAndDrainAtStartup/
 * drainQueue), и вылет процесса (то, что уже дописано в _current.log на диск,
 * не теряется, см. appendCurrent и MainActivity.kt:
 * Thread.setDefaultUncaughtExceptionHandler). Использует уже существующий
 * install_log_bridge.py:send_install_log (Chaquopy, см. WebBridge.kt) —
 * никакого нового сетевого кода здесь нет, это только файловая бухгалтерия.
 *
 * В отличие от desktop-версии (app/pending_install_logs.py) здесь НЕ нужен
 * токен сессии: Bridge.call() из app.js — честно СИНХРОННЫЙ вызов (JS-поток
 * блокируется до возврата из Kotlin, см. android/.../assets/js/bridge.js), а
 * сам JS однопоточный — порядок вызовов гарантирован сам по себе, пока запись
 * на диск делается синхронно прямо внутри диспетчеризации call() (см.
 * WebBridge.kt: install_log_append/install_log_session_start/installLogSend),
 * а не откладывается в отдельный поток (иначе появилась бы та же гонка, от
 * которой на десктопе спасает токен).
 */
object InstallLogQueue {
    private const val PENDING_DIR_NAME = "pending_install_logs"
    private const val CURRENT_LOG_NAME = "_current.log"
    private const val CURRENT_META_NAME = "_current.meta.json"
    private const val CURRENT_ACTIVITY_NAME = "_current.activity"
    private const val QUEUE_DIR_NAME = "queue"
    const val STALE_MARKER = "=== Предыдущий запуск программы не завершился штатно ==="
    const val CRASH_MARKER = "=== ПРИЛОЖЕНИЕ ЗАВЕРШИЛОСЬ С НЕОБРАБОТАННЫМ ИСКЛЮЧЕНИЕМ ==="

    private fun pendingDir(filesDir: File) = File(filesDir, PENDING_DIR_NAME)
    private fun currentLogFile(filesDir: File) = File(pendingDir(filesDir), CURRENT_LOG_NAME)
    private fun currentMetaFile(filesDir: File) = File(pendingDir(filesDir), CURRENT_META_NAME)
    private fun currentActivityFile(filesDir: File) = File(pendingDir(filesDir), CURRENT_ACTIVITY_NAME)
    private fun queueDir(filesDir: File) = File(pendingDir(filesDir), QUEUE_DIR_NAME)

    private fun clearCurrent(filesDir: File) {
        currentLogFile(filesDir).delete()
        currentMetaFile(filesDir).delete()
        currentActivityFile(filesDir).delete()
    }

    /** Новая сессия — свежий _current.* (прошлый, если был, просто убирается:
     * если в нём была реальная активность, он уже должен был уйти в очередь
     * через finalizeToQueue до этого момента — см. app.js: openWizard флашит
     * старую сессию ДО открытия новой модели; если нет — отбрасывать его и не
     * нужно было). */
    @Synchronized
    fun startSession(filesDir: File, brand: String, model: String, modification: String) {
        pendingDir(filesDir).mkdirs()
        clearCurrent(filesDir)
        val meta = JSONObject().put("brand", brand).put("model", model).put("modification", modification)
        currentMetaFile(filesDir).writeText(meta.toString())
        currentLogFile(filesDir).writeText("")
    }

    /** Дозаписывает одну строку лога сессии — синхронно, простым
     * open/write/close (без явного fsync — странице ОС достаточно получить
     * данные раньше, чем исчезнет именно процесс; отключение питания — не тот
     * случай, ради которого существует этот механизм). ОБЯЗАТЕЛЬНО вызывается
     * синхронно внутри диспетчеризации call(), не в фоновом потоке — иначе
     * порядок вызовов из JS перестаёт быть гарантированным (см. докстринг
     * объекта). hasActivity=true дополнительно создаёт файл-маркер активности
     * (идемпотентно — повторные вызовы ничего не портят). */
    @Synchronized
    fun appendCurrent(filesDir: File, line: String, hasActivity: Boolean) {
        try {
            currentLogFile(filesDir).appendText(line + "\n")
            if (hasActivity) currentActivityFile(filesDir).createNewFile()
        } catch (_: Exception) {
            // сбой записи лога не должен мешать самой установке
        }
    }

    private fun writeQueueEntry(filesDir: File, platform: String, brand: String, model: String,
                                 modification: String, success: Boolean, logText: String) {
        val dir = queueDir(filesDir)
        dir.mkdirs()
        val entry = JSONObject()
            .put("platform", platform).put("brand", brand).put("model", model)
            .put("modification", modification).put("success", success).put("log_text", logText)
        val name = "${System.currentTimeMillis()}-${UUID.randomUUID()}.json"
        // .part + переименование (тот же приём, что content_sync.py на
        // десктопе) — недописанный queue-файл никогда не виден drainQueue,
        // даже если процесс упадёт ровно посреди записи.
        val tmp = File(dir, "$name.part")
        tmp.writeText(entry.toString())
        tmp.renameTo(File(dir, name))
    }

    /** Запечатывает текущую сессию в queue/<файл>.json и очищает _current.* —
     * вызывается ПЕРЕД попыткой отправки (см. WebBridge.kt: installLogSend),
     * поэтому сбой сети/процесса после этого момента больше не теряет лог
     * целиком, только откладывает его до следующего запуска (см. drainQueue). */
    @Synchronized
    fun finalizeToQueue(filesDir: File, platform: String, brand: String, model: String,
                        modification: String, success: Boolean, logText: String) {
        writeQueueEntry(filesDir, platform, brand, model, modification, success, logText)
        clearCurrent(filesDir)
    }

    /** Если _current.* пережил прошлый запуск, значит та сессия не дошла до
     * finalizeToQueue (вылет процесса или принудительное закрытие раньше, чем
     * JS успела сама отправить лог). С маркером активности — считаем
     * достойной внимания (была реальная попытка установки), кладём в очередь
     * с success=false и текстовым маркером в начале лога; без маркера — была
     * просто открыта модель, слать нечего, как и сегодня. */
    @Synchronized
    private fun recoverStaleCurrent(filesDir: File, platform: String) {
        val metaFile = currentMetaFile(filesDir)
        if (!metaFile.isFile) {
            clearCurrent(filesDir)
            return
        }
        val hasActivity = currentActivityFile(filesDir).isFile
        if (!hasActivity) {
            clearCurrent(filesDir)
            return
        }
        val meta = try { JSONObject(metaFile.readText()) } catch (_: Exception) { JSONObject() }
        val logText = try { currentLogFile(filesDir).readText() } catch (_: Exception) { "" }
        writeQueueEntry(filesDir, platform, meta.optString("brand"), meta.optString("model"),
            meta.optString("modification"), false, "$STALE_MARKER\n$logText")
        clearCurrent(filesDir)
    }

    private fun listQueue(filesDir: File): List<File> {
        val dir = queueDir(filesDir)
        if (!dir.isDirectory) return emptyList()
        return (dir.listFiles { f -> f.isFile && f.name.endsWith(".json") } ?: emptyArray()).sortedBy { it.name }
    }

    /** Пытается отправить ОДНУ запись через уже существующий
     * install_log_bridge.send_install_log (Chaquopy, передаётся как sendInstallLog
     * — сама функция вызова остаётся в WebBridge.kt, здесь только бухгалтерия
     * очереди) и разбирает JSON-результат (раньше installLogSend его просто
     * отбрасывал, из-за чего было бы невозможно узнать, отправилось ли что-то,
     * и очередь никогда не опустела бы корректно) — удаляет файл при успехе,
     * оставляет при сбое (следующий проход повторит); sendInstallLog бросает
     * исключение — тоже оставляем файл, не роняем весь проход. */
    private fun sendEntry(
        path: File,
        sendInstallLog: (platform: String, brand: String, model: String, modification: String,
                         success: Boolean, logText: String) -> String,
    ) {
        val entry = try { JSONObject(path.readText()) } catch (_: Exception) { return }
        val resultJson = try {
            sendInstallLog(
                entry.optString("platform"), entry.optString("brand"), entry.optString("model"),
                entry.optString("modification"), entry.optBoolean("success"), entry.optString("log_text"),
            )
        } catch (_: Exception) {
            return
        }
        val ok = try { JSONObject(resultJson).optBoolean("ok") } catch (_: Exception) { false }
        if (ok) path.delete()
    }

    /** Проходит по всей очереди — вызывается фоновым потоком после каждого
     * штатного завершения сессии (см. WebBridge.kt: installLogSend — заодно
     * опустошает и то, что накопилось раньше, например попытки без интернета
     * при Wi-Fi ADB). */
    fun drainQueue(
        filesDir: File,
        sendInstallLog: (platform: String, brand: String, model: String, modification: String,
                         success: Boolean, logText: String) -> String,
    ) {
        for (path in listQueue(filesDir)) sendEntry(path, sendInstallLog)
    }

    /** Вызывается ОДИН раз при старте (см. MainActivity.kt: onCreate,
     * фоновым потоком) — сначала восстанавливает брошенную с прошлого раза
     * сессию (см. recoverStaleCurrent), затем проходит по всей очереди. */
    fun recoverAndDrainAtStartup(
        filesDir: File,
        platform: String,
        sendInstallLog: (platform: String, brand: String, model: String, modification: String,
                         success: Boolean, logText: String) -> String,
    ) {
        recoverStaleCurrent(filesDir, platform)
        drainQueue(filesDir, sendInstallLog)
    }
}
