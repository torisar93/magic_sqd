package ru.magicsqd.mobile.usb

import java.io.File

/**
 * Порт cars/_shared/adb_permissions.py:grant_all_permissions на Kotlin —
 * нужен для installApkViaLocalinstall/installApkViaDexShell (см. AdbInstall.kt):
 * после установки не штатным adb install приложению не выдано вообще никаких
 * разрешений, поэтому выдаём их сами. Изначально сознательно НЕ включал
 * автовключение спецвозможностей/доступа к уведомлениям (десктоп это умел, тут
 * — нет) — но именно этот пробел и стал реальным багом: на Geely Cityray/Monji
 * (лог с "CNXN отправлен"/"OPEN отправлен" — это Android-приложение, не
 * десктоп, см. UsbAdbTransport.kt) техник получил "Спецвозможности: нужно
 * открыть" и "Доступ к уведомлениям: нужно открыть" в самом приложении даже
 * после "Разрешения выданы" в логе. Теперь портирована полная логика.
 */
object AdbPermissions {
    private val COMMON_DANGEROUS_PERMISSIONS = listOf(
        "android.permission.CAMERA",
        "android.permission.RECORD_AUDIO",
        "android.permission.ACCESS_FINE_LOCATION",
        "android.permission.ACCESS_COARSE_LOCATION",
        "android.permission.ACCESS_BACKGROUND_LOCATION",
        "android.permission.READ_CONTACTS",
        "android.permission.WRITE_CONTACTS",
        "android.permission.READ_CALENDAR",
        "android.permission.WRITE_CALENDAR",
        "android.permission.READ_SMS",
        "android.permission.SEND_SMS",
        "android.permission.RECEIVE_SMS",
        "android.permission.READ_PHONE_STATE",
        "android.permission.READ_PHONE_NUMBERS",
        "android.permission.CALL_PHONE",
        "android.permission.READ_CALL_LOG",
        "android.permission.WRITE_CALL_LOG",
        "android.permission.READ_EXTERNAL_STORAGE",
        "android.permission.WRITE_EXTERNAL_STORAGE",
        "android.permission.READ_MEDIA_IMAGES",
        "android.permission.READ_MEDIA_VIDEO",
        "android.permission.READ_MEDIA_AUDIO",
        "android.permission.BODY_SENSORS",
        "android.permission.ACTIVITY_RECOGNITION",
        "android.permission.POST_NOTIFICATIONS",
        "android.permission.BLUETOOTH_CONNECT",
        "android.permission.BLUETOOTH_SCAN",
        "android.permission.BLUETOOTH_ADVERTISE",
        "android.permission.NEARBY_WIFI_DEVICES",
    )

    private const val MANAGE_EXTERNAL_STORAGE_OP = "MANAGE_EXTERNAL_STORAGE"

    // "Спецдоступы" (не обычные runtime-разрешения — pm grant их не
    // выдаёт), управляются через appops. MANAGE_EXTERNAL_STORAGE ("доступ ко
    // всем файлам") — реальный баг, найденный на Geely Cityray/Monji (десктоп-
    // версия, см. cars/_shared/adb_permissions.py): pm grant для него молча
    // ничего не даёт (появился как appops-доступ с Android 11), выдаём через
    // appops, как и остальные три ниже.
    private val APPOPS_BY_PERMISSION = mapOf(
        "android.permission.SYSTEM_ALERT_WINDOW" to "SYSTEM_ALERT_WINDOW",
        "android.permission.WRITE_SETTINGS" to "WRITE_SETTINGS",
        "android.permission.PACKAGE_USAGE_STATS" to "GET_USAGE_STATS",
        "android.permission.MANAGE_EXTERNAL_STORAGE" to MANAGE_EXTERNAL_STORAGE_OP,
    )
    // ACCESS_RESTRICTED_SETTINGS — с Android 13 система блокирует включение
    // спецвозможностей/доступа к уведомлениям для приложений, поставленных не
    // через "доверенный" магазин (актуально для всего, что ставит эта
    // программа) — без него enableAccessibilityService/enableNotificationListener
    // ниже пишут нужные settings, но система их не применяет.
    // + SCHEDULE_EXACT_ALARM/RUN_ANY_IN_BACKGROUND/RUN_IN_BACKGROUND/MANAGE_MEDIA —
    // см. cars/_shared/adb_permissions.py (_EXTRA_APPOPS): точные будильники,
    // фон без ограничений, изменение медиафайлов без подтверждения. Незнакомый
    // прошивке op молча не применится, остальные сработают.
    private val EXTRA_APPOPS = listOf(
        "REQUEST_INSTALL_PACKAGES", "ACTIVATE_VPN", "ACCESS_RESTRICTED_SETTINGS",
        "SCHEDULE_EXACT_ALARM", "RUN_ANY_IN_BACKGROUND", "RUN_IN_BACKGROUND", "MANAGE_MEDIA",
    )
    private const val WRITE_SECURE_SETTINGS = "android.permission.WRITE_SECURE_SETTINGS"

