"""
Мастер "Установка" для CityRay: включение кнопки ADB через telnet — отдельно
от выбора и установки приложений. Полезно, если ADB уже был включён раньше
(persist-свойство переживает перезагрузку) и нужно просто переустановить
что-то, не проходя telnet-шаг заново.
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
        "type": "adb",
        "title": "Включить кнопку ADB (telnet, IPv6)",
        "description": (
            "Нужно один раз — после сброса настроек/первой прошивки, пока "
            "кнопка ADB ещё не появилась в настройках Android. Введите "
            "IPv6-адрес магнитолы, скопированный из её сетевых настроек — "
            "подключение по telnet и команду setprop приложение выполнит само."
        ),
        "run": m.enable_adb,
    },
    {
        "type": "apps",
        "title": "Выбор приложений",
        "description": "Отметьте стартовый пакет и/или дополнительные приложения из apk/.",
        "standard_dir": Path(__file__).resolve().parent / "files" / "pack",
    },
    {
        "type": "adb",
        "title": "Установка приложений",
        "description": "Подключение по Wi-Fi ADB и установка отмеченных приложений.",
        "run": _with_connect(lambda ctx: ctx.install_selected_apks(extra_args=["-g"])),
    },
]
