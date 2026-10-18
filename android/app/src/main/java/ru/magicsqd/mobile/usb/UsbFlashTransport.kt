package ru.magicsqd.mobile.usb

import android.app.ActivityManager
import android.app.ApplicationExitInfo
import android.content.Context
import android.content.SharedPreferences
import android.os.Build
import android.os.Process
import androidx.annotation.RequiresApi
import me.jahnen.libaums.core.UsbMassStorageDevice
import me.jahnen.libaums.core.usb.UsbCommunicationFactory
import org.json.JSONObject
import ru.magicsqd.mobile.BuildConfig
import ru.magicsqd.mobile.usb.libusb.LibusbCommunicationCreator
import ru.magicsqd.mobile.usb.libusb.LibusbNative
import java.io.File

/** Способ связи с флешкой: LIBUSB — новый (libusb/LibusbCommunication.kt), ANDROID — прежний (bulkTransfer libaums). */
enum class FlashTransport { LIBUSB, ANDROID }

/**
 * Запись на флешку новым способом — через libusb (libusb/LibusbCommunication.kt: чем плох прежний) — с откатом на
 * прежний (владелец, 18.10.2026: «давай сразу добавим в приложение но безопасно чтобы если что программа сама
 * откатывалась на старый способ, посмотрим пару дней, почитаем логи, если новый способ лучше то потом только его и
 * оставим»). Сам откат посреди работы — UsbFlashSession.fallBack.
 *
 * libusb берётся, только если:
 *  - Android 11+: только с него Android сообщает, отчего закрылся прошлый запуск (checkLastExit); обрывы записи
 *    прежним способом — почти только на Android 13+, на 8–12 их почти нет;
 *  - не выключен с сервера: client.json "settings": {"android_flash_transport": "android"} — со следующего
 *    подключения флешки, без выпуска;
 *  - в этом запуске программы сбоя через libusb ещё не было — после отката до перезапуска только прежний способ;
 *  - не выключен на этом телефоне (до следующей версии программы): два отката подряд, после которых прежний способ
 *    справился, или прошлый запуск упал в машинном коде, когда флешка была подключена через libusb.
 * Способ — в журнале у подключения и у итога записи («Флешка смонтирована: … — libusb 1.0.30»), откаты — строками
 * «libusb: …»: по ним разбор логов сравнивает способы.
 */
internal object FlashTransports {
    private const val PREFS = "usb_flash_transport"
    private const val KEY_OPEN_PID = "libusb_open_pid"
    private const val KEY_FALLBACKS = "libusb_fallbacks_in_row"
    private const val KEY_OFF_VERSION = "libusb_off_version"
    private const val KEY_OFF_REASON = "libusb_off_reason"
    private const val FALLBACKS_TO_DISABLE = 2
    private const val SERVER_SETTING = "android_flash_transport"
    private const val CLIENT_CONFIG = "client_config.json"  // копия client.json (WebBridge.clientConfigFile)

    private val factoryLock = Any()
    private var creatorRegistered = false
    @Volatile private var failedThisRun = false
    @Volatile private var lastExitChecked = false
    @Volatile private var offNoticeLogged = false

    fun title(transport: FlashTransport): String =
        if (transport == FlashTransport.LIBUSB) "libusb ${LibusbNative.version}" else "стандартный способ"

    /** Каким способом подключать флешку сейчас; почему не libusb — в журнал, один раз за запуск. */
    fun choose(context: Context, log: (String) -> Unit): FlashTransport {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return FlashTransport.ANDROID
        checkLastExit(context, log)
        if (failedThisRun) return FlashTransport.ANDROID
        val off = if (serverSetting(context) == "android") "выключен с сервера"
            else disabledReason(context)?.let { "выключен на этом телефоне до следующей версии программы ($it)" }
        if (off == null) return FlashTransport.LIBUSB
        if (!offNoticeLogged) {
            offNoticeLogged = true
            log("libusb $off — флешка подключается стандартным способом.")
        }
        return FlashTransport.ANDROID
    }

    /** libaums берёт способ связи из общего UsbCommunicationFactory: libusb — только на время этого init, после —
     * снова прежний (остальные init, например в окне диагностики, идут по-старому). */
    fun init(device: UsbMassStorageDevice, transport: FlashTransport) {
        synchronized(factoryLock) {
            if (transport == FlashTransport.LIBUSB) {
                if (!creatorRegistered) {
                    UsbCommunicationFactory.registerCommunication(LibusbCommunicationCreator())
                    creatorRegistered = true
                }
                UsbCommunicationFactory.underlyingUsbCommunication = UsbCommunicationFactory.UnderlyingUsbCommunication.OTHER
            }
            try {
                device.init()
            } finally {
                UsbCommunicationFactory.underlyingUsbCommunication =
                    UsbCommunicationFactory.UnderlyingUsbCommunication.DEVICE_CONNECTION_SYNC
            }
        }
    }

