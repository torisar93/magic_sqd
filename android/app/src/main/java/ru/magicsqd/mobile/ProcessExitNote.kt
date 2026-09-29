package ru.magicsqd.mobile

import android.app.ActivityManager
import android.app.ApplicationExitInfo
import android.content.Context
import android.os.Build
import androidx.annotation.RequiresApi

/**
 * Как закончился прошлый запуск программы — строкой в конец журнала сессии, которую досылаем при следующем запуске
 * (InstallLogQueue.recoverStaleCurrent). Раньше там было только «программу закрыли или система выгрузила её», и было
 * не понять, что случилось: техник смахнул программу, Android выгрузил её из-за памяти или она упала (Belgee S50,
 * лог №1718, 29.09: «само вылетело»). Android сообщает это с версии 11 (ApplicationExitInfo); на старых строки нет —
 * версия Android есть в шапке журнала.
 */
object ProcessExitNote {
    const val PREFIX = "Как Android закрыл программу:"

    /** lastLineAt — когда в журнал дописана последняя строка (время файла): запись о закрытии раньше неё — не про эту
     * сессию. */
    fun describe(context: Context, lastLineAt: Long): String? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return null
        return try {
            val manager = context.getSystemService(ActivityManager::class.java) ?: return null
            val info = manager.getHistoricalProcessExitReasons(context.packageName, 0, 5)
                .firstOrNull { it.processName == context.packageName }
            if (info == null || info.timestamp + 60_000 < lastLineAt) "$PREFIX не сообщил."
            else line(info.reason, info.status, info.importance, if (info.pss > 0) info.pss else info.rss,
                info.description, info.timestamp - lastLineAt)
        } catch (_: Exception) {
            null  // досылка журнала важнее этой строки
        }
    }

    @RequiresApi(Build.VERSION_CODES.R)
    internal fun line(reason: Int, status: Int, importance: Int, memoryKb: Long, description: String?, afterMs: Long): String {
        val parts = mutableListOf(
            if (importance <= ActivityManager.RunningAppProcessInfo.IMPORTANCE_VISIBLE) "была на экране" else "была в фоне")
        if (memoryKb > 0) parts += "занимала ${memoryKb / 1024} МБ"
        if (afterMs >= 1000) parts += "через ${DownloadSink.durationText(afterMs)} после последней строки"
        description?.trim()?.takeIf { it.isNotEmpty() }?.let { parts += "пояснение Android: «${it.take(120)}»" }
        return "$PREFIX ${reasonText(reason, status)} (${parts.joinToString(", ")})."
    }

    @RequiresApi(Build.VERSION_CODES.R)
    internal fun reasonText(reason: Int, status: Int): String = when (reason) {
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
