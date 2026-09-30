package ru.magicsqd.mobile

import android.app.ActivityManager
import android.app.ApplicationExitInfo
import android.content.Context
import android.os.Build
import android.os.Process
import android.os.SystemClock
import androidx.annotation.RequiresApi
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Как закончился прошлый запуск программы — строкой в конец журнала сессии, которую досылаем при следующем запуске
 * (InstallLogQueue.recoverStaleCurrent). Раньше там было только «программу закрыли или система выгрузила её», и было
 * не понять, что случилось: техник смахнул программу, Android выгрузил её из-за памяти или она упала (Belgee S50,
 * лог №1718, 29.09: «само вылетело»). Android сообщает это с версии 11 (ApplicationExitInfo); на старых строки нет —
 * версия Android есть в шапке журнала.
 */
object ProcessExitNote {
    const val PREFIX = "Как Android закрыл программу:"

    // Кто остановил программу принудительно — пакет из пояснения Android «… due to from process:<пакет>». Лог №1773
    // (29.09): «[FORCE STOP] stop ru.magicsqd.mobile due to from process:com.miui.securitycenter» — не техник, а очистка
    // Xiaomi через 50 с после сворачивания; строка же говорила «закрыли вручную».
    private val STOPPED_BY = Regex("from process:([\\w.]+)")
    private val KNOWN_STOPPERS = mapOf(
        "com.miui.securitycenter" to "принудительно остановила «Безопасность» Xiaomi — очистка памяти или экономия заряда",
        "com.miui.powerkeeper" to "принудительно остановил «Контроль питания» Xiaomi",
        "com.samsung.android.lool" to "принудительно остановило «Обслуживание устройства» Samsung",
        "com.samsung.android.sm" to "принудительно остановило «Обслуживание устройства» Samsung",
        "com.huawei.systemmanager" to "принудительно остановил «Диспетчер телефона» Huawei",
        "com.coloros.safecenter" to "принудительно остановила «Безопасность» OPPO/realme",
        "com.oplus.battery" to "принудительно остановила экономия заряда OPPO/realme",
        "com.android.settings" to "остановили вручную в «Настройках»",
        "com.android.systemui" to "смахнули из недавних приложений",
    )

