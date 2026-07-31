"""
Демонстрационный install.py — шаблон для реальных моделей.

Файл должен содержать функцию run(ctx). Она вызывается в фоновом потоке,
когда в приложении нажимают "Установить" при выбранной этой модели.

Доступно через ctx (см. app/install_context.py):
    ctx.log(text)                 - вывести строку в лог приложения
    ctx.device                    - серийный номер выбранного устройства (или None)
    ctx.model_dir                 - Path к этой папке модели
    ctx.file("name.apk")          - Path к файлу внутри files/ этой модели
    ctx.selected_apks             - список Path отмеченных галочками APK из общей папки apk/
    ctx.adb(*args)                - произвольная команда adb
    ctx.shell(command)            - adb shell <command>
    ctx.install_apk(path)         - adb install -r <path>
    ctx.install_selected_apks()   - установить все отмеченные галочками APK
    ctx.push(local, remote)       - adb push
    ctx.pull(remote, local)       - adb pull
    ctx.reboot(wait=True)         - перезагрузка устройства и ожидание загрузки
    ctx.sleep(seconds)            - пауза (прерывается кнопкой "Стоп")
    ctx.check_cancelled()         - проверка, не нажали ли "Стоп" (вызывается автоматически
                                     внутри adb/shell/install_apk и т.д.)

Бросьте обычное исключение (raise), если что-то пошло не так — приложение покажет
текст ошибки пользователю и остановит установку.
"""


def run(ctx):
    ctx.log("Начинаю установку для Demo / Test Model X1")
    ctx.log(f"Папка модели: {ctx.model_dir}")

    # Пример специфичного для этой модели файла (лежит в files/ рядом с этим скриптом)
    special_apk = ctx.file("special_launcher.apk")
    if special_apk.exists():
        ctx.install_apk(special_apk)
    else:
        ctx.log(f"(демо) файла {special_apk.name} нет в files/ — пропускаю этот шаг")

    # Пример специфичной для модели shell-команды (например, разблокировка сторонних APK)
    # ctx.shell("settings put secure install_non_market_apps 1")

    # Установка приложений, отмеченных галочками в общем списке apk/
    if ctx.selected_apks:
        ctx.log(f"Устанавливаю {len(ctx.selected_apks)} отмеченных приложений из apk/")
        ctx.install_selected_apks()
    else:
        ctx.log("Ни одно приложение из общего списка не отмечено галочкой")

    # Пример перезагрузки после установки (раскомментировать при необходимости):
    # ctx.reboot(wait=True)

    ctx.log("Готово.")
