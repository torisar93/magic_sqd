"""
Мастер "Установка" для Monjaro (прошитый Китай): выбор приложений отдельно
от установки (пункт "2. ПИ" старого adb_install.bat, без GNSS). Подключение
по USB-кабелю — устройство выбирается прямо в этапе.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from load_sibling import load_install  # noqa: E402

m = load_install(__file__)


def _install(ctx):
    ctx.log("Устанавливаю отмеченные приложения")
    ctx.install_selected_apks(extra_args=["-g"])

    ctx.log("Выдаю права Macrodroid")
    m.grant_macrodroid_permissions(ctx)

    ctx.log("Разрешаю установку APK из магазинов/файловых менеджеров")
    m.grant_install_permissions(ctx)

    ctx.log("Устанавливаю Google-клавиатуру по умолчанию")
    m.set_google_keyboard_default(ctx)

    ctx.log("Настраиваю WiFi")
    m.enable_wifi_settings(ctx)


STAGES = [
    {
        "type": "apps",
        "title": "Выбор приложений",
        "description": "Отметьте стартовый пакет и/или дополнительные приложения из apk/.",
        "standard_dir": Path(__file__).resolve().parent / "files" / "pack",
    },
    {
        "type": "adb",
        "title": "Установка",
        "description": "Установка отмеченных приложений + права Macrodroid + клавиатура + WiFi.",
        "run": _install,
    },
]