    /** Сбой через libusb — до перезапуска программы только прежний способ. */
    fun noteFallback() {
        failedThisRun = true
    }

    /** После отката прежний способ справился — в счёт откатов подряд; на втором libusb на этом телефоне выключается. */
    fun noteStandardHelped(context: Context, log: (String) -> Unit) {
        val prefs = prefs(context)
        val inRow = prefs.getInt(KEY_FALLBACKS, 0) + 1
        if (inRow < FALLBACKS_TO_DISABLE) {
            prefs.edit().putInt(KEY_FALLBACKS, inRow).apply()
            return
        }
        disable(prefs, "$inRow отката подряд, прежний способ справлялся")
        log("libusb: $inRow-й откат подряд, а стандартный способ справляется — на этом телефоне дальше только он " +
            "(до следующей версии программы).")
    }

    /** Этап записан через libusb целиком — счёт откатов подряд заново. */
    fun noteLibusbWorked(context: Context) {
        val prefs = prefs(context)
        if (prefs.getInt(KEY_FALLBACKS, 0) != 0) prefs.edit().putInt(KEY_FALLBACKS, 0).apply()
    }

    /** Флешка подключена через libusb — запомнить процесс (commit: на диске до первой передачи): упадёт он в машинном
     * коде — следующий запуск это увидит (checkLastExit). */
    fun markOpen(context: Context) {
        prefs(context).edit().putInt(KEY_OPEN_PID, Process.myPid()).commit()
    }

    fun markClosed(context: Context) {
        prefs(context).edit().remove(KEY_OPEN_PID).apply()
    }

    /** Прошлый запуск закрылся при флешке, подключённой через libusb: упал в машинном коде (на libusb или мост к ней
     * думать первыми) — на этом телефоне дальше только прежний способ. Закрыл техник, выгрузил Android и т.п. — не в
     * счёт. */
    @RequiresApi(Build.VERSION_CODES.R)
    private fun checkLastExit(context: Context, log: (String) -> Unit) {
        if (lastExitChecked) return
        lastExitChecked = true
        val prefs = prefs(context)
        val pid = prefs.getInt(KEY_OPEN_PID, 0)
        if (pid == 0 || pid == Process.myPid()) return
        prefs.edit().remove(KEY_OPEN_PID).apply()
        val exit = try {
            (context.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager)
                .getHistoricalProcessExitReasons(null, pid, 1).firstOrNull()
        } catch (_: Exception) {
            null
        }
        if (exit?.reason != ApplicationExitInfo.REASON_CRASH_NATIVE) return
        disable(prefs, "прошлый запуск упал в машинном коде при флешке через libusb")
        offNoticeLogged = true  // причина — строкой ниже, «libusb выключен…» в choose не повторяем
        log("libusb: прошлый запуск программы упал в машинном коде, когда флешка была подключена через libusb, — на " +
            "этом телефоне дальше стандартный способ (до следующей версии программы).")
    }

    private fun disable(prefs: SharedPreferences, reason: String) {
        prefs.edit().putInt(KEY_OFF_VERSION, BuildConfig.VERSION_CODE).putString(KEY_OFF_REASON, reason)
            .remove(KEY_FALLBACKS).apply()
    }

    /** Почему libusb выключен на этом телефоне; null — не выключен (или выключался в прошлой версии программы). */
    private fun disabledReason(context: Context): String? {
        val prefs = prefs(context)
        val version = prefs.getInt(KEY_OFF_VERSION, -1)
        if (version == -1) return null
        if (version != BuildConfig.VERSION_CODE) {
            prefs.edit().remove(KEY_OFF_VERSION).remove(KEY_OFF_REASON).apply()
            return null
        }
        return prefs.getString(KEY_OFF_REASON, null) ?: "сбои"
    }

    private fun serverSetting(context: Context): String = try {
        JSONObject(File(context.filesDir, CLIENT_CONFIG).readText()).optJSONObject("settings")
            ?.optString(SERVER_SETTING, "").orEmpty()
    } catch (_: Exception) {
        ""
    }

    private fun prefs(context: Context): SharedPreferences = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
