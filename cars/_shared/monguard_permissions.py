"""Расширенная выдача прав для MonGuard (com.geely.gc.cloudautoclient,
Geely NewEra/OneOS-платформа) — для кнопки "actions"-этапа. Портировано из
собственного deploy-скрипта MonGuard (install_newera.py: grant_permissions_all,
NewEra-специфичный блок) — обычные разрешения из манифеста уже покрывает
общая grant_all_permissions (см. adb_permissions.py, кнопка "Выдать
разрешения"), здесь только то, что MonGuard дополнительно просит СВЕРХ
стандартного: car API, notification listener, доп. appops, Wi-Fi,
режим разработчика. Специфично для этого одного приложения — не путать с
универсальной adb_permissions.py, которая работает с любым выбранным
пакетом."""
from __future__ import annotations

MAIN_PKG = "com.geely.gc.cloudautoclient"
_NOTIFICATION_LISTENER = f"{MAIN_PKG}/com.newera.hu.media.NewEraMediaSessionListener"

# pm grant — часть строк будет ✗ на части ГУ (signature-only/неизвестное
# разрешение на конкретной прошивке) — это нормально, тот же коммент есть
# и в оригинальном скрипте.
_PM_GRANTS = [
    "geely.oneos.permission.SERVICE",
    "android.car.permission.CAR_CONTROL_AUDIO_VOLUME",
    "android.car.permission.CAR_INFO",
    "android.car.permission.CAR_VENDOR_EXTENSION",
    "android.permission.MODIFY_AUDIO_SETTINGS",
    "android.permission.MEDIA_CONTENT_CONTROL",
    "android.permission.WRITE_SECURE_SETTINGS",
    "android.permission.WRITE_GLOBAL_SETTINGS",
    "android.permission.WRITE_SETTINGS",
    "android.permission.SYSTEM_ALERT_WINDOW",
    "android.permission.ACCESS_FINE_LOCATION",
    "android.permission.ACCESS_COARSE_LOCATION",
    "android.permission.ACCESS_BACKGROUND_LOCATION",
    "android.permission.NEARBY_WIFI_DEVICES",
    "android.permission.POST_NOTIFICATIONS",
    "android.permission.KILL_BACKGROUND_PROCESSES",
    "android.permission.REQUEST_INSTALL_PACKAGES",
    "android.permission.INSTALL_PACKAGES",
    "android.permission.DELETE_PACKAGES",
    "android.permission.REQUEST_DELETE_PACKAGES",
    "android.permission.DUMP",
    "android.permission.READ_LOGS",
    # "WIFI_TOOLKIT" — расширенный набор для task/window/media-интеграции,
    # см. апстрим-README про MANAGE_ACTIVITY_TASKS/MANAGE_ACTIVITY_STACKS:
    # signature-only на многих сборках, ✗ там — норма, не баг.
    "android.permission.BIND_NOTIFICATION_LISTENER_SERVICE",
    "android.permission.GET_TASKS",
    "android.permission.REAL_GET_TASKS",
    "android.permission.MANAGE_ACTIVITY_TASKS",
    "android.permission.MANAGE_ACTIVITY_STACKS",
    "android.permission.REORDER_TASKS",
    "android.permission.STATUS_BAR_SERVICE",
    "android.permission.EXPAND_STATUS_BAR",
    "android.permission.UPDATE_DEVICE_STATS",
    "android.permission.BLUETOOTH_PRIVILEGED",
    "android.permission.MANAGE_EXTERNAL_STORAGE",
    "android.permission.CHANGE_CONFIGURATION",
    "android.permission.FORCE_STOP_PACKAGES",
]

_APPOPS_ALLOW = [
    "SYSTEM_ALERT_WINDOW", "MANAGE_EXTERNAL_STORAGE", "REQUEST_INSTALL_PACKAGES",
    "WRITE_SETTINGS", "GET_USAGE_STATS", "RUN_IN_BACKGROUND", "RUN_ANY_IN_BACKGROUND",
    "START_FOREGROUND", "POST_NOTIFICATION", "SCHEDULE_EXACT_ALARM", "WAKE_LOCK",
    "USE_FULL_SCREEN_INTENT", "TURN_SCREEN_ON",
]


