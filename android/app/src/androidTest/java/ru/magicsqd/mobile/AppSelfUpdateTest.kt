package ru.magicsqd.mobile

import android.os.Build
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.IOException

/**
 * Обновление программы из неё самой (AppSelfUpdate.kt) на эмуляторе. APK обновления раздаёт компьютер
 * (python3 -m http.server, с эмулятора — 10.0.2.2), адрес и sha256 — аргументами am instrument:
 *   -e updateUrl http://10.0.2.2:8765/update.apk -e updateSha <sha256> [-e foreignUrl …/foreign.apk]
 * update.apk — та же программа с versionCode на 1 больше и той же отладочной подписью, foreign.apk — она же с чужой
 * подписью. selfUpdate ставит обновление; процесс при этом закрывается — номер версии проверяют снаружи
 * (dumpsys package), окно подтверждения Android 8–11 жмут оттуда же (uiautomator dump → input tap). Разрешение на установку перед ним: adb shell appops set ru.magicsqd.mobile REQUEST_INSTALL_PACKAGES allow.
 */
@RunWith(AndroidJUnit4::class)
class AppSelfUpdateTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val args = InstrumentationRegistry.getArguments()

    private fun arg(name: String): String {
        val value = args.getString(name)
        assumeTrue("нужен -e $name", !value.isNullOrBlank())
        return value!!
    }

    @Test
    fun permissionStateIsReported() {
        Log.i(TAG, "canInstall=${AppSelfUpdate.canInstall(context)} silent=${AppSelfUpdate.silentPossible()}")
        assertEquals(Build.VERSION.SDK_INT >= Build.VERSION_CODES.S, AppSelfUpdate.silentPossible())
    }

    @Test
    fun wrongChecksumIsRejected() {
        val error = try {
            AppSelfUpdate.download(context, arg("updateUrl"), "0".repeat(64)) { _, _ -> }
            null
        } catch (e: IOException) {
            e.message
        }
        assertTrue("$error", error!!.contains("контрольная сумма не совпала"))
    }

    @Test
    fun foreignSignatureIsRejected() {
        val apk = AppSelfUpdate.download(context, arg("foreignUrl"), null) { _, _ -> }
        assertEquals("файл подписан другим ключом", AppSelfUpdate.verify(context, apk))
    }

    /** -e visible false — программа «свёрнута»: окно подтверждения (Android 8–11) ждёт MainActivity.onStart
     * (adb shell am start -n ru.magicsqd.mobile/.MainActivity). */
    @Test
    fun selfUpdate() {
        AppSelfUpdate.appVisible = args.getString("visible") != "false"
        AppSelfUpdate.onStatus = { stage, message -> Log.i(TAG, "итог установки: $stage $message") }
        val apk = AppSelfUpdate.download(context, arg("updateUrl"), arg("updateSha")) { done, total ->
            if (done == total) Log.i(TAG, "скачано $done байт")
        }
        assertEquals(null, AppSelfUpdate.verify(context, apk))
        assertTrue("нет разрешения на установку", AppSelfUpdate.canInstall(context))
        Log.i(TAG, "ставлю обновление (silent=${AppSelfUpdate.silentPossible()})")
        AppSelfUpdate.install(context, apk)
        Thread.sleep(60_000)  // Android закроет процесс, когда поставит новую версию
    }

    companion object {
        private const val TAG = "AppSelfUpdateTest"
    }
}
