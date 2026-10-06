package ru.magicsqd.mobile.usb

/**
 * Changan (прошивка changan_car — UNI-K, CS35 Plus New и родня): команды shell спрашивают пароль подтверждения со
 * своего stdin («please input verify password:»). Наш поток shell: stdin не закрывает — запрос ждёт до таймаута чтения
 * (у pm install — 2 мин на каждый способ), и ни одной установки на этих магнитолах так и не было (логи №3524–3529;
 * на ПК — №1073, там «verify failed!»). Пароль известен (инструкции моделей Changan, владелец 06.10.2026: «adb369875
 * или adb36987»). Подаём его первой строкой stdin команды. Та же логика на ПК — app/adb_utils.py (Adb.shell).
 */
object ShellVerifyPassword {
    val PASSWORDS = listOf("adb369875", "adb36987")

    /** Пароль, который подаём в каждую команду; null — магнитола его не спрашивала. */
    @Volatile var current: String? = null
        private set

    @Volatile private var lastBanner: String? = null

    /** Новое подключение. Магнитоле Changan (по баннеру) пароль подаём сразу — иначе первая же команда ждала бы
     * таймаута; остальным — только если спросит. Переподключение к той же магнитоле найденный пароль не сбрасывает. */
    fun onConnected(banner: String) {
        if (banner == lastBanner) return
        lastBanner = banner
        current = if (banner.contains("changan", ignoreCase = true)) PASSWORDS.first() else null
    }

    fun asks(text: String) = text.contains("verify password", ignoreCase = true)

    fun failed(text: String) = text.contains("verify failed", ignoreCase = true)

    /** Группа — чтобы пароль дошёл и до «a && b», и до «a; b». Строка одна: в команде с несколькими вызовами pm пароль
     * достанется только первому — программа зовёт pm по одному на команду (pm grant, pm install). */
    fun wrap(command: String, password: String) = "echo $password | {\n$command\n}"

    /** Приглашение печатается и при верном пароле — убираем, чтобы разбор вывода (Success, package:…) шёл как без него. */
    fun strip(text: String) = text.replace(Regex("please input verify password:[ \\t]*", RegexOption.IGNORE_CASE), "")

    /** Команда с паролем, если нужен: сначала как есть (или с уже найденным паролем), при запросе — с известными.
     * Не подошёл и найденный — пробуем другие, но найденный не забываем: так бывает и у команды, которой stdin нужен
     * под данные (cat … | pm install -S — пароль туда не подать), а следующим командам он нужен. */
    fun run(command: String, log: (String) -> Unit, exec: (String) -> AdbShellResult): AdbShellResult {
        val known = current
        val first = exec(known?.let { wrap(command, it) } ?: command)
        var output = first as? AdbShellResult.Output ?: return first
        if (if (known == null) asks(output.text) else failed(output.text)) {
            if (known == null) log("Магнитола спрашивает пароль подтверждения команд (Changan) — подаю известный пароль...")
            val found = PASSWORDS.filter { it != known }.firstNotNullOfOrNull { password ->
                val retry = exec(wrap(command, password))
                if (retry is AdbShellResult.Output && !failed(retry.text)) password to retry else null
            }
            if (found != null) {
                current = found.first
                output = found.second
                log("Пароль подтверждения подошёл — дальше подаю его в каждую команду сам.")
            } else if (known == null) {
                log("Известные пароли подтверждения не подошли (${PASSWORDS.joinToString()}).")
            }
        }
        return if (current != null) AdbShellResult.Output(strip(output.text)) else output
    }
}
