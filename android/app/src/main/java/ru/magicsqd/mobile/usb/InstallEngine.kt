package ru.magicsqd.mobile.usb

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.SynchronousQueue
import java.util.concurrent.TimeUnit

sealed class StageRunResult {
    object Success : StageRunResult()
    data class Failed(val reason: String) : StageRunResult()
}

/**
 * Мост для команды "#ask" (см. wizard_spec.py:parse_adb_line) — исполнение
 * команд идёт на фоновом потоке (см. InstallEngine.runAdbCommands) и должно
 * ДОЖДАТЬСЯ ответа техника, который вводится через WebView-диалог (JS) и
 * приходит обратно через WebBridge.adb_ask_input_response на другом потоке.
 * SynchronousQueue — самый простой способ передать один ответ между двумя
 * потоками без гонок.
 */
object AskInputBroker {
    private val pending = ConcurrentHashMap<String, SynchronousQueue<String>>()

    fun register(requestId: String): SynchronousQueue<String> {
        val q = SynchronousQueue<String>()
        pending[requestId] = q
        return q
    }

    fun resolve(requestId: String, value: String) {
        pending[requestId]?.offer(value)
    }

    fun unregister(requestId: String) {
        pending.remove(requestId)
    }
}

/**
 * Интерпретатор мини-DSL ADB-команд из _wizard_spec.json (см. wizard_spec.py)
 * поверх уже установленного AdbSession — заменяет исполнение stages.py/
 * install.py на десктопе для моделей, созданных мастером "Добавить машину...".
 * Работает на фоновом потоке, вызывающий код (WebBridge) отвечает за это.
 */