    private val REQUESTED_PERMISSIONS_HEADER = Regex("^requested permissions:\\s*$")
    private val PERMISSION_LINE = Regex("^([\\w.]+)(?::.*)?$")
    private val COMPONENT_NAME_RE = Regex("name=([\\w.]+)")
    // Компонент в коротком виде ("пакет/.Класс") — так он стоит в "Service
    // Resolver Table" реального dumpsys package, в той же строке, что и
    // разрешение службы (см. cars/_shared/adb_permissions.py:_SHORT_COMPONENT_RE).
    private val SHORT_COMPONENT_RE = Regex("([\\w.]+)/([\\w.$]+)")

    private fun parseRequestedPermissions(dumpsysOutput: String): List<String> {
        val result = mutableListOf<String>()
        var inBlock = false
        for (raw in dumpsysOutput.lineSequence()) {
            val line = raw.trim()
            if (!inBlock) {
                if (REQUESTED_PERMISSIONS_HEADER.matches(line)) inBlock = true
                continue
            }
            if (line.isEmpty()) break
            val m = PERMISSION_LINE.matchEntire(line)
            if (m != null && m.groupValues[1].contains(".")) {
                result.add(m.groupValues[1])
            } else {
                break
            }
        }
        return result
    }

    private fun fullClassName(pkg: String, cls: String): String = when {
        cls.startsWith(".") -> pkg + cls
        "." !in cls -> "$pkg.$cls"
        else -> cls
    }

    /** Best-effort поиск класса службы (спецвозможности/слушателя
     * уведомлений — marker разный, логика одна) в dumpsys package — портовая
     * копия cars/_shared/adb_permissions.py:_find_service_component (там же
     * подробности). Основной путь — компонент в той же строке, что и marker
     * (реальный формат Android 8+); раньше был только поиск "name=..." в
     * строках выше, которого в реальном dumpsys нет, — служба не находилась
     * ни у одного приложения (жалоба 2026-09-23, Haval Jolion 2026). Старый
     * разбор оставлен запасным. Если не нашли, вызывающий честно логирует
     * это, а не молча пропускает. */
    private fun findServiceComponent(pkg: String, dumpsysOutput: String, marker: String): String? {
        val lines = dumpsysOutput.lines()
        for (line in lines) {
            if (marker !in line) continue
            for (m in SHORT_COMPONENT_RE.findAll(line)) {
                if (m.groupValues[1] == pkg) return "$pkg/${fullClassName(pkg, m.groupValues[2])}"
            }
        }
        for (i in lines.indices) {
            if (marker !in lines[i]) continue
            for (j in (i - 1) downTo maxOf(0, i - 15)) {
                val m = COMPONENT_NAME_RE.find(lines[j].trim()) ?: continue
                return "$pkg/${fullClassName(pkg, m.groupValues[1])}"
            }
        }
        return null
    }

    /** Портовая копия cars/_shared/adb_permissions.py:_service_declared — у службы в dumpsys строка
     * «… filter … permission android.permission.BIND_…» (у старых прошивок «permission=…»); строка из
     * «requested permissions» — не служба. */
    private fun serviceDeclared(dumpsysOutput: String, marker: String): Boolean =
        Regex("\\bpermission[\\s=]+android\\.permission\\.$marker\\b").containsMatchIn(dumpsysOutput)

