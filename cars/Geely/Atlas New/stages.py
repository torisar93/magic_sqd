"""
Мастер "Установка по этапам..." — все пункты старого меню Start Atlas.bat
как отдельные кнопки. Подключение по Wi-Fi ADB (adb connect) выполняется
заново в начале каждого этапа — это дёшево и не мешает, зато не важно,
в каком порядке и сколько раз нажимать кнопки.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from load_sibling import load_install  # noqa: E402
from wifi_adb import connect_wifi  # noqa: E402

m = load_install(__file__)


def _with_connect(fn):
    def wrapped(ctx):
        connect_wifi(ctx, m.WIFI_PORT)
        fn(ctx)
    return wrapped


STAGES = [
    {
        "type": "apps",
        "title": "Выбор приложений",
        "description": "Отметьте стартовый пакет и/или дополнительные приложения из apk/.",
        "standard_dir": Path(__file__).resolve().parent / "files" / "pack",
    },
    {
        "type": "adb",
        "title": "Стартовый пакет + MacroDroid",
        "description": "Установка отмеченных приложений + MacroDroid.",
        "run": _with_connect(lambda ctx: (ctx.install_selected_apks(extra_args=["-g"]), m.install_macrodroid(ctx))),
    },
    {
        "type": "adb",
        "title": "GPS Connector",
        "description": "Для внешнего USB GPS (вариант 1 — альтернатива UsbGps4Droid ниже).",
        "run": _with_connect(m.install_gps_connector),
    },
    {
        "type": "adb",
        "title": "UsbGps4Droid",
        "description": "Для внешнего USB GPS (вариант 2 — альтернатива GPS Connector выше).",
        "run": _with_connect(m.install_usbgps),
    },
    {
        "type": "adb",
        "title": "HUR 7.04",
        "description": (
            "Head Unit Reloaded, версия 7.04. После установки зайдите в "
            "настройки HUR на магнитоле и переключите отображение на "
            "портретную ориентацию — только после этого ставьте HUR 7.2."
        ),
        "run": _with_connect(m.install_hur_704),
    },
    {
        "type": "manual",
        "title": "Переключить ориентацию в HUR",
        "description": (
            "На магнитоле откройте настройки HUR 7.04 и переключите "
            "отображение на портретную ориентацию. Только после этого "
            "переходите к этапу «HUR 7.2»."
        ),
    },
    {
        "type": "adb",
        "title": "HUR 7.2",
        "description": "Head Unit Reloaded, последняя версия (ставится после HUR 7.04).",
        "run": _with_connect(m.install_hur_72),
    },
    {
        "type": "adb",
        "title": "Открыть настройки Android",
        "description": "adb shell am start -a android.settings.SETTINGS.",
        "run": _with_connect(m.open_settings),
    },
]
