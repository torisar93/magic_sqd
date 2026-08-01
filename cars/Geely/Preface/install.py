"""
Geely Preface — установка по ADB через Wi-Fi (переписано из Start Preface.bat).

Как и Atlas, эта магнитола не подключается по USB-кабелю для ADB —
компьютер подключается к Wi-Fi-сети магнитолы, install.py сам вызывает
"adb connect" на IP шлюза этого подключения (см. cars/_shared/wifi_adb.py).
Порт — 7777 (у Atlas порт другой — 5555).

Все шаги (пункты старого меню) — отдельные этапы в stages.py (мастер
"Установка"): выбор приложений + установка стартового пакета/MacroDroid,
GPS-варианты, HUR 7.04/7.2, переустановка приложения "Настройки", открытие
системных настроек.

Свои APK разложите по подпапкам files/ этой модели:
    files/pack/            - стартовый пакет (раньше папка StartApps),
                              показывается галочками на этапе "Выбор приложений"
    files/macrodroid/       - MacroDroid.apk, helper-apk (с "helper"/"mdh" в имени),
                              *.category (данные для импорта)
    files/gps_connector/    - GPS Connector
    files/usbgps/           - UsbGps4Droid
    files/hur/               - HUR_7.04*.apk и HUR_7.2*.apk (версия по имени файла)
    files/settings_app/     - Settings.apk (переустановка приложения "Настройки")
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from wifi_adb import open_android_settings  # noqa: E402

WIFI_PORT = 7777


def install_settings_app(ctx):
    """Переустановка приложения "Настройки" для Preface из files/settings_app/."""
    settings_dir = ctx.file("settings_app")
    apks = sorted(settings_dir.glob("*.apk")) if settings_dir.exists() else []
    if not apks:
        ctx.log(f"(нет APK в {settings_dir} — пропускаю)")
        return
    for apk in apks:
        ctx.install_apk(apk, reinstall=True, extra_args=["-g"])


def install_macrodroid(ctx):
    """MacroDroid + Helper, выдача прав, импорт *.category (в Preface,
    в отличие от Atlas, без дополнительных Geely-специфичных грантов)."""
    md_dir = ctx.file("macrodroid")
    if not md_dir.exists():
        ctx.log(f"(нет папки {md_dir} — пропускаю MacroDroid)")
        return

    def _is_helper(p):
        name = p.name.lower()
        return "helper" in name or "mdh" in name

    main_apks = [p for p in sorted(md_dir.glob("*.apk")) if not _is_helper(p)]
    helper_apks = [p for p in sorted(md_dir.glob("*.apk")) if _is_helper(p)]

    for apk in main_apks:
        ctx.install_apk(apk, reinstall=True, extra_args=["-g"])

    pkg = "com.arlosoft.macrodroid"
    for perm in ("WRITE_SECURE_SETTINGS", "CHANGE_CONFIGURATION", "DUMP", "READ_LOGS",
                 "SET_VOLUME_KEY_LONG_PRESS_LISTENER"):
        ctx.shell(f"pm grant {pkg} android.permission.{perm}", check=False)
    ctx.shell("settings put global captive_portal_mode 0", check=False)
    ctx.shell("pm disable-user --user 0 com.geely.lottieclock", check=False)
    ctx.shell("pm disable-user --user 0 com.geely.screensaver", check=False)

    for data_file in sorted(md_dir.glob("*.zip")) + sorted(md_dir.glob("*.category")):
        ctx.push(data_file, f"/storage/emulated/0/Download/{data_file.name}")

    if helper_apks:
        ctx.shell("cmd package uninstall -k com.arlosoft.macrodroid.helper", check=False)
        ctx.sleep(5)
        for apk in helper_apks:
            ctx.install_apk(apk, reinstall=True, extra_args=["-g"])
        ctx.shell(f"pm grant {pkg}.helper android.permission.WRITE_SECURE_SETTINGS", check=False)


def install_gps_connector(ctx):
    """GPS Connector (для внешнего USB GPS) из files/gps_connector/."""
    gps_dir = ctx.file("gps_connector")
    apks = sorted(gps_dir.glob("*.apk")) if gps_dir.exists() else []
    if not apks:
        ctx.log(f"(нет APK в {gps_dir} — пропускаю)")
        return
    for apk in apks:
        ctx.install_apk(apk)
    ctx.shell("settings put global development_settings_enabled 1", check=False)
    ctx.shell("appops set de.pilablu.gpsconnector android:mock_location allow", check=False)


def install_usbgps(ctx):
    """UsbGps4Droid (для внешнего USB GPS) из files/usbgps/."""
    usbgps_dir = ctx.file("usbgps")
    apks = sorted(usbgps_dir.glob("*.apk")) if usbgps_dir.exists() else []
    if not apks:
        ctx.log(f"(нет APK в {usbgps_dir} — пропускаю)")
        return
    for apk in apks:
        ctx.install_apk(apk)
    pkg = "org.broeuschmeul.android.gps.usb.provider"
    ctx.shell("settings put global development_settings_enabled 1", check=False)
    ctx.shell(f"appops set {pkg} android:mock_location allow", check=False)
    ctx.shell(f"settings put secure enabled_accessibility_services {pkg}/{pkg}.service.BootService",
              check=False)


def install_hur(ctx, name_contains: str):
    """Устанавливает из files/hur/ первый .apk, в имени которого встречается
    name_contains (например "7.04" или "7.2")."""
    hur_dir = ctx.file("hur")
    apks = sorted(hur_dir.glob("*.apk")) if hur_dir.exists() else []
    match = next((p for p in apks if name_contains in p.name), None)
    if not match:
        ctx.log(f"(не найден APK с '{name_contains}' в {hur_dir} — пропускаю)")
        return
    ctx.install_apk(match)


def install_hur_704(ctx):
    """HUR 7.04 — после установки в настройках HUR вручную переключите
    отображение на портретную ориентацию, и только потом ставьте 7.2."""
    install_hur(ctx, "7.04")


def install_hur_72(ctx):
    """HUR 7.2 — последняя версия."""
    install_hur(ctx, "7.2")


def open_settings(ctx):
    open_android_settings(ctx)
