# Dongfeng — источник: drive2 (Aeolus Huge), применимо и к другим Aeolus/eπ

Источник: https://www.drive2.ru/l/674680676842213805/ (Dongfeng Aeolus Huge)
Судя по поиску, тот же способ подходит для Aeolus Huge/Mage/L7, eπ 007/008
— наш каталог просто "Dongfeng / Dongfeng" (общая заглушка), нужно
уточнить у пользователя точную модель головного устройства.

## Вход в инженерное меню
1. Настройки → Система → быстро тапнуть несколько раз по строке "Software
   version" (версия ПО).
2. Ввести код доступа: `2847579*#*`
3. Откроется инженерное меню — выбрать второй пункт снизу.

## Включение ADB
1. В инженерном меню — первая кнопка "ADB/HOST" (отвечает за режим
   переднего USB-порта).
2. Появится запрос пароля — ввести `BuZhunXieLou!`
3. ADB включён.

## Установка приложений (с компьютера, PowerShell + adb из Android SDK
platform-tools)
```
./adb devices
./adb install 03ES.apk
./adb install 04Touchmaster.apk
./adb install BackButton.apk
./adb shell am start -n com.estrongs.android.pop/com.estrongs.android.pop.view.FileExplorerActivity
```
(Автор ссылается на Telegram-канал, откуда брал сами эти 4 apk файла —
сами файлы не считаны, только имена; после — рекомендует ставить RuStore
для остальных приложений.)
Подключение — USB-A-USB-A кабель к переднему USB-порту магнитолы.

⚠️ Нужно уточнить у пользователя точную модель Dongfeng (Aeolus Huge?
Mage? eπ007/008?) — способ, скорее всего, общий для платформы, но лучше
подтвердить перед тем как класть в инструкцию.