    /** Портовая копия cars/_shared/adb_permissions.py:_enable_accessibility_service. Службы нет — молчим
     * (у большинства приложений её нет, раньше строка «не найдена в dumpsys» шла у каждого и выглядела как
     * сбой, лог #799); объявлена, но не разобрали — предупреждаем. */
    private fun enableAccessibilityService(pkg: String, dumpsysOutput: String, log: (String) -> Unit) {
        val component = findServiceComponent(pkg, dumpsysOutput, "BIND_ACCESSIBILITY_SERVICE")
        if (component == null) {
            if (serviceDeclared(dumpsysOutput, "BIND_ACCESSIBILITY_SERVICE")) {
                log("Внимание: у $pkg есть служба спецвозможностей, но определить её не удалось — " +
                    "включите её вручную (Настройки → Спецвозможности).")
            }
            return
        }
        val current = shellText("settings get secure enabled_accessibility_services", log).trim()
        val existing = if (current.isNotEmpty() && current != "null") {
            current.split(":").filter { it.isNotEmpty() }.toMutableList()
        } else mutableListOf()
        if (component !in existing) {
            existing.add(component)
            safeShell("settings put secure enabled_accessibility_services ${existing.joinToString(":")}", log)
        }
        safeShell("settings put secure accessibility_enabled 1", log)
        log("Служба специальных возможностей включена: $component")
    }

    /** Портовая копия cars/_shared/adb_permissions.py:_enable_notification_listener.
     * cmd notification allow_listener — более новый и надёжный путь (Android
     * 11+), settings put secure enabled_notification_listeners — для более
     * старых прошивок, где этой команды ещё нет. См. ACCESS_RESTRICTED_SETTINGS
     * в EXTRA_APPOPS — без него на Android 13+ ни то, ни другое не
     * применяется для приложений, поставленных не через "доверенный" магазин. */
    private fun enableNotificationListener(pkg: String, dumpsysOutput: String, log: (String) -> Unit) {
        val component = findServiceComponent(pkg, dumpsysOutput, "BIND_NOTIFICATION_LISTENER_SERVICE")
        if (component == null) {
            if (serviceDeclared(dumpsysOutput, "BIND_NOTIFICATION_LISTENER_SERVICE")) {
                log("Внимание: у $pkg есть служба доступа к уведомлениям, но определить её не удалось — " +
                    "включите доступ вручную (Настройки → Уведомления → Доступ к уведомлениям).")
            }
            return
        }
        val current = shellText("settings get secure enabled_notification_listeners", log).trim()
        val existing = if (current.isNotEmpty() && current != "null") {
            current.split(":").filter { it.isNotEmpty() }.toMutableList()
        } else mutableListOf()
        if (component !in existing) {
            existing.add(component)
            safeShell("settings put secure enabled_notification_listeners ${existing.joinToString(":")}", log)
        }
        safeShell("cmd notification allow_listener $component", log)
        log("Доступ к уведомлениям включён: $component")
    }

    private fun shellText(command: String, log: (String) -> Unit, timeoutMs: Int = 5000): String =
        when (val r = safeShell(command, log, timeoutMs)) {
            is AdbShellResult.Output -> r.text
            else -> ""
        }

    /** AdbSession.shell без исключения наружу. Связь умерла прямо на команде — ответа на открытие потока нет,
     * и AdbLinkLostException раньше вылетал из потока операции и ронял всю программу (лог #1266: «Выдать
     * разрешения» com.maxinf.car на Atlas New Monji, 1.0.42). Отмечаем связь потерянной: следующие команды
     * не ждут каждая свой таймаут, а выдача честно пишет «связь с магнитолой потеряна». */
    private fun safeShell(command: String, log: (String) -> Unit, timeoutMs: Int = 5000): AdbShellResult {
        if (!AdbSession.isConnected) return AdbShellResult.Failed("связь с магнитолой потеряна")
        return try {
            AdbSession.shell(command, log, timeoutMs)
        } catch (e: AdbLinkLostException) {
            AdbSession.markLinkLost()
            AdbShellResult.Failed(e.message ?: "связь с магнитолой потеряна")
        }
    }

