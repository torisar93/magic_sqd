package ru.magicsqd.adbdiag

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Settings
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * На каждой загрузке повторяет способы 2 и 4 (persist.sv.debug.adb_enable
 * пишем и тут на всякий случай, хотя persist.* и так должен сохраниться сам
 * — вдруг конкретно на этой прошивке это свойство почему-то не переживает
 * перезагрузку без повторной записи) — если это сработает, ADB должен
 * подниматься сам при каждом включении машины, без похода в инженерное
 * меню. Результат сохраняется в SharedPreferences и показывается в
 * MainActivity при следующем открытии — техник видит его прямо на экране,
 * не запуская никаких кнопок.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED) return

        val results = StringBuilder()
        try {
            Settings.Global.putInt(context.contentResolver, "adb_wifi_enabled", 1)
            results.append("adb_wifi_enabled=1 OK; ")
        } catch (e: Exception) {
            results.append("adb_wifi_enabled: ${e.javaClass.simpleName}; ")
        }

        try {
            val process = ProcessBuilder("sh", "-c", "setprop persist.sv.debug.adb_enable 1")
                .redirectErrorStream(true).start()
            process.waitFor()
            results.append("persist.sv.debug.adb_enable OK")
        } catch (e: Exception) {
            results.append("persist.sv.debug.adb_enable: ${e.javaClass.simpleName}")
        }

        val stamp = SimpleDateFormat("dd.MM HH:mm:ss", Locale.US).format(Date())
        context.getSharedPreferences("adbdiag", Context.MODE_PRIVATE).edit()
            .putString("last_boot_summary", "$stamp — $results")
            .apply()
    }

    companion object {
        fun lastRunSummary(context: Context): String =
            context.getSharedPreferences("adbdiag", Context.MODE_PRIVATE)
                .getString("last_boot_summary", "(ещё не было перезагрузок с этим приложением)")
                ?: "(нет данных)"
    }
}