def grant_monguard_extended(ctx, package: str = MAIN_PKG) -> None:
    """Расширенные права + notification listener + Wi-Fi + режим
    разработчика для MonGuard — обычные разрешения из манифеста выдаются
    отдельной кнопкой "Выдать разрешения" (adb_permissions.grant_all_
    permissions), эта функция только добавляет то, что штатная выдача не
    покрывает."""
    ctx.log(f"Расширенные права MonGuard: {package}")
    ok = fail = 0
    for perm in _PM_GRANTS:
        result = ctx.shell(f"pm grant {package} {perm}", check=False)
        if result.returncode == 0:
            ok += 1
        else:
            fail += 1
    ctx.log(f"pm grant: {ok} выдано, {fail} отклонено (signature-only/неизвестные — норма)")

    for op in _APPOPS_ALLOW:
        ctx.shell(f"appops set {package} {op} allow", check=False)
    ctx.log(f"appops allow: {len(_APPOPS_ALLOW)} применено")

    ctx.shell(f"cmd deviceidle whitelist +{package}", check=False)
    ctx.shell(f"cmd power whitelist-add {package}", check=False)
    ctx.log("Добавлено в whitelist Doze/Power")

    # notification listener (media-сессия MonGuard)
    current = (ctx.shell("settings get secure enabled_notification_listeners", check=False).stdout or "").strip()
    existing = [c for c in current.split(":") if c] if current and current != "null" else []
    if _NOTIFICATION_LISTENER not in existing:
        existing.append(_NOTIFICATION_LISTENER)
        ctx.shell(f"settings put secure enabled_notification_listeners {':'.join(existing)}", check=False)
    ctx.shell(f"cmd notification allow_listener {_NOTIFICATION_LISTENER}", check=False)
    ctx.log(f"Notification listener включён: {_NOTIFICATION_LISTENER}")

    # локация (нужна MonGuard для геосервисов) + провижининг + Wi-Fi
    ctx.shell("settings put secure location_mode 3", check=False)
    ctx.shell("settings put secure location_providers_allowed +gps,+network", check=False)
    ctx.shell("settings put global device_provisioned 1", check=False)
    ctx.shell("settings put global geely_device_provisioned 1", check=False)
    # Фикс зависаний Wi-Fi на части Geely-сборок (из оригинального скрипта).
    ctx.shell("setprop persist.service.wifi.ipcp false", check=False)
    ctx.shell("svc wifi enable", check=False)
    ctx.shell("settings put global wifi_on 1", check=False)
    ctx.shell("cmd wifi set-wifi-enabled enabled", check=False)
    ctx.shell("settings put global wifi_sleep_policy 2", check=False)
    ctx.log("Локация, провижининг и Wi-Fi настроены.")
    ctx.log("Готово.")


# Имена лаунчера различаются на KX11/FX11/G733 и других сборках Geely —
# пробуем все известные, как и оригинальный скрипт.
_LAUNCHER_PACKAGES = ("com.android.launcher3", "com.geely.launcher3", "com.geely.oneos.launcher")


def restart_launcher(ctx) -> None:
    """OneOS-Launcher3 кэширует список "системных приложений" до
    перезагрузки — после установки MonGuard его иконка может не появиться,
    пока лаунчер не перезапущен. force-stop + HOME-intent вместо полной
    перезагрузки магнитолы."""
    ctx.log("Перезапуск лаунчера...")
    for pkg in _LAUNCHER_PACKAGES:
        ctx.shell(f"am force-stop {pkg}", check=False)
    ctx.shell("am kill com.geely.dockbar", check=False)
    ctx.sleep(1)
    ctx.shell("am start -a android.intent.action.MAIN -c android.intent.category.HOME", check=False)
    ctx.log("Готово — если иконка MonGuard всё ещё не видна, потребуется перезагрузка магнитолы.")