    /** Список установленных на магнитоле пакетов (для выбора приложения
     * перед grantAllPermissions/setMockLocationApp ниже) — портовая копия
     * cars/_shared/adb_permissions.py:list_installed_packages. thirdPartyOnly
     * — без системных пакетов производителя/Android (иначе список из сотен
     * записей неудобно листать ради обычно нужных технику сторонних APK). */
    fun listInstalledPackages(thirdPartyOnly: Boolean, log: (String) -> Unit): List<String> {
        val packages = pmPackages(if (thirdPartyOnly) "-3" else "", log).toMutableList()
        if (thirdPartyOnly) {
            // Поставленные программой в /system/app — тоже в списке, если Android их уже видит (после перезагрузки).
            val ours = systemAppsByUs(log)
            if (ours.isNotEmpty()) {
                val system = pmPackages("-s", log).toSet()
                packages += ours.filter { it in system && it !in packages }
            }
        }
        return packages.sorted()
    }

    private fun pmPackages(flag: String, log: (String) -> Unit): List<String> =
        shellText("pm list packages $flag".trim(), log).lineSequence()
            .map { it.trim() }
            .filter { it.startsWith("package:") }
            .map { it.removePrefix("package:").trim() }
            .filter { it.isNotEmpty() }
            .toList()

    // Метка в /system/app/<пакет>/ — одна строка: «magicsqd» или «magicsqd mock_location» (GPS-приложение).
    private const val SYSTEM_APP_MARKERS_COMMAND =
        "for f in /system/app/*/$SYSTEM_APP_MARKER; do [ -f \"\$f\" ] && echo \"\$f \$(cat \"\$f\")\"; done"
    private val OUR_SYSTEM_APP = Regex("^/system/app/([\\w.]+)/\\.magicsqd(?:[ \\t]+(.*))?$", RegexOption.MULTILINE)

    /** Приложения, которые программа сама положила в /system/app (способ «в системную папку», BAIC U5 Plus): пакет →
     * текст метки. Android считает их системными, но это не штатные приложения магнитолы: их можно запускать,
     * выдавать разрешения, отключать и удалять. Как ПК: adb_permissions._system_app_markers. */
    private fun systemAppMarkers(log: (String) -> Unit): Map<String, String> =
        OUR_SYSTEM_APP.findAll(shellText(SYSTEM_APP_MARKERS_COMMAND, log, 30_000))
            .associate { it.groupValues[1] to it.groupValues[2].trim() }

    fun systemAppsByUs(log: (String) -> Unit): Set<String> = systemAppMarkers(log).keys

    /** Все разрешения сразу всем приложениям, записанным программой в /system/app. Android видит их только после
     * перезагрузки, а ADB на BAIC U5 Plus её не переживает — поэтому это отдельный этап после перезагрузки и повторного
     * включения ADB. GPS-приложению с меткой mock_location — ещё и фиктивное местоположение. null — готово, иначе
     * причина. Как ПК: adb_permissions.grant_system_apps_permissions. */
    fun grantSystemApps(log: (String) -> Unit): String? {
        val markers = systemAppMarkers(log)
        if (!AdbSession.isConnected) return "Связь с магнитолой потеряна — переподключитесь и повторите."
        if (markers.isEmpty()) {
            return "На магнитоле нет приложений, записанных программой в системную папку — сначала этап установки."
        }
        val known = pmPackages("-s", log).toSet()
        val waiting = markers.keys.filter { it !in known }.sorted()
        for (pkg in markers.keys.sorted()) {
            if (pkg in waiting) continue
            grantAllPermissions(pkg, log)
            if ("mock_location" in markers.getValue(pkg).split(" ")) setMockLocationApp(pkg, log)
        }
        if (waiting.isNotEmpty()) {
            return "Android ещё не видит ${waiting.joinToString(", ")} — приложения появятся после перезагрузки " +
                "магнитолы. Перезагрузите её, снова включите ADB в инженерном меню и повторите."
        }
        return null
    }

