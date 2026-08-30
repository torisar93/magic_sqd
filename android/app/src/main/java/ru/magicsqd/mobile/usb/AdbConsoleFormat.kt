package ru.magicsqd.mobile.usb

/**
 * Красивое форматирование вывода мини-консоли ADB (см. WebBridge.kt:
 * adbShellCommand, assets/js/app.js: onLogCmdRun) — сырой протокольный текст
 * Android-команд (pm/am/dumpsys/wm) ничего не говорит человеку, не знакомому
 * с внутренностями системы. Намеренно повторяет (не переиспользует напрямую —
 * см. app/web/api/install_api.py:_format_console_output/_translate_console_error
 * на desktop, та же логика, разный рантайм) те же самые случаи: держать оба
 * места в синхроне вручную при добавлении новых команд.
 *
 * В отличие от desktop-версии здесь НЕТ отдельных adb-команд верхнего уровня
 * (devices/connect/install <путь-на-компьютере>/push/pull) — вся консоль
 * всегда выполняет ровно одну shell-команду на уже подключённом единственном
 * устройстве (см. AdbSession.shell), поэтому набор форматов уже.
 */
object AdbConsoleFormat {

    /** Срезает лишний "adb "/"adb shell "/"shell " в начале — по привычке к
     * настоящему терминалу это иногда дописывают, хотя поле ввода и так
     * выполняет только shell-команды (см. placeholder "shell-команда
     * вручную"). Без этого, например, "shell pm list packages" пытался бы
     * выполниться на устройстве как есть — команды "shell" там нет. */
    fun stripRedundantPrefix(command: String): String {
        var result = command
        val lower = result.lowercase()
        if (lower == "adb") return ""
        if (lower.startsWith("adb shell ")) result = result.substring("adb shell ".length)
        else if (lower.startsWith("adb ")) result = result.substring("adb ".length)
        if (result.lowercase().startsWith("shell ")) result = result.substring("shell ".length)
        return result.trim()
    }

    private val ERROR_TRANSLATIONS = listOf(
        "does not exist" to "Ошибка: указанный компонент (activity/сервис) не найден — проверьте, что " +
            "приложение установлено, и что имя пакета и класса набраны верно (регистр букв важен).",
        "permission denial" to "Ошибка: отказано в доступе — не хватает разрешения на это действие.",
        "unknown package" to "Ошибка: указанный пакет не найден — проверьте имя точно " +
            "(посмотреть можно через \"pm list packages\").",
        "install_failed_already_exists" to "Ошибка: пакет с таким именем уже установлен.",
        "install_failed_insufficient_storage" to "Ошибка: на устройстве не хватает места для установки.",
        "install_failed_version_downgrade" to "Ошибка: нельзя установить более старую версию поверх уже установленной.",
        "install_failed_invalid_apk" to "Ошибка: файл повреждён или не является корректным APK.",
        "install_failed_no_matching_abis" to "Ошибка: это приложение не поддерживает архитектуру процессора этого устройства.",
        "no such file or directory" to "Ошибка: файл или папка не найдены по указанному пути.",
        "not running as root" to "Ошибка: нет root-доступа на этом устройстве.",
        "remount failed" to "Ошибка: не удалось перемонтировать /system на запись (обычно нужен root).",
    )

    private val FAILURE_REGEX = Regex("failure\\s*\\[(\\w+)]", RegexOption.IGNORE_CASE)

    /** None, если известного случая нет — тогда используется исходный текст
     * (см. вызывающий код). */
    fun translateError(text: String): String? {
        val lowered = text.lowercase()
        for ((needle, translation) in ERROR_TRANSLATIONS) {
            if (lowered.contains(needle)) return translation
        }
        FAILURE_REGEX.find(text)?.let { return "Ошибка: команда отклонена системой (код ${it.groupValues[1]})." }
        return null
    }

