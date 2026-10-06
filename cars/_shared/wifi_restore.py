"""Кнопка «Вернуть Wi-Fi / ДХО / Arkamys» в «Доп. действиях» (kind "restore_wifi", app/car_generator.py; владелец,
2026-10-06) — магнитолы Desay SV NV8020/18 на SemiDrive X9H (Omoda C5 до 2026, Tiggo 4 New, Tenet T4, X-Cross 8).

Вся работа — InstallContext.restore_wifi_features (app/install_context.py + app/wifi_patch.py): снимает SystemUI и
Настройки с самой машины, точечно правит dex, переподписывает публичным платформенным ключом AOSP
(cars/_shared/platform_cert) и кладёт обратно в /system с бэкапом на машине, затем перезагрузка. Метод есть в
программе для компьютера с 1.0.62; файл приходит с каталогом и в старые версии — поэтому наличие метода проверяется
(как optimize_for_motion в adb_permissions.py). На телефоне кнопка не предлагается (Android показывает «Доступно
только в версии для Windows»), а если всё же вызвать — понятное сообщение."""
from __future__ import annotations

CONFIRM = "Да, изменить системные приложения и перезагрузить магнитолу"


def restore_wifi_features(ctx) -> None:
    run = getattr(ctx, "restore_wifi_features", None)
    if run is None:
        ctx.log("Не удалось: эта версия программы не умеет возвращать Wi-Fi/ДХО/Arkamys — обновите программу для "
                "компьютера (на телефоне эта кнопка недоступна: системный раздел правится только по USB-кабелю с ПК).")
        return
    # Правится системный раздел и магнитола перезагружается — подтверждение от случайного нажатия. Отмена —
    # InstallCancelled из ctx.ask_choice, магнитола не тронута.
    ctx.ask_choice("Программа снимет с магнитолы SystemUI и Настройки, откроет в них Wi-Fi, ДХО и Arkamys и запишет "
                   "обратно в системный раздел (оригиналы сохранятся на самой магнитоле), затем перезагрузит её. "
                   "Только по USB-кабелю. Продолжить?", [CONFIRM], title="Вернуть Wi-Fi / ДХО / Arkamys",
                   allow_manual=False)
    run()