    /** pm uninstall системное не удаляет — для поставленного программой в /system/app нужны root и запись в
     * системный раздел, как при установке. Android забудет приложение после перезагрузки. */
    private fun removeSystemAppByUs(context: android.content.Context, pkg: String, log: (String) -> Unit) {
        AdbSession.rootAndReconnect(context, log)?.let { log("Не удалось удалить: $it."); return }
        AdbSession.remountSystem(log)?.let { log("Не удалось удалить: $it."); return }
        safeShell("rm -rf /system/app/$pkg", log, 60_000)
        if (shellText("[ -e /system/app/$pkg ] && echo LEFT", log).contains("LEFT")) {
            log("Не удалось удалить: файлы приложения остались в /system/app.")
            return
        }
        log("Готово: приложение удалено из системной папки — оно исчезнет с магнитолы после перезагрузки.")
    }

    /** Назначает приложение "приложением для фиктивных местоположений"
     * (имитация GPS, как в Настройки → Для разработчиков) и включает саму
     * возможность — портовая копия cars/_shared/adb_permissions.py:
     * set_mock_location_app. appops — актуальный механизм (Android 6+);
     * settings put secure mock_location — для более старых прошивок, где
     * appops эту операцию не знает. */
    fun setMockLocationApp(pkg: String, log: (String) -> Unit) {
        log("Приложение для фиктивных местоположений: $pkg")
        safeShell("appops set $pkg android:mock_location allow", log)
        safeShell("settings put secure mock_location 1", log)
        log(if (AdbSession.isConnected) "Готово." else "Не удалось: связь с магнитолой потеряна.")
    }

    /** Запускает главную activity приложения (как тап по иконке в лаунчере) — портовая копия
     * cars/_shared/adb_permissions.py:launch_main_activity («monkey -c android.intent.category.LAUNCHER» сам
     * находит launcher-activity). Раньше всегда писалось «Готово.», что бы ни ответила магнитола: у техника на
     * Geely Preface приложения «не запускались», а в журнале — «Готово.» по десять раз (лог #1348: GInputBridge
     * четыре раза подряд; у MicroG значка для запуска нет вовсе). Теперь: нет значка — так и пишем; monkey не
     * сработал — запускаем activity напрямую (am start); через полторы секунды смотрим, есть ли приложение на
     * экране, и если нет — что там вместо него. */
    fun launchMainActivity(pkg: String, log: (String) -> Unit) {
        log("Запускаю приложение: $pkg")
        val monkey = shellText("monkey -p $pkg -c android.intent.category.LAUNCHER 1", log)
        if (!AdbSession.isConnected) { log("Не удалось: связь с магнитолой потеряна."); return }
        if (monkey.contains("No activities found")) {
            log("Не удалось запустить: у приложения нет значка для запуска — оно работает в фоне " +
                "или открывается из другого приложения.")
            return
        }
        if (!monkey.contains("Events injected: 1")) {
            val resolved = shellText("cmd package resolve-activity --brief -a android.intent.action.MAIN " +
                "-c android.intent.category.LAUNCHER $pkg", log)
            val component = resolved.lines().map { it.trim() }.lastOrNull { it.contains("/") && !it.contains(" ") }
            if (component == null) {
                val answer = monkey.trim().lines().lastOrNull()?.take(200)?.takeIf { it.isNotBlank() } ?: "магнитола не ответила"
                log("Не удалось запустить: $answer")
                return
            }
            val started = shellText("am start -n $component", log)
            if (started.contains("Error")) {
                log("Не удалось запустить: ${started.trim().lines().last().take(200)}")
                return
            }
        }
        Thread.sleep(1500)
        val screen = onScreen(pkg, log)
        when {
            screen == null || screen.first == 0 -> log("Готово.")
            screen.first != null -> log("Готово: приложение открылось на дополнительном экране магнитолы " +
                "(экран ${screen.first}), а не на основном.")
            else -> {
                val now = if (screen.second.isNotEmpty()) " (на экране: ${screen.second.take(2).joinToString(", ")})" else ""
                log("Команда запуска прошла, но через полторы секунды приложения на экране нет$now. Если оно не " +
                    "открылось — его закрывает прошивка магнитолы или у него нет окна.")
            }
        }
    }