    /** None, если для этой команды/вывода готового формата ещё нет. */
    fun formatOutput(command: String, output: String): String? {
        val trimmedOutput = output.trim()
        if (trimmedOutput.equals("success", ignoreCase = true)) return "Выполнено успешно."

        val words = command.trim().split(Regex("\\s+"))
        if (words.size >= 2 && words[0].lowercase() == "pm" && words[1].lowercase() == "list"
            && words.getOrNull(2)?.lowercase() == "packages") {
            return formatPackages(output)
        }
        if (words.size >= 2 && words[0].lowercase() == "am" && words[1].lowercase() == "start") {
            return formatAmStart(output)
        }
        if (words.size >= 2 && words[0].lowercase() == "pm" && words[1].lowercase() == "path") {
            return formatPmPath(output)
        }
        if (words.size >= 2 && words[0].lowercase() == "dumpsys" && words[1].lowercase() == "battery") {
            return formatDumpsysBattery(output)
        }
        if (words.size >= 2 && words[0].lowercase() == "wm" && words[1].lowercase() == "size") {
            return formatWmSizeOrDensity(output, "size", "разрешение")
        }
        if (words.size >= 2 && words[0].lowercase() == "wm" && words[1].lowercase() == "density") {
            return formatWmSizeOrDensity(output, "density", "плотность")
        }
        return null
    }

    private fun formatPackages(output: String): String? {
        val names = output.lineSequence()
            .map { it.trim() }
            .filter { it.startsWith("package:") }
            .map { line ->
                val value = line.removePrefix("package:")
                value.substringAfterLast("=", value)
            }
            .sorted()
            .toList()
        if (names.isEmpty()) return null
        return "Установлено пакетов: ${names.size}\n" + names.joinToString("\n") { "  • $it" }
    }

    private fun formatAmStart(output: String): String? {
        if (!output.trim().lowercase().startsWith("starting: intent")) return null
        val match = Regex("(?:act|cmp)=(\\S+)").find(output)
        return if (match != null) "Запущено: ${match.groupValues[1]}" else "Запущено."
    }

    private fun formatPmPath(output: String): String? {
        val line = output.trim().lineSequence().firstOrNull() ?: return null
        if (!line.startsWith("package:")) return null
        return "Путь к APK: ${line.removePrefix("package:")}"
    }

    private val BATTERY_STATUS = mapOf(
        "1" to "неизвестно", "2" to "заряжается", "3" to "разряжается",
        "4" to "не заряжается", "5" to "заряжена полностью",
    )
    private val BATTERY_HEALTH = mapOf(
        "1" to "неизвестно", "2" to "в порядке", "3" to "перегрев", "4" to "неисправна",
        "5" to "перенапряжение", "6" to "сбой", "7" to "переохлаждение",
    )

    private fun formatDumpsysBattery(output: String): String? {
        val fields = Regex("^\\s*([\\w ]+?):\\s*(\\S+)\\s*$", RegexOption.MULTILINE)
            .findAll(output)
            .associate { it.groupValues[1] to it.groupValues[2] }
        val level = fields["level"] ?: return null
        val status = BATTERY_STATUS[fields["status"]] ?: fields["status"] ?: "?"
        val health = BATTERY_HEALTH[fields["health"]] ?: fields["health"] ?: "?"
        val parts = mutableListOf("Заряд: $level%", "статус: $status", "состояние: $health")
        fields["temperature"]?.toIntOrNull()?.let { parts.add("температура: ${it / 10.0}°C") }
        fields["voltage"]?.let { parts.add("напряжение: $it мВ") }
        return parts.joinToString(" · ")
    }

    private fun formatWmSizeOrDensity(output: String, field: String, ruLabel: String): String? {
        val physical = Regex("physical $field:\\s*(\\S+)", RegexOption.IGNORE_CASE).find(output)
        val override = Regex("override $field:\\s*(\\S+)", RegexOption.IGNORE_CASE).find(output)
        if (physical == null && override == null) return null
        val parts = mutableListOf<String>()
        physical?.let { parts.add("физическ${if (ruLabel == "разрешение") "ое" else "ая"} $ruLabel: ${it.groupValues[1]}") }
        override?.let { parts.add("установленн${if (ruLabel == "разрешение") "ое" else "ая"} $ruLabel: ${it.groupValues[1]}") }
        val text = parts.joinToString(" · ") + "."
        return text.replaceFirstChar { it.uppercase() }
    }
}