    /** lastLineAt — когда в журнал дописана последняя строка (время файла): запись о закрытии раньше неё — не про эту
     * сессию. */
    fun describe(context: Context, lastLineAt: Long): String? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return null
        return try {
            // Процесс запущен раньше последней строки журнала — значит, её писал он сам и не закрывался: Android
            // пересоздал только окно программы, сессия прервалась вместе с ним (лог №1775, 29.09: «не сообщил»).
            val startedAt = System.currentTimeMillis() - (SystemClock.elapsedRealtime() - Process.getStartElapsedRealtime())
            if (startedAt + 2_000 < lastLineAt) {
                return "$PREFIX не закрывал — программа работала дальше, Android пересоздал только её окно."
            }
            val manager = context.getSystemService(ActivityManager::class.java) ?: return null
            val info = manager.getHistoricalProcessExitReasons(context.packageName, 0, 5)
                .firstOrNull { it.processName == context.packageName }
                ?: return "$PREFIX не сообщил."
            if (info.timestamp + 60_000 < lastLineAt) {
                "$PREFIX не сообщил (его последняя запись — " +
                    "${reasonText(info.reason, info.status, info.description, context.packageName)} в ${clock(info.timestamp)}, " +
                    "раньше конца этой сессии)."
            } else {
                line(info.reason, info.status, info.importance, if (info.pss > 0) info.pss else info.rss,
                    info.description, info.timestamp - lastLineAt, context.packageName)
            }
        } catch (_: Exception) {
            null  // досылка журнала важнее этой строки
        }
    }

    private fun clock(ms: Long): String = SimpleDateFormat("HH:mm:ss", Locale.US).format(Date(ms))

    @RequiresApi(Build.VERSION_CODES.R)
    internal fun line(reason: Int, status: Int, importance: Int, memoryKb: Long, description: String?, afterMs: Long,
                      ownPackage: String): String {
        val parts = mutableListOf(
            if (importance <= ActivityManager.RunningAppProcessInfo.IMPORTANCE_VISIBLE) "была на экране" else "была в фоне")
        if (memoryKb > 0) parts += "занимала ${memoryKb / 1024} МБ"
        if (afterMs >= 1000) parts += "через ${DownloadSink.durationText(afterMs)} после последней строки"
        description?.trim()?.takeIf { it.isNotEmpty() }?.let { parts += "пояснение Android: «${it.take(120)}»" }
        return "$PREFIX ${reasonText(reason, status, description, ownPackage)} (${parts.joinToString(", ")})."
    }

    @RequiresApi(Build.VERSION_CODES.R)
    internal fun reasonText(reason: Int, status: Int, description: String? = null, ownPackage: String = ""): String {
        val stopper = STOPPED_BY.find(description.orEmpty())?.groupValues?.get(1)?.takeIf { it != ownPackage }
        if (reason == ApplicationExitInfo.REASON_USER_REQUESTED && stopper != null) {
            return KNOWN_STOPPERS[stopper] ?: "принудительно остановило приложение телефона «$stopper»"
        }
        // Пояснение Android точнее кода причины: «[REMOVE TASK]» (Honor, Realme) и «SwipeUpClean» (Xiaomi, код OTHER —
        // писалось «закрыл по своей причине») — смахнули; «[KILL BACKGROUND]» (Samsung) — очистка памяти, а не техник
        // (логи №1799, №1800, №1810, №1813, 30.09).
        if (reason == ApplicationExitInfo.REASON_USER_REQUESTED || reason == ApplicationExitInfo.REASON_OTHER) {
            val text = description.orEmpty().lowercase()
            if ("remove task" in text || "swipeupclean" in text) return "смахнули из недавних приложений"
            if ("kill background" in text) return "закрыл в фоне при очистке памяти"
        }
        return when (reason) {
            ApplicationExitInfo.REASON_LOW_MEMORY -> "выгрузил из-за нехватки памяти"
            ApplicationExitInfo.REASON_USER_REQUESTED -> "закрыли вручную (смахнули из недавних или «Остановить» в настройках)"
            ApplicationExitInfo.REASON_CRASH -> "сбой программы"
            ApplicationExitInfo.REASON_CRASH_NATIVE -> "сбой программы (native)"
            ApplicationExitInfo.REASON_ANR -> "программа не отвечала, Android закрыл её"
            ApplicationExitInfo.REASON_EXIT_SELF -> "программа завершилась сама"
            ApplicationExitInfo.REASON_SIGNALED -> "процесс остановлен сигналом $status"
            ApplicationExitInfo.REASON_INITIALIZATION_FAILURE -> "сбой при запуске программы"
            ApplicationExitInfo.REASON_PERMISSION_CHANGE -> "изменились разрешения программы"
            ApplicationExitInfo.REASON_EXCESSIVE_RESOURCE_USAGE -> "закрыл за чрезмерную нагрузку"
            ApplicationExitInfo.REASON_USER_STOPPED -> "остановлен профиль пользователя на телефоне"
            ApplicationExitInfo.REASON_DEPENDENCY_DIED -> "закрыл вслед за связанным процессом"
            ApplicationExitInfo.REASON_OTHER -> "закрыл по своей причине"
            14 -> "закрыл замороженную в фоне программу"  // REASON_FREEZER, Android 13
            15 -> "изменилось состояние программы в системе"  // REASON_PACKAGE_STATE_CHANGE, Android 14
            16 -> "программа обновилась"  // REASON_PACKAGE_UPDATED, Android 14
            else -> "причина неизвестна (код $reason)"
        }
    }
}