    /** Что сейчас на экранах магнитолы: (номер экрана с нашим приложением или null, приложения на основном).
     * null — магнитола не ответила. «Display #N» — заголовок экрана, mResumedActivity (Android 9) /
     * ResumedActivity (10+) — приложение на нём; topResumedActivity не берём — он печатается после всех экранов. */
    private fun onScreen(pkg: String, log: (String) -> Unit): Pair<Int?, List<String>>? {
        val out = shellText("dumpsys activity activities | grep -E 'Display #|ResumedActivity'", log)
        if (out.isBlank()) return null
        var display = 0
        var ours: Int? = null
        val main = mutableListOf<String>()
        for (line in out.lines()) {
            val header = Regex("Display #(\\d+)").find(line)
            if (header != null) { display = header.groupValues[1].toInt(); continue }
            if (!line.contains("ResumedActivity") || line.contains("topResumedActivity")) continue
            val component = Regex("u\\d+ (\\S+/\\S+)").find(line)?.groupValues?.get(1) ?: continue
            if (display == 0 && component !in main) main.add(component)
            if (ours == null && component.startsWith("$pkg/")) ours = display
        }
        return ours to main
    }

    /** Удаляет приложение (pm uninstall) — портовая копия
     * cars/_shared/adb_permissions.py:uninstall_app. В отличие от
     * disable_app (которого на Android пока нет вовсе) стирает APK
     * полностью; для системных/предустановленных пакетов без root обычно
     * не сработает — раньше здесь безусловно писалось "Готово." независимо
     * от реального ответа устройства (см. лог #536: "pm uninstall android"
     * — ядро системы, заведомо защищено — тоже отчиталось "Готово.", хотя
     * pm его отклонила). Настоящий результат — по тексту ("Success"/
     * "Failure [...]"), тот же приём, что и на десктопе. */
    fun uninstallApp(context: android.content.Context, pkg: String, log: (String) -> Unit) {
        log("Удаляю приложение: $pkg")
        if (pkg in systemAppsByUs(log)) { removeSystemAppByUs(context, pkg, log); return }
        when (val r = removePackage(pkg, log, uninstallHelper(context))) {
            Removal.Removed -> log("Готово.")
            Removal.Blocked -> log("Не удалось удалить: $MANUAL_REMOVAL")
            is Removal.Failed -> log("Не удалось удалить: ${r.reason}")
        }
    }

    /** Итог удаления одного пакета. Blocked — прошивка не даёт удалять через ADB: pm закрыт, поток сразу
     *  закрывается (Geely OneOS/Monji, Jetour T2 — логи #797, #962); владелец (2026-09-25): пусть удаляют
     *  штатно на самой магнитоле. */
    sealed class Removal {
        object Removed : Removal()
        object Blocked : Removal()
        data class Failed(val reason: String) : Removal()
    }

    const val MANUAL_REMOVAL = "эта магнитола не даёт удалять приложения через программу — " +
        "удалите штатно на самой магнитоле (Настройки → Приложения)."

    /** pm uninstall без записи в лог итога — общий для кнопки «Удалить приложение» и «Откатить в сток». Прошивка не
     * пускает pm (поток сразу закрывается: Geely OneOS/Monji, VOLGA/N155) — удаляем dex-хелпером (removeViaHelper),
     * и только если не вышло — Blocked («удалите штатно»). helper — cars/_shared/uninstall_helper.dex. */
    fun removePackage(pkg: String, log: (String) -> Unit, helper: File? = null): Removal =
        when (val r = safeShell("pm uninstall $pkg", log)) {
            is AdbShellResult.Output -> {
                val text = r.text.trim()
                if (text.contains("success", ignoreCase = true) && !text.contains("failure", ignoreCase = true)) Removal.Removed
                else Removal.Failed(text.ifBlank { "устройство не ответило" })
            }
            is AdbShellResult.Rejected -> if (helper != null) removeViaHelper(pkg, helper, log) else Removal.Blocked
            is AdbShellResult.Failed -> Removal.Failed(r.reason)
        }

    const val UNINSTALL_HELPER = "uninstall_helper.dex"
    private const val UNINSTALL_HELPER_REMOTE = "/data/local/tmp/uninstall_helper.dex"
    private val PACKAGE_NAME = Regex("[A-Za-z0-9_.]+")

    fun uninstallHelper(context: android.content.Context): File = File(context.filesDir, "cars/_shared/$UNINSTALL_HELPER")

