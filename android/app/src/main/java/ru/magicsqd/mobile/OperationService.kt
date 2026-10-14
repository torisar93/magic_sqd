package ru.magicsqd.mobile

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat

/**
 * Служба переднего плана на время операции с магнитолой (заливка и установка APK, запись флешки, разрешения…).
 *
 * Разбор логов 14.10.2026: если свернуть программу во время заливки, связь рвалась по Wi-Fi в 29 из 43 случаев (на
 * экране — 24 из 1039), по USB — в 2 из 10 (на экране — 36 из 3356). Свёрнутую программу без службы Android
 * «замораживает», магнитола не дожидается продолжения и закрывает соединение. Со службой процесс остаётся «на переднем
 * плане» с уведомлением «Идёт установка»; WakeLock держит процессор при погасшем экране, WifiLock — Wi-Fi без
 * энергосбережения. Включает и гасит службу [OperationKeeper] из WebBridge.runExclusive (владелец 14.10: «да»).
 *
 * Запуск — обычным startService из видимой программы (техник только что нажал кнопку), а не startForegroundService:
 * у того служба, остановленная раньше startForeground, роняет программу («did not then call startForeground»). Здесь
 * не удалось включиться — строка в журнал, операция идёт как раньше, без службы.
 */
class OperationService : Service() {
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val english = intent?.getBooleanExtra(EXTRA_ENGLISH, false) == true
        try {
            val type = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE else 0
            ServiceCompat.startForeground(this, NOTIFICATION_ID, buildNotification(english), type)
        } catch (e: Exception) {
            // Android 12+ не даёт включить службу, если программа уже ушла в фон (ForegroundServiceStartNotAllowedException).
            OperationKeeper.note("Уведомление «Идёт установка» не включилось (${e.javaClass.simpleName}: ${e.message}) — " +
                "не сворачивайте программу до конца операции: в фоне связь с магнитолой может оборваться.")
            OperationKeeper.serviceStopped(this)
            stopSelf()
            return START_NOT_STICKY
        }
        acquireLocks()
        OperationKeeper.serviceStarted(this)
        return START_NOT_STICKY
    }

    /** Операции кончились (и задержка после них вышла) — снять уведомление и остановиться. */
    fun finish() {
        releaseLocks()
        ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    override fun onDestroy() {
        releaseLocks()
        OperationKeeper.serviceStopped(this)
        super.onDestroy()
    }

    private fun buildNotification(english: Boolean): android.app.Notification {
        val manager = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O && manager != null) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                if (english) "Installing to the head unit" else "Установка на магнитолу",
                NotificationManager.IMPORTANCE_LOW,
            )
            channel.setShowBadge(false)
            manager.createNotificationChannel(channel)
        }
        // Нажатие на уведомление — вернуться в программу (та же задача, что у значка на рабочем столе).
        val open = packageManager.getLaunchIntentForPackage(packageName)
            ?.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
        val pending = open?.let {
            PendingIntent.getActivity(this, 0, it, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_operation)
            .setContentTitle(if (english) "Installation in progress" else "Идёт установка")
            .setContentText(if (english) "Don't close Magic SQD until it finishes." else "Не закрывайте Magic SQD, пока она не закончится.")
            .setCategory(NotificationCompat.CATEGORY_PROGRESS)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setOngoing(true)
            .setSilent(true)
            .setShowWhen(false)
            .apply { if (pending != null) setContentIntent(pending) }
            .build()
    }

    private fun acquireLocks() {
        try {
            val power = getSystemService(POWER_SERVICE) as PowerManager
            wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "MagicSQD:operation").apply {
                setReferenceCounted(false)
                acquire(LOCK_LIMIT_MS)  // предохранитель: даже если finish() не вызовут, отпустится сам
            }
        } catch (_: Exception) {
        }
        try {
            val wifi = applicationContext.getSystemService(WIFI_SERVICE) as WifiManager
            // HIGH_PERF на Android 14+ ничего не делает, там — LOW_LATENCY (действует, пока программа на экране).
            @Suppress("DEPRECATION")
            val mode = if (Build.VERSION.SDK_INT >= 34) WifiManager.WIFI_MODE_FULL_LOW_LATENCY else WifiManager.WIFI_MODE_FULL_HIGH_PERF
            wifiLock = wifi.createWifiLock(mode, "MagicSQD:operation").apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (_: Exception) {
        }
    }

    private fun releaseLocks() {
        try { wakeLock?.takeIf { it.isHeld }?.release() } catch (_: Exception) {}
        try { wifiLock?.takeIf { it.isHeld }?.release() } catch (_: Exception) {}
        wakeLock = null
        wifiLock = null
    }

    companion object {
        const val EXTRA_ENGLISH = "english"
        private const val CHANNEL_ID = "operation"
        private const val NOTIFICATION_ID = 1407
        private const val LOCK_LIMIT_MS = 3L * 60 * 60 * 1000
    }
}

/**
 * Счётчик операций с магнитолой → служба [OperationService]. Состояние меняется только в главном потоке. После
 * последней операции служба ждёт [LINGER_MS]: следующий шаг (интерфейс запускает его сам) не остаётся без защиты,
 * а уведомление не мигает между операциями.
 */
object OperationKeeper {
    private const val LINGER_MS = 20_000L
    private val main = Handler(Looper.getMainLooper())
    private var active = 0
    private var service: OperationService? = null
    private var starting = false

    /** Куда писать строки для журнала установки (WebBridge.pushAdbLog). */
    @Volatile
    var onNote: ((String) -> Unit)? = null

    private val stopLater = Runnable {
        if (active == 0) {
            // Сразу забываем: следующая операция поднимет новую службу, а не будет ждать гаснущую.
            val leaving = service
            service = null
            leaving?.finish()
        }
    }

    fun begin(context: Context, english: Boolean) {
        val app = context.applicationContext
        main.post {
            active++
            main.removeCallbacks(stopLater)
            if (service != null || starting) return@post
            starting = true
            try {
                app.startService(Intent(app, OperationService::class.java).putExtra(OperationService.EXTRA_ENGLISH, english))
            } catch (e: Exception) {
                // IllegalStateException: программа уже в фоне — запускать службы ей нельзя.
                starting = false
                note("Уведомление «Идёт установка» не включилось (${e.javaClass.simpleName}) — не сворачивайте программу до конца операции.")
            }
        }
    }

    fun end() {
        main.post {
            if (active > 0) active--
            if (active == 0) {
                main.removeCallbacks(stopLater)
                main.postDelayed(stopLater, LINGER_MS)
            }
        }
    }

    internal fun serviceStarted(started: OperationService) {
        service = started
        starting = false
        if (active == 0) {
            main.removeCallbacks(stopLater)
            main.postDelayed(stopLater, LINGER_MS)
        }
    }

    internal fun serviceStopped(stopped: OperationService) {
        if (service === stopped || service == null) {
            service = null
            starting = false
        }
    }

    internal fun note(line: String) {
        try { onNote?.invoke(line) } catch (_: Exception) {}
    }
}
