package ru.magicsqd.mobile.usb

/**
 * Порт cars/_shared/adb_permissions.py:grant_all_permissions на Kotlin —
 * нужен для installApkViaLocalinstall (см. AdbInstall.kt): после установки
 * через localinstall.apk приложению не выдано вообще никаких разрешений
 * (в отличие от "adb install -g"/desktop-пути), поэтому выдаём их сами.
 * НЕ включает автовключение службы специальных возможностей (desktop
 * умеет искать BIND_ACCESSIBILITY_SERVICE в dumpsys package и сама её
 * включать) — редкий случай, сознательно упрощено для первой версии на
 * Android; обычные runtime-разрешения и appops (показ поверх окон,
 * запись настроек, установка APK без диалога, VPN, статистика
 * использования) выдаются так же, как на desktop.
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

    // "Спецдоступы" (не обычные runtime-разрешения — pm grant их не
    // выдаёт), управляются через appops.
    private val APPOPS_BY_PERMISSION = mapOf(
        "android.permission.SYSTEM_ALERT_WINDOW" to "SYSTEM_ALERT_WINDOW",
        "android.permission.WRITE_SETTINGS" to "WRITE_SETTINGS",
        "android.permission.PACKAGE_USAGE_STATS" to "GET_USAGE_STATS",
    )
    private val EXTRA_APPOPS = listOf("REQUEST_INSTALL_PACKAGES", "ACTIVATE_VPN")
    private const val WRITE_SECURE_SETTINGS = "android.permission.WRITE_SECURE_SETTINGS"

    private val REQUESTED_PERMISSIONS_HEADER = Regex("^requested permissions:\\s*$")
    private val PERMISSION_LINE = Regex("^([\\w.]+)(?::.*)?$")

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

    private fun shellText(command: String, log: (String) -> Unit): String =
        when (val r = AdbSession.shell(command, log)) {
            is AdbShellResult.Output -> r.text
            else -> ""
        }

    /** Выдаёт пакету все разрешения, которые он запрашивает в манифесте
     * (см. dumpsys), плюс WRITE_SECURE_SETTINGS и appops-спецдоступы —
     * то, что на большинстве магнитол нельзя дать через штатный экран
     * настроек. Плюс освобождает от ограничений энергосбережения (Doze). */
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
        }
        for (op in EXTRA_APPOPS) {
            AdbSession.shell("appops set $pkg $op allow", log)
        }
        AdbSession.shell("pm grant $pkg $WRITE_SECURE_SETTINGS", log)
        AdbSession.shell("dumpsys deviceidle whitelist +$pkg", log)
        log("Разрешения выданы.")
    }
}