    /** Удалить dex-хелпером, минуя pm (ctx.uninstall_via_helper общего Python-кода, PyCtxBridge). true — удалено. */
    fun removePackageViaHelper(context: android.content.Context, pkg: String, log: (String) -> Unit): Boolean =
        removeViaHelper(pkg, uninstallHelper(context), log) == Removal.Removed

    /** Наш dex-хелпер удаления (исходник — helpers/uninstall_helper): app_process от имени shell →
     * PackageInstaller.uninstall. Прошивки, закрывшие pm, пропускают app_process — так же ставит хелпер установки.
     * Откат в сток на N155 упирался в «удалите штатно» (логи №797, №962, №1664, №1746). Проверено на эмуляторе
     * Android 9: установленное — «Success», штатное — «Failure [DELETE_FAILED_INTERNAL_ERROR]». ПК —
     * app/uninstall_helper.py. */
    private fun removeViaHelper(pkg: String, helper: File, log: (String) -> Unit): Removal {
        if (!PACKAGE_NAME.matches(pkg)) return Removal.Blocked
        if (!helper.isFile) {
            log("dex-хелпер не удалил: нет файла $UNINSTALL_HELPER — обновите каталог.")
            return Removal.Blocked
        }
        log("Магнитола не пускает pm uninstall — удаляю через dex-хелпер...")
        val pushed = try {
            AdbSession.push(PushSource.of(helper), UNINSTALL_HELPER_REMOTE, log)
        } catch (e: AdbLinkLostException) {
            AdbSession.markLinkLost()
            return Removal.Failed(e.message ?: "связь с магнитолой потеряна")
        }
        if (pushed is AdbPushResult.Failed) return Removal.Failed(pushed.reason)
        safeShell("chmod 644 $UNINSTALL_HELPER_REMOTE", log)
        val text = when (val r = safeShell(
            "CLASSPATH=$UNINSTALL_HELPER_REMOTE app_process /data/local/tmp MagicSqdUninstaller $pkg", log, 90_000)) {
            is AdbShellResult.Output -> r.text.trim()
            is AdbShellResult.Rejected -> "команда отклонена: ${r.reason}"
            is AdbShellResult.Failed -> return Removal.Failed(r.reason)
        }
        safeShell("rm -f $UNINSTALL_HELPER_REMOTE", log)
        if (text.contains("success", ignoreCase = true) && !text.contains("failure", ignoreCase = true)) return Removal.Removed
        log("dex-хелпер не удалил: ${text.ifBlank { "хелпер не ответил" }}")
        return Removal.Blocked
    }

    /** Отключить/включить приложение — то же, что desktop cars/_shared/
     * adb_permissions.py: disable_app/enable_app (до 2026-09-23 на Android
     * этих кнопок не было вовсе — «Доступно только в версии для Windows»).
     * Об успехе pm сообщает строкой «Package <пакет> new state: ...». */
    fun disableApp(pkg: String, log: (String) -> Unit) {
        log("Отключаю приложение: $pkg")
        setEnabledState("pm disable-user --user 0 $pkg", "отключить", log)
    }

    fun enableApp(pkg: String, log: (String) -> Unit) {
        log("Включаю приложение: $pkg")
        setEnabledState("pm enable $pkg", "включить", log)
    }

    private fun setEnabledState(command: String, failVerb: String, log: (String) -> Unit) {
        val text = when (val r = safeShell(command, log)) {
            is AdbShellResult.Output -> r.text
            is AdbShellResult.Rejected -> "Команда отклонена устройством: ${r.reason}"
            is AdbShellResult.Failed -> r.reason
        }
        if (text.contains("new state", ignoreCase = true)) {
            log("Готово.")
        } else {
            log("Не удалось $failVerb: ${text.ifBlank { "устройство не ответило" }}")
        }
    }

    // Когда пакету в последний раз выдавали разрешения — чтобы автовыдача после
    // установки (InstallEngine) не дублировала инлайн-выдачу способов localinstall/
    // dex_shell (AdbInstall.kt), которые выдают ДО первого запуска приложения.
    private val lastGrantAt = java.util.concurrent.ConcurrentHashMap<String, Long>()

