package ru.magicsqd.mobile.usb

import android.content.Context
import com.chaquo.python.Python
import java.io.File

/**
 * «Работа в движении» на Android (владелец 2026-09-30: Haval Dargo 2026 не проигрывает видео в движении). Порт
 * десктопного InstallContext.optimize_for_motion: снять установленный APK с магнитолы (AdbSession.pull / sync RECV),
 * пометить все его окна distractionOptimized=true и пересобрать (Chaquopy-модуль motion_patch — ТОТ ЖЕ чистый Python,
 * что и на ПК, см. app/motion_patch.py), переподписать нашим ключом (cars/_shared/motion_cert через ApkResign, как
 * переподпись Changan) и поставить заново (InstallEngine — все резервные способы). Подпись после правки другая,
 * поверх не встанет, поэтому старое приложение удаляется, а при неудаче возвращается исходное (данные приложения
 * теряются — техник ставит это при настройке). У приложения из нескольких частей (split) так не сработает.
 *
 * Строки лога — те же, что на ПК (их знают правила разбора логов на сервере).
 */
object MotionOptimize {
    /** Папка ключа в cars/_shared. Скачивается при запуске (content_sync.STARTUP_SHARED_FOLDERS), а если запуск был
     *  без интернета — перед нажатием (WebBridge.actionsMotionOptimize). До 1.0.54 не скачивалась вовсе (лог №1985). */
    const val CERT_FOLDER = "motion_cert"

    private fun certDirOf(context: Context) = File(context.filesDir, "cars/_shared/$CERT_FOLDER")

    fun certReady(context: Context): Boolean =
        File(certDirOf(context), "private.pk8").isFile && File(certDirOf(context), "certificate.crt").isFile

    fun run(context: Context, pkg: String, engine: InstallEngine, cancelled: () -> Boolean, log: (String) -> Unit) {
        if (!AdbSession.isConnected) { log("ADB не подключён — команда не выполнена."); return }
        val certDir = certDirOf(context)
        if (!certReady(context)) {
            log("Не удалось включить работу в движении: нет ключа подписи — не удалось скачать его с сервера. " +
                "Проверьте интернет на телефоне и повторите.")
            return
        }
        log("Работа в движении: снимаю $pkg с магнитолы...")
        val paths = installedApkPaths(pkg, log)
        if (paths.isEmpty()) { log("Не удалось: приложение $pkg не установлено на магнитоле."); return }
        if (paths.size > 1) {
            log("Не удалось: приложение собрано из нескольких частей (split APK) — включить работу в движении для него нельзя.")
            return
        }
        val work = File(context.cacheDir, "motion").apply { mkdirs() }
        val original = File(work, "$pkg.apk")
        val patched = File(work, "$pkg.motion.apk")
        val signed = File(work, "$pkg.signed.apk")
        listOf(original, patched, signed).forEach { it.delete() }
        try {
            when (val r = AdbSession.pull(paths[0], original, log)) {
                is AdbPullResult.Failed -> { log("Не удалось снять приложение с магнитолы: ${r.reason}"); return }
                is AdbPullResult.Success -> {}
            }
            if (!original.isFile || original.length() == 0L) { log("Не удалось: пустой файл приложения."); return }
            val py = Python.getInstance().getModule("motion_patch")
            val marked = py.callAttr("patch_apk", original.absolutePath, patched.absolutePath).toInt()
            if (marked == 0) {
                log("У приложения уже все окна помечены — менять ничего не нужно.")
                logVerdict(pkg, log)
                return
            }
            log("Помечено окон: $marked. Переподписываю и ставлю заново...")
            resignApkFile(patched, certDir, signed)
        } catch (e: Exception) {
            log("Не удалось пометить приложение: ${e.message}. Оно оставлено как было.")
            return
        }
        // Подпись изменилась — поверх старого приложения не встанет, поэтому сначала удаляем его.
        AdbSession.shell("pm uninstall $pkg", log, 120000)
        val result = engine.installApks(listOf(signed.absolutePath), cancelled = cancelled)
        if (result is StageRunResult.Failed) {
            log("Не удалось поставить помеченную версию: ${result.reason}. Возвращаю исходную...")
            val restore = engine.installApks(listOf(original.absolutePath), cancelled = cancelled)
            if (restore is StageRunResult.Failed) log("Не удалось вернуть исходную версию — установите $pkg заново из каталога.")
            else log("Исходное приложение возвращено — работа в движении не включена.")
            listOf(patched, signed).forEach { it.delete() }
            return
        }
        log("Приложение переустановлено — если оно просит вход, войдите в него заново на магнитоле.")
        logVerdict(pkg, log)
        listOf(original, patched, signed).forEach { it.delete() }
    }

    private fun installedApkPaths(pkg: String, log: (String) -> Unit): List<String> {
        val text = (AdbSession.shell("pm path $pkg", log, 30000) as? AdbShellResult.Output)?.text ?: return emptyList()
        return text.lineSequence().map { it.trim() }
            .filter { it.startsWith("package:") && it != "package:" }
            .map { it.removePrefix("package:") }.toList()
    }

    private fun logVerdict(pkg: String, log: (String) -> Unit) {
        val dump = (AdbSession.shell("dumpsys car_service", log, 60000) as? AdbShellResult.Output)?.text ?: ""
        val py = Python.getInstance().getModule("motion_patch")
        val verdict = py.callAttr("car_service_verdict", dump, pkg).toString()
        log(py.callAttr("verdict_line", verdict, pkg, dump).toString())
    }
}