class InstallEngine(
    private val context: Context,
    private val log: (String) -> Unit,
    private val requestAskInput: (requestId: String, prompt: String) -> Unit,
) {
    /**
     * commands — JSONArray объектов {"kind": ..., ...}, как возвращает
     * wizard_spec.parse_commands на Python-стороне. filesByName — basename ->
     * абсолютный локальный путь (adb_files этапа, уже скачанные
     * sync_model_payload на Python-стороне).
     */
    fun runAdbCommands(commands: JSONArray, filesByName: Map<String, String>): StageRunResult {
        var lastAsk: String? = null
        for (i in 0 until commands.length()) {
            val cmd = commands.getJSONObject(i)
            when (cmd.getString("kind")) {
                "skip" -> {}

                "sleep" -> {
                    val seconds = cmd.getDouble("seconds")
                    log("Пауза ${seconds}с...")
                    Thread.sleep((seconds * 1000).toLong())
                }

                "reboot" -> {
                    log("Перезагружаю устройство и жду его возвращения (до 90с)...")
                    AdbSession.service("reboot:", log)
                    AdbSession.disconnect()
                    val result = AdbSession.waitForDeviceAndReconnect(context, 90_000, log)
                    if (result !is AdbHandshakeResult.Connected) {
                        return StageRunResult.Failed("Устройство не вернулось после перезагрузки: ${(result as? AdbHandshakeResult.Failed)?.reason}")
                    }
                    log("Устройство снова на связи.")
                }

                "reboot_nowait" -> {
                    log("Перезагружаю устройство (без ожидания)...")
                    AdbSession.service("reboot:", log)
                    AdbSession.disconnect()
                }

                "wait_device" -> {
                    val timeoutMs = ((cmd.optDouble("timeout", 60.0)) * 1000).toLong()
                    if (!AdbSession.isConnected) {
                        val result = AdbSession.waitForDeviceAndReconnect(context, timeoutMs, log)
                        if (result !is AdbHandshakeResult.Connected) {
                            return StageRunResult.Failed("Не дождались устройства: ${(result as? AdbHandshakeResult.Failed)?.reason}")
                        }
                    }
                }

                "ask" -> {
                    val prompt = cmd.getString("prompt")
                    val requestId = UUID.randomUUID().toString()
                    val queue = AskInputBroker.register(requestId)
                    requestAskInput(requestId, prompt)
                    val value = try {
                        queue.poll(5, TimeUnit.MINUTES)
                    } finally {
                        AskInputBroker.unregister(requestId)
                    }
                    if (value == null) return StageRunResult.Failed("Не дождались ответа техника на вопрос: $prompt")
                    lastAsk = value
                }

                "root" -> logResult(AdbSession.service("root:", log))
                "disable_verity" -> logResult(AdbSession.service("disable-verity:", log))
                "remount" -> logResult(AdbSession.service("remount:", log))

                "push" -> {
                    val name = cmd.getString("file")
                    val localPath = filesByName[name]
                        ?: return StageRunResult.Failed("Файл не найден для #push: $name")
                    val bytes = File(localPath).readBytes()
                    val remote = cmd.getString("remote")
                    when (val r = AdbSession.push(bytes, remote, log)) {
                        is AdbPushResult.Failed -> return StageRunResult.Failed(r.reason)
                        AdbPushResult.Success -> log("Файл $name записан на устройство ($remote).")
                    }
                }

                // install_stream (desktop: cat file | pm install -S <size>) — обходной
                // путь для adbd, где обычный `adb install` не работает. У нас всегда
                // один и тот же classic sync-путь (installApkOverAdb), так что оба
                // намеренно ведут себя одинаково — отдельной "потоковой" реализации нет.
                "install", "install_stream" -> {
                    val name = cmd.getString("file")
                    val localPath = filesByName[name]
                        ?: return StageRunResult.Failed("Файл не найден для установки: $name")
                    val bytes = File(localPath).readBytes()
                    when (val r = AdbSession.installApk(bytes, log)) {
                        is AdbInstallResult.Failed -> return StageRunResult.Failed(r.reason)
                        is AdbInstallResult.Success -> log("Установлено: $name")
                    }
                }

                "shell" -> {
                    var command = cmd.getString("command")
                    if (lastAsk != null) command = command.replace("{ask}", lastAsk)
                    when (val r = AdbSession.shell(command, log)) {
                        // Сырой вывод устройства на успешную команду больше не
                        // льём в лог — на цепочках из десятков shell-команд
                        // (например выдача разрешений через pm grant/dumpsys)
                        // это превращало лог в стену технического текста без
                        // единой понятной строки об итоге (тот же фикс, что и
                        // в desktop-версии, см. app/adb_utils.py: Adb.run()).
                        is AdbShellResult.Output -> {}
                        is AdbShellResult.Rejected -> log("Команда отклонена устройством: $command (${r.reason})")
                        is AdbShellResult.Failed -> return StageRunResult.Failed("'$command': ${r.reason}")
                    }
                }

                else -> log("Неизвестный тип команды в _wizard_spec.json: ${cmd.getString("kind")}")
            }
        }
        return StageRunResult.Success
    }

    /** "telnet"-этап: каждая строка commands — отдельный вызов
     * enableAdbViaTelnet с этой строкой как командой (см. TelnetAdb.kt,
     * порт cars/_shared/telnet_adb.py:enable_adb_via_telnet) — НЕ через
     * мини-DSL, raw-строки как есть (см. wizard_spec.py). */
    fun runTelnetCommands(host: String, commands: List<String>): StageRunResult {
        for (command in commands) {
            if (command.isBlank()) continue
            when (val r = enableAdbViaTelnet(host, command = command, log = log)) {
                is TelnetResult.Failed -> return StageRunResult.Failed(r.reason)
                TelnetResult.Success -> {}
            }
        }
        return StageRunResult.Success
    }

    /** Устанавливает список APK (по абсолютным локальным путям) по очереди. */
    fun installApks(apkPaths: List<String>): StageRunResult {
        for (path in apkPaths) {
            val file = File(path)
            if (!file.exists()) return StageRunResult.Failed("Файл не скачан: $path")
            log("Устанавливаю ${file.name}...")
            when (val r = AdbSession.installApk(file.readBytes(), log)) {
                is AdbInstallResult.Failed -> return StageRunResult.Failed("${file.name}: ${r.reason}")
                is AdbInstallResult.Success -> log("Установлено: ${file.name}")
            }
        }
        return StageRunResult.Success
    }

    private fun logResult(result: AdbShellResult) {
        when (result) {
            is AdbShellResult.Output -> if (result.text.isNotBlank()) log(result.text.trim())
            is AdbShellResult.Rejected -> log("Сервис отклонён устройством: ${result.reason}")
            is AdbShellResult.Failed -> log("Ошибка: ${result.reason}")
        }
    }
}