    fun grantedSince(pkg: String, sinceMillis: Long): Boolean = (lastGrantAt[pkg] ?: 0L) >= sinceMillis

    /** Выдаёт пакету все разрешения, которые он запрашивает в манифесте
     * (см. dumpsys), плюс WRITE_SECURE_SETTINGS и appops-спецдоступы — то,
     * что на большинстве магнитол нельзя дать через штатный экран настроек.
     * Плюс, если удалось найти в dumpsys, включает службу специальных
     * возможностей и доступ к уведомлениям. Плюс освобождает от ограничений
     * энергосбережения (Doze). */
    fun grantAllPermissions(pkg: String, log: (String) -> Unit) {
        lastGrantAt[pkg] = System.currentTimeMillis()
        log("Выдаю разрешения: $pkg")
        // Кольцо окна установки — «Выдача разрешений», дальше ход по шагам: у крупных приложений тут десятки
        // команд, и под надписью «Установка приложения» окно выглядело зависшим (владелец, 2026-09-28).
        AdbInstallProgress.granting()
        // dumpsys package у крупных приложений выдаёт сотни КБ и не укладывается
        // в общие 5 с AdbSession.shell — вывод обрывается, и список запрошенных
        // разрешений получается неполным.
        val dumpsysOutput = shellText("dumpsys package $pkg", log, 20_000)
        // Связь пропала ещё до первой команды: раньше ни одна команда не уходила, а в журнале всё равно
        // было «Разрешения выданы.» (логи #721, #910, #966 — после переустановки Podpratel Pro).
        if (!AdbSession.isConnected) { lastGrantAt.remove(pkg); log(linkLostMessage(pkg)); return }
        val requested = parseRequestedPermissions(dumpsysOutput).ifEmpty { COMMON_DANGEROUS_PERMISSIONS }
        val toGrant = requested.filter { it !in APPOPS_BY_PERMISSION } // APPOPS_BY_PERMISSION — ниже через appops
        // dumpsys; pm grant; appops (+ --uid для MANAGE_EXTERNAL_STORAGE); доп. appops; WRITE_SECURE_SETTINGS, Doze,
        // спецвозможности, уведомления — ровно столько вызовов step() ниже (как cars/_shared/adb_permissions.py).
        val stepsTotal = 1 + toGrant.size + APPOPS_BY_PERMISSION.size +
            APPOPS_BY_PERMISSION.values.count { it == MANAGE_EXTERNAL_STORAGE_OP } + EXTRA_APPOPS.size + 4
        var stepsDone = 0
        fun step() = AdbInstallProgress.granting(++stepsDone, stepsTotal)
        step() // dumpsys

        for (perm in toGrant) {
            safeShell("pm grant $pkg $perm", log)
            step()
        }
        for (op in APPOPS_BY_PERMISSION.values) {
            safeShell("appops set $pkg $op allow", log)
            step()
            if (op == MANAGE_EXTERNAL_STORAGE_OP) {
                // См. cars/_shared/adb_permissions.py — на части прошивок
                // (Geely Cityray/Monji) обычная форма выше молча не
                // применяется, --uid (не стандартный AOSP-флаг, добавлен
                // этим OEM) реально резолвит uid заново.
                safeShell("appops set --uid $pkg $op allow", log)
                step()
            }
        }
        for (op in EXTRA_APPOPS) {
            safeShell("appops set $pkg $op allow", log)
            step()
        }
        safeShell("pm grant $pkg $WRITE_SECURE_SETTINGS", log)
        step()
        safeShell("dumpsys deviceidle whitelist +$pkg", log)
        step()
        enableAccessibilityService(pkg, dumpsysOutput, log)
        step()
        enableNotificationListener(pkg, dumpsysOutput, log)
        step()
        if (AdbSession.isConnected) {
            log("Разрешения выданы.")
        } else {
            lastGrantAt.remove(pkg)
            log(linkLostMessage(pkg))
        }
    }

    private fun linkLostMessage(pkg: String) =
        "Не удалось выдать разрешения $pkg: связь с магнитолой потеряна. " +
            "Переподключитесь и выдайте их на этапе «Доп. действия»."
}
