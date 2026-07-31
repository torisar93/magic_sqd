"""
Geely Monjaro (машина ОД) — установка по ADB.

Переписано из старого adb_install.bat, пункт меню "1. ОД — Установка
Monjaro Starter Pack (WiFi, GPS, Приложения, Магазины приложений,
Monjaro Tweaks PRO)". Вариант для прошитого Китая (без GNSS) — отдельная
модель "Monjaro (Китай)" рядом.

Свои APK разложите по подпапкам files/ этой модели (раньше лежали в
подпапках рядом с .bat):
    files/pack/  - Starter Pack (раньше папка Pack)
    files/gnss/  - USBGPS4Droid и т.п. (раньше папка GNSS)
"""


def install_pack(ctx):
    """Установка Monjaro Starter Pack из files/pack/."""
    pack_dir = ctx.file("pack")
    apks = sorted(pack_dir.glob("*.apk")) if pack_dir.exists() else []
    if not apks:
        ctx.log(f"(нет APK в {pack_dir} — пропускаю)")
        return
    for apk in apks:
        ctx.install_apk(apk, reinstall=True, extra_args=["-g"])


def install_gnss(ctx):
    """USBGPS4Droid: APK из files/gnss/ + разрешение на подмену геолокации
    + сервис специальных возможностей (в .bat — блок ":GNSS")."""
    gnss_dir = ctx.file("gnss")
    apks = sorted(gnss_dir.glob("*.apk")) if gnss_dir.exists() else []
    if not apks:
        ctx.log(f"(нет APK в {gnss_dir} — пропускаю)")
        return
    for apk in apks:
        ctx.install_apk(apk)
    pkg = "org.broeuschmeul.android.gps.usb.provider"
    ctx.shell(f"appops set {pkg} android:mock_location allow", check=False)
    _add_accessibility_service(ctx, f"{pkg}/{pkg}.service.BootService")


def grant_macrodroid_permissions(ctx):
    """Права Macrodroid + сервис специальных возможностей (часть сценария
    ":Pack" в .bat)."""
    pkg = "com.arlosoft.macrodroid"
    for perm in (
        "WRITE_SECURE_SETTINGS",
        "CHANGE_CONFIGURATION",
        "READ_LOGS",
        "SET_VOLUME_KEY_LONG_PRESS_LISTENER",
        "DUMP",
    ):
        ctx.shell(f"pm grant {pkg} android.permission.{perm}", check=False)
    ctx.shell(f"pm grant {pkg}.helper android.permission.WRITE_SECURE_SETTINGS", check=False)
    _add_accessibility_service(
        ctx,
        f"{pkg}/{pkg}.triggers.services.MacroDroidAccessibilityServiceJellyBean",
        f"{pkg}/{pkg}.triggers.services.VolumeButtonAccessibilityService",
        f"{pkg}/{pkg}.action.services.UIInteractionAccessibilityService",
        f"{pkg}/{pkg}.triggers.services.FingerprintAccessibilityService",
    )


def grant_install_permissions(ctx):
    """Разрешить перечисленным магазинам/файловым менеджерам ставить APK
    без лишних подтверждений (часть сценария ":Pack" в .bat)."""
    for pkg in (
        "com.aurora.store",
        "com.huawei.appmarket",
        "com.estrongs.android.pop",
        "com.android.chrome",
        "ru.vk.store",
        "ru.yandex.disk",
    ):
        ctx.shell(f"appops set {pkg} REQUEST_INSTALL_PACKAGES allow", check=False)


def set_google_keyboard_default(ctx):
    """Google-клавиатура по умолчанию (часть сценария ":Pack" в .bat)."""
    ime = "com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME"
    ctx.shell(f"ime enable {ime}", check=False)
    ctx.shell(f"ime set {ime}", check=False)


def enable_wifi_settings(ctx):
    """adb root/remount + сброс ipcp + включение development_settings
    (в .bat — "Включаю WiFi...", часть сценария ":Pack")."""
    ctx.adb("root")
    ctx.adb("remount")
    ctx.shell("setprop persist.service.wifi.ipcp false", check=False)
    ctx.shell("settings put global development_settings_enabled 1", check=False)


def _add_accessibility_service(ctx, *services):
    """Дописывает сервисы в settings secure enabled_accessibility_services,
    не затирая то, что там уже включено (аналог парсинга
    "settings list secure | findstr enabled_access" + обрезки префикса в .bat)."""
    result = ctx.shell("settings list secure", check=False)
    current = ""
    for line in (result.stdout or "").splitlines():
        if line.startswith("enabled_accessibility_services="):
            current = line.split("=", 1)[1].strip()
            break
    parts = [p for p in current.split(":") if p]
    for service in services:
        if service not in parts:
            parts.append(service)
    ctx.shell(f"settings put secure enabled_accessibility_services {':'.join(parts)}", check=False)


def run(ctx):
    """"1. ОД" из .bat: Starter Pack + права Macrodroid + клавиатура +
    WiFi + GNSS. Вызывается кнопкой "Установить по ADB"."""
    ctx.log("Устанавливаю Monjaro Starter Pack")
    install_pack(ctx)

    ctx.log("Выдаю права Macrodroid")
    grant_macrodroid_permissions(ctx)

    ctx.log("Разрешаю установку APK из магазинов/файловых менеджеров")
    grant_install_permissions(ctx)

    ctx.log("Устанавливаю Google-клавиатуру по умолчанию")
    set_google_keyboard_default(ctx)

    ctx.log("Настраиваю WiFi")
    enable_wifi_settings(ctx)

    ctx.log("Устанавливаю GNSS (USBGPS4Droid)")
    install_gnss(ctx)

    if ctx.selected_apks:
        ctx.log(f"Устанавливаю {len(ctx.selected_apks)} отмеченных приложений из apk/")
        ctx.install_selected_apks()

    ctx.log("Готово. Перезагрузите магнитолу.")
