package ru.magicsqd.mobile.usb

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
            AdbSession.shell("settings put secure enabled_accessibility_services ${existing.joinToString(":")}", log)
        }
        AdbSession.shell("settings put secure accessibility_enabled 1", log)
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
            AdbSession.shell("settings put secure enabled_notification_listeners ${existing.joinToString(":")}", log)
        }
        AdbSession.shell("cmd notification allow_listener $component", log)
        log("Доступ к уведомлениям включён: $component")
    }

    private fun shellText(command: String, log: (String) -> Unit, timeoutMs: Int = 5000): String =
        when (val r = AdbSession.shell(command, log, timeoutMs)) {
            is AdbShellResult.Output -> r.text
            else -> ""
        }

    /** Список установленных на магнитоле пакетов (для выбора приложения
     * перед grantAllPermissions/setMockLocationApp ниже) — портовая копия
     * cars/_shared/adb_permissions.py:list_installed_packages. thirdPartyOnly
     * — без системных пакетов производителя/Android (иначе список из сотен
     * записей неудобно листать ради обычно нужных технику сторонних APK). */
    fun listInstalledPackages(thirdPartyOnly: Boolean, log: (String) -> Unit): List<String> {
        val flag = if (thirdPartyOnly) "-3" else ""
        val output = shellText("pm list packages $flag".trim(), log)
        return output.lineSequence()
            .map { it.trim() }
            .filter { it.startsWith("package:") }
            .map { it.removePrefix("package:").trim() }
            .filter { it.isNotEmpty() }
            .sorted()
            .toList()
    }

    /** Назначает приложение "приложением для фиктивных местоположений"
     * (имитация GPS, как в Настройки → Для разработчиков) и включает саму
     * возможность — портовая копия cars/_shared/adb_permissions.py:
     * set_mock_location_app. appops — актуальный механизм (Android 6+);
     * settings put secure mock_location — для более старых прошивок, где
     * appops эту операцию не знает. */
    fun setMockLocationApp(pkg: String, log: (String) -> Unit) {
        log("Приложение для фиктивных местоположений: $pkg")
        AdbSession.shell("appops set $pkg android:mock_location allow", log)
        AdbSession.shell("settings put secure mock_location 1", log)
        log("Готово.")
    }

    /** Запускает главную activity приложения (как обычный тап по иконке в
     * лаунчере) — портовая копия cars/_shared/adb_permissions.py:
     * launch_main_activity ("monkey -c android.intent.category.LAUNCHER" —
     * тот же приём, что уже используется после каждой автоматической
     * установки, см. AdbInstall.kt — сам находит launcher-activity, не
     * требует знать её имя заранее). */
    fun launchMainActivity(pkg: String, log: (String) -> Unit) {
        log("Запускаю приложение: $pkg")
        AdbSession.shell("monkey -p $pkg -c android.intent.category.LAUNCHER 1", log)
        log("Готово.")
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
    fun uninstallApp(pkg: String, log: (String) -> Unit) {
        log("Удаляю приложение: $pkg")
        when (val r = removePackage(pkg, log)) {
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

    /** pm uninstall без записи в лог итога — общий для кнопки «Удалить приложение» и «Откатить в сток». */
    fun removePackage(pkg: String, log: (String) -> Unit): Removal =
        when (val r = AdbSession.shell("pm uninstall $pkg", log)) {
            is AdbShellResult.Output -> {
                val text = r.text.trim()
                if (text.contains("success", ignoreCase = true) && !text.contains("failure", ignoreCase = true)) Removal.Removed
                else Removal.Failed(text.ifBlank { "устройство не ответило" })
            }
            is AdbShellResult.Rejected -> Removal.Blocked
            is AdbShellResult.Failed -> Removal.Failed(r.reason)
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
        val text = when (val r = AdbSession.shell(command, log)) {
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
        // dumpsys package у крупных приложений выдаёт сотни КБ и не укладывается
        // в общие 5 с AdbSession.shell — вывод обрывается, и список запрошенных
        // разрешений получается неполным.
        val dumpsysOutput = shellText("dumpsys package $pkg", log, 20_000)
        // Связь пропала ещё до первой команды: раньше ни одна команда не уходила, а в журнале всё равно
        // было «Разрешения выданы.» (логи #721, #910, #966 — после переустановки Podpratel Pro).
        if (!AdbSession.isConnected) { lastGrantAt.remove(pkg); log(linkLostMessage(pkg)); return }
        val requested = parseRequestedPermissions(dumpsysOutput).ifEmpty { COMMON_DANGEROUS_PERMISSIONS }

        for (perm in requested) {
            if (perm in APPOPS_BY_PERMISSION) continue // выдаётся ниже через appops
            AdbSession.shell("pm grant $pkg $perm", log)
        }
        for (op in APPOPS_BY_PERMISSION.values) {
            AdbSession.shell("appops set $pkg $op allow", log)
            if (op == MANAGE_EXTERNAL_STORAGE_OP) {
                // См. cars/_shared/adb_permissions.py — на части прошивок
                // (Geely Cityray/Monji) обычная форма выше молча не
                // применяется, --uid (не стандартный AOSP-флаг, добавлен
                // этим OEM) реально резолвит uid заново.
                AdbSession.shell("appops set --uid $pkg $op allow", log)
            }
        }
        for (op in EXTRA_APPOPS) {
            AdbSession.shell("appops set $pkg $op allow", log)
        }
        AdbSession.shell("pm grant $pkg $WRITE_SECURE_SETTINGS", log)
        AdbSession.shell("dumpsys deviceidle whitelist +$pkg", log)
        enableAccessibilityService(pkg, dumpsysOutput, log)
        enableNotificationListener(pkg, dumpsysOutput, log)
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
