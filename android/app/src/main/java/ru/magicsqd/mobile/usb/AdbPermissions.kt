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
    private val EXTRA_APPOPS = listOf("REQUEST_INSTALL_PACKAGES", "ACTIVATE_VPN", "ACCESS_RESTRICTED_SETTINGS")
    private const val WRITE_SECURE_SETTINGS = "android.permission.WRITE_SECURE_SETTINGS"

    private val REQUESTED_PERMISSIONS_HEADER = Regex("^requested permissions:\\s*$")
    private val PERMISSION_LINE = Regex("^([\\w.]+)(?::.*)?$")
    private val COMPONENT_NAME_RE = Regex("name=([\\w.]+)")

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

    /** Best-effort поиск класса службы (спецвозможности/слушателя
     * уведомлений — marker разный, логика одна) в dumpsys package — портовая
     * копия cars/_shared/adb_permissions.py:_find_service_component. Формат
     * вывода не стандартизован между версиями Android/прошивками, поэтому
     * если не нашли, вызывающий честно логирует это, а не молча пропускает. */
    private fun findServiceComponent(pkg: String, dumpsysOutput: String, marker: String): String? {
        val lines = dumpsysOutput.lines()
        for (i in lines.indices) {
            if (marker !in lines[i]) continue
            for (j in (i - 1) downTo maxOf(0, i - 15)) {
                val m = COMPONENT_NAME_RE.find(lines[j].trim()) ?: continue
                var cls = m.groupValues[1]
                cls = when {
                    cls.startsWith(".") -> pkg + cls
                    "." !in cls -> "$pkg.$cls"
                    else -> cls
                }
                return "$pkg/$cls"
            }
        }
        return null
    }

    /** Портовая копия cars/_shared/adb_permissions.py:_enable_accessibility_service. */
    private fun enableAccessibilityService(pkg: String, dumpsysOutput: String, log: (String) -> Unit) {
        val component = findServiceComponent(pkg, dumpsysOutput, "BIND_ACCESSIBILITY_SERVICE")
        if (component == null) {
            log("Служба спецвозможностей не найдена в dumpsys ($pkg) — похоже, приложение " +
                "её не объявляет, либо включить придётся вручную.")
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
            log("Служба доступа к уведомлениям не найдена в dumpsys ($pkg) — похоже, " +
                "приложение её не объявляет, либо включить придётся вручную.")
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

    private fun shellText(command: String, log: (String) -> Unit): String =
        when (val r = AdbSession.shell(command, log)) {
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

    /** Выдаёт пакету все разрешения, которые он запрашивает в манифесте
     * (см. dumpsys), плюс WRITE_SECURE_SETTINGS и appops-спецдоступы — то,
     * что на большинстве магнитол нельзя дать через штатный экран настроек.
     * Плюс, если удалось найти в dumpsys, включает службу специальных
     * возможностей и доступ к уведомлениям. Плюс освобождает от ограничений
     * энергосбережения (Doze). */
    fun grantAllPermissions(pkg: String, log: (String) -> Unit) {
        log("Выдаю разрешения: $pkg")
        val dumpsysOutput = shellText("dumpsys package $pkg", log)
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
        log("Разрешения выданы.")
    }
}
