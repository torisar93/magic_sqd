package ru.magicsqd.mobile.usb

/**
 * Changan (прошивка changan_car — UNI-K, UNI-S, CS35 Plus New, CS75 Plus S311_G1 и родня): пароль подтверждения
 * («please input verify password:») спрашивает САМ СЕАНС adb shell — до любой команды, даже chmod (логи №3524, №5981), —
 * и читает его из потока. На неверный — «verify failed! verify password:» и снова ждёт; команда не запускается вовсе.
 * Поэтому «echo пароль | { команда }» (1.0.62–1.1.8) не срабатывал ни разу: echo — часть команды, а она исполняется
 * только ПОСЛЕ проверки. Разбор 15.10.2026 (владелец: «ChanganInstaller, ChanganTweaker ставят отлично»): они подают
 * пароль adb36987 в stdin adb каждой команды — у нас это ввод в поток команды (runAdbService, stdin). Пароль сеанса
 * проверяется раз на подключение безобидной командой; если пароль спрашивают только pm/cmd — подаётся так же, по
 * запросу. Та же логика на ПК — app/adb_utils.py (Adb.shell).
 */
object ShellVerifyPassword {
    val PASSWORDS = listOf("adb36987", "adb369875")
    const val PROBE = "echo msqd_verify_probe"
    private const val PROBE_MARK = "msqd_verify_probe"
    // Верный пароль сеанс принимает сразу; на неверный ждёт следующего до бесконечности — столько и ждём.
    private const val PROBE_TIMEOUT_MS = 6000

    /** Пароль, который подаём в каждую команду; null — магнитола его не спрашивала. */
    @Volatile var current: String? = null
        private set

    /** Сеанс shell этой магнитолы уже проверен (после подключения). */
    @Volatile private var checked = false

    /** Сеанс спрашивает пароль, а известные не подошли — команды сразу отказываются, а не ждут. */
    @Volatile private var noKnownPassword = false

    @Volatile private var lastBanner: String? = null

    /** Новое подключение — сеанс проверим заново. Переподключение к той же магнитоле найденное не сбрасывает. */
    fun onConnected(banner: String) {
        if (banner == lastBanner) return
        lastBanner = banner
        current = null
        checked = false
        noKnownPassword = false
    }

    fun asks(text: String) = text.contains("verify password", ignoreCase = true)

    fun failed(text: String) = text.contains("verify failed", ignoreCase = true)

    /** Строка пароля в поток команды. */
    fun input(password: String) = "$password\n".toByteArray(Charsets.UTF_8)

    /** Приглашение и «verify success!» печатаются и при верном пароле — убираем, чтобы разбор вывода (Success,
     * package:…) шёл как без него. */
    fun strip(text: String) = text.replace(
        Regex("please input verify password:[ \\t]*(?:verify success!?[ \\t]*(?:\\r?\\n)?)?", RegexOption.IGNORE_CASE), "")

    /** Команда с паролем, если нужен. exec(команда, ввод в поток или null, таймаут чтения или null — обычный).
     * Пароль сеанса — во все команды; пароль только у pm/cmd — по запросу, с известными по очереди. Не подошёл и
     * найденный — пробуем другие, но найденный не забываем: так бывает и у команды, которой stdin нужен под данные
     * (cat … | pm install -S при пароле у pm), а следующим командам он нужен. */
    fun run(command: String, log: (String) -> Unit, exec: (String, ByteArray?, Int?) -> AdbShellResult): AdbShellResult {
        if (!checked) checkSession(log, exec)
        if (noKnownPassword) {
            return AdbShellResult.Failed("Магнитола спрашивает пароль подтверждения на каждую команду, а известные " +
                "пароли (${PASSWORDS.joinToString()}) не подошли — нужен пароль от этой прошивки.")
        }
        val known = current
        val first = exec(command, known?.let(::input), null)
        var output = first as? AdbShellResult.Output ?: return first
        if (if (known == null) asks(output.text) else failed(output.text)) {
            if (known == null) log("Магнитола спрашивает пароль подтверждения команд (Changan) — подаю известный пароль...")
            val found = PASSWORDS.filter { it != known }.firstNotNullOfOrNull { password ->
                val retry = exec(command, input(password), null)
                if (retry is AdbShellResult.Output && !failed(retry.text) && !asks(strip(retry.text))) password to retry
                else null
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

    /** Спрашивает ли сеанс shell пароль: безобидная команда с паролем во вводе — обычная магнитола её просто
     * выполнит (лишняя строка во вводе echo не мешает), Changan — только с верным паролем. Не ответила вовсе —
     * не решаем, проверим при следующей команде. */
    private fun checkSession(log: (String) -> Unit, exec: (String, ByteArray?, Int?) -> AdbShellResult) {
        PASSWORDS.forEachIndexed { index, password ->
            val text = (exec(PROBE, input(password), PROBE_TIMEOUT_MS) as? AdbShellResult.Output)?.text ?: return
            if (!asks(text)) {
                checked = text.contains(PROBE_MARK)  // обычная магнитола
                return
            }
            if (index == 0) log("Магнитола спрашивает пароль подтверждения на каждую команду (Changan) — подаю известный...")
            if (strip(text).contains(PROBE_MARK)) {  // команда выполнилась — пароль подошёл
                current = password
                checked = true
                log("Пароль подтверждения подошёл — дальше подаю его в каждую команду сам.")
                return
            }
        }
        log("Известные пароли подтверждения не подошли (${PASSWORDS.joinToString()}).")
        noKnownPassword = true
        checked = true
    }
}
