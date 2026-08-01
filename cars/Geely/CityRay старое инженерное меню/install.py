"""
Geely CityRay — установка по ADB.

У этой магнитолы кнопка "Отладка по USB/ADB" в настройках Android изначально
скрыта. Чтобы её показать, нужно один раз выполнить telnet-команду на
IPv6-адрес магнитолы (порт 23):
    setprop persist.service.adb.button.visible ON
См. cars/_shared/telnet_adb.py — приложение делает это само, нужно только
вставить IPv6-адрес, скопированный из сетевых настроек Android на самой
магнитоле (см. instruction.html). Это persist-свойство, оно переживает
перезагрузку — то есть в норме telnet-шаг нужен один раз (до следующего
сброса настроек/перепрошивки).

После включения кнопки ADB подключение идёт по Wi-Fi ADB (adb connect,
порт 5555, как у моделей Atlas/Preface) — компьютер должен быть подключён
к Wi-Fi-сети самой магнитолы.

Свои APK для стартового пакета разложите по files/pack/ этой модели — они
появятся отмеченными галочками на этапе "Выбор приложений" в stages.py.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from telnet_adb import enable_adb_via_telnet  # noqa: E402

WIFI_PORT = 5555


def enable_adb(ctx):
    """Спрашивает у пользователя IPv6-адрес магнитолы и включает кнопку ADB
    через telnet (см. cars/_shared/telnet_adb.py)."""
    ipv6 = ctx.ask_input(
        "Вставьте IPv6-адрес магнитолы (скопирован из сетевых настроек Android "
        "на CityRay). Компьютер должен быть подключён к Wi-Fi-сети магнитолы.",
        title="IPv6-адрес магнитолы (CityRay)",
    )
    enable_adb_via_telnet(ctx, ipv6)
    ctx.sleep(2)
