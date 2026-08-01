# Magic SQD

Программа с графическим интерфейсом для установки приложений на
Android-магнитолы китайских авто — через ADB или через USB-флешку (для
моделей, где ADB недоступен). Все данные (марки/модели, инструкции,
скрипты установки, стандартные APK) лежат рядом с .exe в открытом виде и
легко редактируются без пересборки программы.

## Структура (то, что лежит рядом с .exe)

```
magic_sqd.exe
assets/
    icon.ico                  <- иконка окна и .exe
    splash.png                 <- картинка сплеш-скрина при запуске
tools/
    adb.exe                  <- положить сюда platform-tools (см. tools/README.txt)
    AdbWinApi.dll
    AdbWinUsbApi.dll
apk/
    yandex_navi.apk           <- любые общие APK, появятся как галочки в программе
    yandex_navi.json          <- (опц.) {"name": "...", "description": "..."}
cars/
    _shared/                  <- общий .py код для install.py/stages.py разных моделей
        load_sibling.py       <- load_install(__file__) — грузит install.py той же модели
        telnet_adb.py, wifi_adb.py  <- хелперы для подключения по сети вместо USB
    Chery/
        Tiggo 7 Pro (MTCD)/
            instruction.html   <- инструкция по получению доступа к ADB / по USB-установке
            install.py         <- функции-этапы (см. "Контракт install.py + stages.py")
            stages.py          <- порядок этапов, обязателен — см. ниже
            files/             <- файлы для install.py (ADB-режим)
            usb_files/         <- файлы, которые копируются на флешку в "usb"-этапах
    Geely/
        Coolray (MTCA)/
            ...
    Demo/
        Test Model X1/         <- рабочий пример-шаблон
```

Папки-марки, начинающиеся с `_` (например `_shared`), в списке марок не
показываются — это служебная зона.

## Добавление новой марки/модели

Проще всего — прямо в программе: кнопка **"Добавить машину..."** открывает
мастер, где этапы, ADB-команды, файлы для флешки и стандартный набор APK
собираются без единой строчки кода. Мастер сам генерирует `install.py` и
`stages.py` в `cars/<Марка>/<Модель>/` — их можно потом открыть и
доредактировать руками, если нужно что-то, чего нет в мастере (см. ниже
контракт этих файлов). Готовую модель можно сразу отправить на проверку
разработчику кнопкой "Отправить на проверку" (нужен настроенный
`submit.json`, см. ниже).

Вручную (без мастера): скопируйте `cars/Demo/Test Model X1` в
`cars/<Марка>/<Модель>` и отредактируйте `instruction.html`/`install.py`/
`stages.py` по образцу.

## Контракт install.py + stages.py

Каждая модель — это пара файлов. `install.py` — просто набор функций
`def имя_этапа(ctx): ...`, ничего не запускает сам по себе. `stages.py`
обязателен для КАЖДОЙ модели (без него кнопка "Установка" неактивна) и
описывает порядок/тип этапов, ссылаясь на функции из своего `install.py`:

```python
# install.py
def usb_step_1(ctx):          # ctx — UsbContext, для типа "usb"
    ctx.copy_dir(ctx.usb_file("step_1"), "")

def adb_step_2(ctx):          # ctx — InstallContext, для типа "adb"
    ctx.install_selected_apks()
```

```python
# stages.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from load_sibling import load_install
m = load_install(__file__)   # грузит install.py этой же папки

STAGES = [
    {"type": "usb", "title": "Флешка — прошивка", "run": m.usb_step_1,
     "description": "Что делать пользователю на этом этапе."},
    {"type": "manual", "title": "Обновление на магнитоле"},   # run не нужен
    {"type": "apps", "title": "Выбор приложений"},             # чекбоксы APK
    {"type": "adb", "title": "Установка по ADB", "run": m.adb_step_2},
]
```

Типы этапов (`type`): `usb`, `adb`, `manual`, `apps` (чекбоксы APK),
`exe` (запуск стороннего .exe-инсталлятора), `check` (техник вручную
выбирает один из вариантов — например версию прошивки, влияет на
видимость/содержимое дальнейших этапов через `condition_var`/
`condition_values` и `variants` на других этапах).
`instruction` (имя .html рядом со `stages.py`) или обычный `description`
— по желанию, для оформления конкретного этапа.

Вместо `description` для одного этапа — `"instruction": "stage1.html"`
(html-файл рядом со `stages.py`, как основной `instruction.html`).

