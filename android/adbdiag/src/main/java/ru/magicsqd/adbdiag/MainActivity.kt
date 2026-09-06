package ru.magicsqd.adbdiag

import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.InetSocketAddress
import java.net.Socket
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Одноразовый инструмент для расследования "можно ли включить ADB прямо с
 * головного устройства, без ноутбука" (см. переписку с Клодом про Monji/
 * MonGuard/NetworkAdbRootRunner) — техник открывает это приложение ПРЯМО НА
 * ЭКРАНЕ магнитолы (не через adb shell) и жмёт кнопки, весь вывод — тут же,
 * текстом на экране, копируется штатным выделением текста (textIsSelectable).
 *
 * Важно: у обычного (не системного/непривилегированного) приложения нет
 * root — большинство setprop/settings put команд ниже, скорее всего,
 * завершатся с ошибкой доступа. Это ожидаемо и само по себе результат —
 * задача этого инструмента не гарантированно включить ADB, а ЧЕСТНО
 * показать, какой именно способ сработал (если хоть один), не выходя из
 * машины и не подключая ноутбук.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var output: TextView
    private val log = StringBuilder()
    private val timeFmt = SimpleDateFormat("HH:mm:ss", Locale.US)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        output = findViewById(R.id.output)
        findViewById<TextView>(R.id.last_boot).text =
            "Последняя попытка при загрузке: ${BootReceiver.lastRunSummary(this)}"

        findViewById<Button>(R.id.btn_refresh).setOnClickListener { refreshStatus() }
        findViewById<Button>(R.id.btn_try_tcp).setOnClickListener { tryTcpPort() }
        findViewById<Button>(R.id.btn_try_wifi_debug).setOnClickListener { tryAdbWifiEnabled() }
        findViewById<Button>(R.id.btn_try_persist).setOnClickListener { tryPersistProperty() }
        findViewById<Button>(R.id.btn_check_port).setOnClickListener { checkPort5555() }

        refreshStatus()
    }

    private fun appendLine(text: String) {
        log.append(text).append('\n')
        output.text = log.toString()
    }

    private fun section(title: String) {
        appendLine("")
        appendLine("--- ${timeFmt.format(Date())} $title ---")
    }

    /** sh -c "<cmd>" от имени нашего же приложения (без root) — не всё,
     * что доступно adb shell, доступно и так, но getprop/settings get —
     * обычно можно читать без спецправ, это и проверяем в первую очередь. */
    private fun runShell(cmd: String): String {
        return try {
            val process = ProcessBuilder("sh", "-c", cmd).redirectErrorStream(true).start()
            val text = BufferedReader(InputStreamReader(process.inputStream)).readText().trim()
            process.waitFor()
            text.ifEmpty { "(пусто)" }
        } catch (e: Exception) {
            "Исключение: ${e.javaClass.simpleName}: ${e.message}"
        }
    }

    private fun refreshStatus() {
        section("Статус")
        appendLine("getprop service.adb.tcp.port = ${runShell("getprop service.adb.tcp.port")}")
        appendLine("getprop persist.sv.debug.adb_enable = ${runShell("getprop persist.sv.debug.adb_enable")}")
        appendLine("getprop persist.sys.usb.adb = ${runShell("getprop persist.sys.usb.adb")}")
        appendLine("settings get global adb_enabled = ${runShell("settings get global adb_enabled")}")
        appendLine("settings get global adb_wifi_enabled = ${runShell("settings get global adb_wifi_enabled")}")
        appendLine("id = ${runShell("id")}")
    }

    private fun tryTcpPort() {
        section("Способ 1: TCP-порт adbd + restart")
        appendLine(runShell("setprop service.adb.tcp.port 5555; setprop ctl.restart adbd"))
        Thread.sleep(1000)
        appendLine("после: service.adb.tcp.port = ${runShell("getprop service.adb.tcp.port")}")
    }

    private fun tryAdbWifiEnabled() {
        section("Способ 2: adb_wifi_enabled через Settings API")
        try {
            Settings.Global.putInt(contentResolver, "adb_wifi_enabled", 1)
            appendLine("Settings.Global.putInt выполнен без исключения.")
        } catch (e: SecurityException) {
            appendLine("SecurityException: ${e.message} (нет WRITE_SECURE_SETTINGS)")
        } catch (e: Exception) {
            appendLine("Исключение: ${e.javaClass.simpleName}: ${e.message}")
        }
        appendLine("после: adb_wifi_enabled = ${runShell("settings get global adb_wifi_enabled")}")
    }

    private fun tryPersistProperty() {
        section("Способ 4: persist.sv.debug.adb_enable")
        appendLine(runShell("setprop persist.sv.debug.adb_enable 1"))
        appendLine("после: persist.sv.debug.adb_enable = ${runShell("getprop persist.sv.debug.adb_enable")}")
    }

    private fun checkPort5555() {
        section("Проверка порта 5555 (localhost)")
        appendLine("Проверяю...")
        Thread {
            val result = try {
                Socket().use { s ->
                    s.connect(InetSocketAddress("127.0.0.1", 5555), 800)
                    "ОТКРЫТ — adbd слушает 5555 локально."
                }
            } catch (e: Exception) {
                "закрыт/недоступен (${e.javaClass.simpleName}: ${e.message})"
            }
            runOnUiThread { appendLine("Порт 5555: $result") }
        }.start()
    }
}