Полный список методов `ctx`:
- ADB-этапы (`InstallContext`, `app/install_context.py`) —
  `ctx.install_apk`, `ctx.shell`, `ctx.push`, `ctx.reboot`,
  `ctx.install_selected_apks()`, `ctx.ask_input(...)`, `ctx.log(...)`.
- USB-этапы (`UsbContext`, `app/usb_context.py`) — `ctx.copy_file`,
  `ctx.copy_dir`, `ctx.copy_selected_apks`, `ctx.write_text`,
  `ctx.usb_file(...)`, `ctx.file(...)`, `ctx.log(...)`.

Если что-то пошло не так — `raise Exception("текст ошибки")`, программа
покажет его пользователю и остановит установку.

### USB-флешка

Кнопка "Через USB-флешку..." (для "usb"-этапов) позволяет выбрать флешку
из списка (показываются только съёмные накопители), опционально
отформатировать (FAT32 — до ~32 ГБ, exFAT — для больших) с явным
подтверждением, и копирует файлы через `run(ctx)` этапа.

## Запуск из исходников (для разработки)

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python main.py
```

## Сборка .exe и инсталлятора

```powershell
.\.venv\Scripts\pyinstaller magic_sqd.spec
```

Собирает onedir-сборку в `dist\magic_sqd\` (customtkinter требует
`--onedir`, не `--onefile` — см. комментарий в `magic_sqd.spec`).
Дальше `installer.iss` (Inno Setup, `ISCC.exe installer.iss`) упаковывает
её в один `MagicSQD_Setup.exe` — тяжёлые `apk/`, `cars/*/files`,
`cars/*/usb_files` в инсталлятор не попадают (`Excludes` в `installer.iss`),
они докачиваются программой по требованию (см. ниже).

## Иконка и сплеш-скрин

`assets\icon.ico` — иконка `.exe` (зашивается в файл при сборке через
`icon=` в `magic_sqd.spec`) и иконка окна во время работы программы.
`assets\splash.png` — картинка, которая на пару секунд показывается при
запуске, пока программа читает `cars\`/`apk\` и грузит `tkinterweb`. Если
`assets\splash.png` рядом с `.exe`/`main.py` нет — сплеш просто не
показывается, программа стартует как обычно.

Если понадобится заменить логотип — исходник лежит в
`assets\icon_source.png` (1024×1024). Пересобрать `icon.ico`/`icon.png`/
`splash.png` из него:

```powershell
.\.venv\Scripts\pip install Pillow
.\.venv\Scripts\python -c "from PIL import Image; im = Image.open('assets/icon_source.png').convert('RGBA'); im.save('assets/icon.ico', sizes=[(s,s) for s in (16,24,32,48,64,128,256)]); im.resize((256,256)).save('assets/icon.png'); im.resize((420,420)).save('assets/splash.png')"
```

## Синхронизация со своим сервером (server.json / submit.json)

Тяжёлые файлы (APK, прошивки) не обязаны лежать в самом репозитории —
программа умеет тихо подтягивать их со своего сервера (см.
`app/content_sync.py`). Создайте `server.json` рядом с `main.py`/`.exe`:

```json
{"base_url": "https://ваш-домен/content"}
```

Это работает автоматически, без кнопок в интерфейсе:
- **при каждом запуске** программа обновляет скрипты/инструкции моделей
  (`install.py`, `stages.py`, `instruction.html`) и список общей
  библиотеки APK — сами тяжёлые файлы при этом ещё не скачиваются;
- **файлы конкретной модели/APK** скачиваются непосредственно перед
  использованием — по факту нажатия "Установить"/"Через USB-флешку", а
  не заранее.

`submit.json` — куда уходит "Отправить на проверку" (мастер "Добавить
машину...") и "Сообщить о проблеме":
```json
{"submit_url": "https://ваш-домен/submit", "submit_key": "..."}
```

Если `server.json`/`submit.json` нет — вся синхронизация и кнопки
отправки просто молча пропускаются, программа работает как обычно с
локальными файлами. Серверная часть (приём заявок, админка) в этот
репозиторий не входит — приватная инфраструктура разработчика.

## adb.exe

Программа сначала ищет `tools\adb.exe` рядом с собой, и только если его там
нет — использует `adb` из системного PATH. Проще всего один раз положить
папку `platform-tools` (adb.exe + пара .dll) в `tools\` и больше об этом не
думать.
