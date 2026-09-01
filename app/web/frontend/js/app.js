// Бутстрап главного окна: разделитель, 3-шаговый пикер, панель лога, stage
// wizard, мелкие диалоги (usb/report/admin-upload) и мастер "Добавить/
// Изменить машину" (с редактором инструкции внутри).
let currentModel = null;
let stageUiReady = null;

// См. использование ниже (log-toggle / adb-console-input focus / карточка
// инструкции) — true только пока лог развёрнут АВТОМАТИЧЕСКИ (фокусом в
// поле ввода консоли), не вручную кнопкой "Развернуть".
let autoExpandedLog = false;

function setLogExpanded(expanded) {
  document.getElementById("log-card").classList.toggle("is-expanded", expanded);
  const button = document.getElementById("log-toggle");
  button.textContent = expanded ? "Свернуть" : "Развернуть";
  button.setAttribute("aria-expanded", String(expanded));
}

function initializeStageUi() {
  if (stageUiReady) return stageUiReady;
  stageUiReady = new Promise((resolve) => {
    // Каталог должен появиться раньше редакторов. Это заметно на реальных
    // наборах данных: создание обработчиков большого мастера установки и
    // редактора графа занимало первый кадр главного экрана, хотя до выбора
    // модели они пользователю не нужны.
    setTimeout(() => {
      window.instructionEditor.init();
      window.stageWizard.init(document.getElementById("install-content"), log);
      try {
        window.graphWizard.init(log);
      } catch (err) {
        console.error("Не удалось инициализировать редактор", err);
        log(`Ошибка инициализации редактора: ${err.message || err}`);
      }
      window.pendingList.init();
      resolve();
    }, 0);
  });
  return stageUiReady;
}

function log(text) {
  const el = document.getElementById("log-panel");
  const line = document.createElement("div");
  line.className = `log-line log-line-${window.classifyLogLevel(text)}`;
  // highlightLogKeywords сам экранирует HTML и подсвечивает отдельные слова
  // (Starting/Error/Warning и т.п., см. log_format.js) — поэтому innerHTML,
  // а не textContent.
  line.innerHTML = window.highlightLogKeywords(text);
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

// Мини-консоль ADB под логом (была в tkinter-версии до перехода на pywebview,
// см. app/gui.py в истории git) — свободная "adb shell <команда>" на
// выбранное устройство, независимо от того, выбрана ли машина/этап.
let adbConsoleDeviceByLabel = {};

async function refreshAdbConsoleDevices() {
  const select = document.getElementById("adb-console-device");
  const previous = select.value;
  const devices = await window.pywebview.api.install_list_devices();
  select.innerHTML = "";
  adbConsoleDeviceByLabel = {};
  for (const d of devices) {
    let label = d.serial;
    if (d.model) label += `  (${d.model})`;
    if (d.state !== "device") label += `  [${d.state}]`;
    adbConsoleDeviceByLabel[label] = d.state === "device" ? d.serial : null;
    const opt = document.createElement("option");
    opt.value = label;
    opt.textContent = label;
    select.appendChild(opt);
  }
  if (Object.prototype.hasOwnProperty.call(adbConsoleDeviceByLabel, previous)) {
    select.value = previous;
  }
}

// Команды, после которых список устройств мог измениться (см.
// adb_utils.SERVER_LEVEL_COMMANDS на стороне Python — тот же набор) — сама
// консоль результат не ждёт (install_console_send возвращает сразу, работа
// идёт в фоновом потоке), поэтому обновляем список с небольшой задержкой,
// а не сразу.
const ADB_DEVICE_LIST_COMMANDS = new Set(["connect", "disconnect", "pair", "tcpip", "kill-server", "start-server"]);

// Единый источник для подсказок (datalist) и для красивой подписи введённой
// команды в логе (см. logConsoleCommand ниже) — value без "adb " в начале
// (сама подпись "adb" уже стоит перед полем, см. index.html), label — что
// реально покажется в логе вместо сырой команды.
const ADB_CONSOLE_SUGGESTIONS = [
  // -- adb: подключение/устройства --------------------------------------
  { value: "devices", label: "Список подключённых устройств" },
  { value: "devices -l", label: "Список устройств подробно (модель, порт)" },
  { value: "connect ", label: "Подключиться по Wi-Fi — connect <ip>:<порт>" },
  { value: "disconnect", label: "Отключить все Wi-Fi ADB соединения" },
  { value: "disconnect ", label: "Отключить конкретное соединение — disconnect <ip>:<порт>" },
  { value: "pair ", label: "Сопряжение по Wi-Fi (Android 11+) — pair <ip>:<порт> <код>" },
  { value: "tcpip ", label: "Включить ADB по Wi-Fi на текущем порту USB — tcpip <порт>" },
  { value: "usb", label: "Вернуть устройство обратно в режим USB" },
  { value: "wait-for-device", label: "Ждать появления устройства" },
  { value: "get-state", label: "Текущее состояние устройства" },
  { value: "get-serialno", label: "Серийный номер устройства" },
  { value: "kill-server", label: "Остановить adb-сервер на компьютере" },
  { value: "start-server", label: "Запустить adb-сервер на компьютере" },
  // -- adb: система устройства -------------------------------------------
  { value: "reboot", label: "Перезагрузить устройство" },
  { value: "reboot recovery", label: "Перезагрузить в recovery" },
  { value: "reboot bootloader", label: "Перезагрузить в bootloader/fastboot" },
  { value: "reboot sideload", label: "Перезагрузить в режим sideload (recovery + ADB)" },
  { value: "root", label: "Перезапустить adbd с root-правами" },
  { value: "unroot", label: "Вернуть adbd в обычный режим (без root)" },
  { value: "remount", label: "Перемонтировать /system на запись" },
  { value: "bugreport ", label: "Собрать полный баг-репорт в файл — bugreport <путь к файлу>" },
  // -- adb: файлы и приложения --------------------------------------------
  { value: "install ", label: "Установить APK — install <путь к файлу>" },
  { value: "install -r ", label: "Установить APK поверх существующего — install -r <путь к файлу>" },
  { value: "uninstall ", label: "Удалить пакет — uninstall <пакет>" },
  { value: "uninstall -k ", label: "Удалить пакет, оставив данные/кэш — uninstall -k <пакет>" },
  { value: "push ", label: "Скопировать файл на устройство — push <локальный путь> <путь на устройстве>" },
  { value: "pull ", label: "Скопировать файл с устройства — pull <путь на устройстве> <локальный путь>" },
  { value: "sideload ", label: "Установить OTA/zip через recovery — sideload <путь к файлу>" },
  { value: "logcat -d", label: "Системный журнал устройства (снимок)" },
  { value: "logcat -c", label: "Очистить системный журнал устройства" },
  { value: "logcat -b all -d", label: "Системный журнал — все буферы (main/system/radio/events)" },
  { value: "forward --list", label: "Список проброшенных портов (adb forward)" },
  { value: "reverse --list", label: "Список обратно проброшенных портов (adb reverse)" },
  // -- shell: pm (пакеты) --------------------------------------------------
  { value: "shell pm list packages", label: "Список всех установленных пакетов" },
  { value: "shell pm list packages -3", label: "Список пользовательских (не системных) приложений" },
  { value: "shell pm list packages -s", label: "Список системных приложений" },
  { value: "shell pm list packages -d", label: "Список отключённых приложений" },
  { value: "shell pm list packages -e", label: "Список включённых приложений" },
  { value: "shell pm list packages -f", label: "Список пакетов с путями к APK" },
  { value: "shell pm path ", label: "Путь к APK установленного пакета — pm path <пакет>" },
  { value: "shell pm dump ", label: "Полная информация о пакете — pm dump <пакет>" },
  { value: "shell pm clear ", label: "Очистить данные приложения (сброс к заводским) — pm clear <пакет>" },
  { value: "shell pm disable-user ", label: "Отключить приложение для пользователя — pm disable-user <пакет>" },
  { value: "shell pm enable ", label: "Включить приложение — pm enable <пакет>" },
  { value: "shell pm uninstall ", label: "Удалить пакет через pm — pm uninstall <пакет>" },
  { value: "shell pm uninstall -k --user 0 ", label: "Удалить приложение только для текущего пользователя (system app) — pm uninstall -k --user 0 <пакет>" },
  { value: "shell pm grant ", label: "Выдать разрешение — pm grant <пакет> <разрешение>" },
  { value: "shell pm revoke ", label: "Забрать разрешение — pm revoke <пакет> <разрешение>" },
  { value: "shell pm list permissions -d -g", label: "Список опасных разрешений группами" },
  { value: "shell pm resolve-activity ", label: "Какая activity откроется по умолчанию — pm resolve-activity <пакет>" },
  // -- shell: am (менеджер приложений/активностей) -------------------------
  { value: "shell am start -n ", label: "Запустить activity напрямую — am start -n <пакет>/<activity>" },
  { value: "shell am start -a android.intent.action.VIEW -d ", label: "Открыть ссылку/URI — am start -a android.intent.action.VIEW -d <uri>" },
  { value: "shell am start -a android.settings.SETTINGS", label: "Открыть системные настройки Android" },
  { value: "shell am start -a android.settings.WIFI_SETTINGS", label: "Открыть настройки Wi-Fi" },
  { value: "shell am start -a android.settings.APPLICATION_SETTINGS", label: "Открыть список приложений в настройках" },
  { value: "shell am start -a android.settings.APN_SETTINGS", label: "Открыть настройки точки доступа (APN) — мобильная сеть/SIM" },
  { value: "shell am start -a android.settings.DATE_SETTINGS", label: "Открыть настройки даты и времени" },
  { value: "shell am start -a android.settings.DISPLAY_SETTINGS", label: "Открыть настройки экрана" },
  { value: "shell am start -a android.settings.SECURITY_SETTINGS", label: "Открыть настройки безопасности" },
  { value: "shell am start -a android.settings.ACCESSIBILITY_SETTINGS", label: "Открыть настройки специальных возможностей" },
  { value: "shell am start -a android.settings.INPUT_METHOD_SETTINGS", label: "Открыть настройки клавиатуры/способов ввода" },
  { value: "shell am start -a android.settings.LOCATION_SOURCE_SETTINGS", label: "Открыть настройки геолокации" },
  { value: "shell am start -a android.settings.APPLICATION_DETAILS_SETTINGS -d package:", label: "Открыть настройки конкретного приложения — ...-d package:<пакет>" },
  { value: "shell am force-stop ", label: "Принудительно остановить приложение — am force-stop <пакет>" },
  { value: "shell am kill ", label: "Остановить фоновый процесс приложения — am kill <пакет>" },
  { value: "shell am broadcast -a ", label: "Отправить broadcast-сообщение системе — am broadcast -a <действие>" },
  { value: "shell am startservice -n ", label: "Запустить сервис приложения — am startservice -n <пакет>/<сервис>" },
  // -- shell: dumpsys (диагностика системы) --------------------------------
  { value: "shell dumpsys battery", label: "Статус батареи" },
  { value: "shell dumpsys package ", label: "Подробности о пакете — dumpsys package <пакет>" },
  { value: "shell dumpsys meminfo ", label: "Использование памяти приложением — dumpsys meminfo <пакет>" },
  { value: "shell dumpsys activity activities", label: "Стек запущенных активностей" },
  { value: "shell dumpsys window windows", label: "Список окон на экране" },
  { value: "shell dumpsys cpuinfo", label: "Загрузка процессора по процессам" },
  { value: "shell dumpsys connectivity", label: "Состояние сетевого подключения" },
  { value: "shell dumpsys wifi", label: "Подробный статус Wi-Fi" },
  { value: "shell dumpsys power", label: "Состояние питания/экрана (спит/не спит)" },
  // -- shell: settings/wm/svc (системные настройки) ------------------------
  { value: "shell settings list global", label: "Глобальные системные настройки" },
  { value: "shell settings list secure", label: "Защищённые системные настройки" },
  { value: "shell settings list system", label: "Пользовательские системные настройки" },
  { value: "shell settings get global ", label: "Прочитать глобальную настройку — settings get global <ключ>" },
  { value: "shell settings put global ", label: "Изменить глобальную настройку — settings put global <ключ> <значение>" },
  { value: "shell wm size", label: "Разрешение экрана" },
  { value: "shell wm size reset", label: "Сбросить разрешение экрана к заводскому" },
  { value: "shell wm density", label: "Плотность экрана (DPI)" },
  { value: "shell wm density reset", label: "Сбросить плотность экрана к заводской" },
  { value: "shell svc wifi enable", label: "Включить Wi-Fi" },
  { value: "shell svc wifi disable", label: "Выключить Wi-Fi" },
  { value: "shell svc power reboot", label: "Перезагрузка средствами системы (аналог reboot)" },
  // -- shell: устройство/эмуляция ввода -------------------------------------
  { value: "shell getprop", label: "Все системные свойства устройства" },
  { value: "shell getprop ro.product.model", label: "Модель устройства" },
  { value: "shell getprop ro.product.manufacturer", label: "Производитель устройства" },
  { value: "shell getprop ro.build.version.release", label: "Версия Android" },
  { value: "shell getprop ro.build.version.sdk", label: "Версия Android SDK (API level)" },
  { value: "shell getprop ro.serialno", label: "Серийный номер устройства (из системы)" },
  { value: "shell ps -A", label: "Список запущенных процессов" },
  { value: "shell top -n 1", label: "Снимок нагрузки процессора/памяти прямо сейчас" },
  { value: "shell df", label: "Свободное место на разделах" },
  { value: "shell input keyevent 3", label: "Кнопка «Домой»" },
  { value: "shell input keyevent 4", label: "Кнопка «Назад»" },
  { value: "shell input keyevent 26", label: "Кнопка питания (вкл/выкл экран)" },
  { value: "shell input keyevent 82", label: "Кнопка «Меню»" },
  { value: "shell input keyevent 187", label: "Обзор последних приложений" },
  { value: "shell input tap ", label: "Тап по координатам — input tap <x> <y>" },
  { value: "shell input swipe ", label: "Свайп — input swipe <x1> <y1> <x2> <y2>" },
  { value: "shell input text ", label: "Ввести текст — input text <строка без пробелов>" },
  { value: "shell screencap -p /sdcard/screen.png", label: "Сделать скриншот на устройство" },
  { value: "shell screenrecord /sdcard/screen.mp4", label: "Записать видео с экрана (до 3 минут)" },
];

function initAdbConsoleSuggestions() {
  const datalist = document.getElementById("adb-console-suggestions");
  for (const { value, label } of ADB_CONSOLE_SUGGESTIONS) {
    datalist.appendChild(new Option(value, value, false, false)).label = label;
    // Дублируем с "adb " в начале — по привычке к настоящему терминалу
    // многие всё равно печатают команду целиком с "adb ", хотя это слово
    // уже показано отдельной подписью перед полем (см. index.html) и не
    // нужно. Браузер фильтрует подсказки по совпадению С НАЧАЛА строки —
    // без этой пары человек, начавший печатать "adb dev...", вообще не
    // увидел бы "devices" в списке, хотя команда та же самая (см. также
    // install_api.py: console_send — на бэкенде это же "adb " срезается
    // перед выполнением).
    const withAdb = `adb ${value}`;
    datalist.appendChild(new Option(withAdb, withAdb, false, false)).label = label;
  }
}

// Совпадает с console_send на стороне Python (install_api.py) — свой
// нормализатор здесь нужен, чтобы найти красивую подпись из
// ADB_CONSOLE_SUGGESTIONS даже если человек ввёл "adb "-версию.
function stripAdbPrefix(command) {
  if (command.toLowerCase() === "adb") return "";
  if (command.slice(0, 4).toLowerCase() === "adb ") return command.slice(4).trimStart();
  return command;
}

function sendAdbConsoleCommand() {
  const input = document.getElementById("adb-console-input");
  const command = input.value.trim();
  if (!command) return;
  input.value = "";
  const select = document.getElementById("adb-console-device");
  const device = adbConsoleDeviceByLabel[select.value] || null;
  // Раньше в логе была видна только реакция на команду (или вообще ничего,
  // см. install_api.py:_console_worker), а сама введённая команда — нет:
  // выглядело так, будто лог отвечает сам по себе. Логируем ввод сразу же
  // здесь, до ответа от Python (тот придёт отдельным событием "log" уже
  // из фонового потока), отдельным оформлением — не через
  // classifyLogLevel, это не результат, а именно эхо того, что ввёл человек.
  logConsoleCommand(command);
  window.pywebview.api.install_console_send(device, command);
  const firstWord = command.split(/\s+/, 1)[0].toLowerCase();
  if (ADB_DEVICE_LIST_COMMANDS.has(firstWord)) {
    setTimeout(refreshAdbConsoleDevices, 1500);
  }
}

// Ищет команду среди ADB_CONSOLE_SUGGESTIONS — сначала точное совпадение
// (короткие команды без аргументов, например "devices"), потом по префиксу
// для команд, ожидающих аргумент после пробела (значения в списке,
// оканчивающиеся пробелом — "install ", "shell pm clear " и т.п.). Команда
// вне этого списка своей красивой подписи не имеет — не наш случай угадать
// текст для абсолютно любой возможной adb/shell-команды, только для тех,
// что мы сами туда добавили (см. ADB_CONSOLE_SUGGESTIONS выше).
function findConsoleSuggestion(normalized) {
  const exact = ADB_CONSOLE_SUGGESTIONS.find((s) => s.value.trim() === normalized);
  if (exact) return exact;
  // Не только записи с пробелом на конце — есть и такие, где аргумент
  // прилепляется прямо без пробела (например "...package:<пакет>").
  const prefixMatches = ADB_CONSOLE_SUGGESTIONS.filter(
    (s) => s.value.length < normalized.length && normalized.startsWith(s.value));
  if (!prefixMatches.length) return undefined;
  // Самый длинный (специфичный) из подходящих префиксов — иначе, например,
  // "pm uninstall -k --user 0 " никогда бы не выбрался: более общий
  // "pm uninstall " тоже подходит по startsWith и может стоять в списке раньше.
  return prefixMatches.reduce((best, s) => (s.value.length > best.value.length ? s : best));
}

// В самой подсказке (datalist, см. initAdbConsoleSuggestions) label
// включает синтаксис после " — " ("Запустить activity напрямую — am start
// -n <пакет>/<activity>") — это помогает ВЫБРАТЬ команду до того, как её
// набрали. В эхо введённой команды это уже не нужно и просто дублирует то,
// что человек только что сам напечатал (аргумент показывается следом,
// после двоеточия) — оставляем только описание до тире.
function shortLabel(label) {
  const dashIndex = label.indexOf(" — ");
  return dashIndex === -1 ? label : label.slice(0, dashIndex);
}

function logConsoleCommand(command) {
  const el = document.getElementById("log-panel");
  const line = document.createElement("div");
  line.className = "log-line log-line-command";
  const normalized = stripAdbPrefix(command).trim();
  const match = findConsoleSuggestion(normalized);
  if (match) {
    const extraArg = normalized.slice(match.value.trim().length).trim();
    line.textContent = `❯ ${shortLabel(match.label)}${extraArg ? `: ${extraArg}` : ""}`;
  } else {
    line.textContent = `❯ ${command}`;
  }
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

// Показывает/прячет admin-only элементы интерфейса — вызывается на старте
// (см. pywebviewready ниже, с info.admin_mode) и повторно сразу после
// разблокировки функций администратора из "Настроек" (см. settings.js: 10
// тапов по версии → вход), без перезапуска программы. window.mainPicker.
// setAdminMode сама одноразовая (переносит кнопки в попап при первом
// enabled=true, дальше no-op) — безопасно звать оба раза.
function applyAdminMode(enabled) {
  window.mainPicker.setAdminMode(enabled);
  const display = enabled ? "" : "none";
  document.getElementById("admin-upload-btn").style.display = display;
  document.getElementById("admin-add-apk-btn").style.display = display;
  document.getElementById("admin-browse-btn").style.display = display;
  document.getElementById("admin-logout-btn").style.display = display;
  document.getElementById("pending-section").style.display = display;
}
window.applyAdminMode = applyAdminMode;

function onModelSelected(model) {
  currentModel = model;
  const shell = document.getElementById("app-shell");
  shell.classList.remove("catalog-home");
  shell.classList.add("workspace-open");
  shell.style.gridTemplateColumns = "";
  document.getElementById("workspace-nav").hidden = false;
  document.getElementById("workspace-model-title").textContent = model.modification
    ? `${model.brand} ${model.name} — ${model.modification}`
    : `${model.brand} ${model.name}`;
  document.getElementById("report-btn").disabled = false;
  document.getElementById("edit-car-btn").disabled = !model.has_wizard_spec;
  document.getElementById("workspace-edit-car").disabled = !model.has_wizard_spec;
  window.stageWizard.open(model);
}

function returnToCatalog() {
  const previousModel = currentModel;
  currentModel = null;
  const shell = document.getElementById("app-shell");
  shell.classList.remove("workspace-open");
  shell.classList.add("catalog-home");
  shell.style.gridTemplateColumns = "";
  document.getElementById("workspace-nav").hidden = true;
  document.getElementById("report-btn").disabled = true;
  document.getElementById("edit-car-btn").disabled = true;
  document.getElementById("workspace-edit-car").disabled = true;
  window.mainPicker.showModelListFor(previousModel);
}

function openCarEditorForNewModel() {
  window.graphWizard.open(null, window.mainPicker.getBrands(),
    (brand, modelName, modification, dir) => onCarCreated(brand, modelName, modification, dir, null));
}

function openCarEditorForCurrentModel() {
  const editingKey = currentModel ? currentModel.key : null;
  window.graphWizard.open(currentModel, window.mainPicker.getBrands(),
    (brand, modelName, modification, dir) => onCarCreated(brand, modelName, modification, dir, editingKey));
}

// editingKey — ключ (== пройденный путь папки, см. scanner_api.py:
// _model_to_dict) модели, которая была открыта в редакторе НА МОМЕНТ клика
// "Изменить" (см. edit-car-btn ниже) — null для "Добавить машину"/правки
// заявки клиента (pending_list.js, там свой onCreated). Если это была та
// же модель, что сейчас показана в основном окне — перезагружаем её показ
// вместо того, чтобы оставлять устаревшую инструкцию/этапы висеть до
// следующего ручного выбора модели в пикере. dir — новый путь модели (см.
// car_saved-событие, car_editor_api.py:_worker) — это и есть новый key,
// даже если марку/модель переименовали.
async function onCarCreated(brand, modelName, modification, dir, editingKey) {
  const label = modification ? `${brand} / ${modelName} — ${modification}` : `${brand} / ${modelName}`;
  log(`Сохранена модель: ${label}`);
  await window.mainPicker.reload();
  if (editingKey && dir && currentModel && currentModel.key === editingKey) {
    const full = await window.pywebview.api.scanner_select_model(dir);
    if (!full.error) onModelSelected(full);
  }
}

// Автообновление ПРОГРАММЫ (не путать с showUpdatesNotice ниже — та про
// содержимое cars/, эта про саму программу, см. app/web/api/update_api.py).
// Проверяется при каждом запуске (не блокирует остальной старт, см. вызов
// ниже), диалог с чейнджлогом появляется, только если версия новее найдена.
async function checkForUpdate() {
  // Возвращает true, если обновление найдено и диалог показан — вызывающий
  // код (см. pywebviewready ниже) использует это, чтобы не показывать ещё и
  // донат-попап поверх/следом: если есть обновление, показываем ТОЛЬКО его.
  let update;
  try {
    update = await window.pywebview.api.update_check();
  } catch (err) {
    return false;
  }
  if (!update.available) return false;
  window.updateDialog.open(update);
  return true;
}

// Сводка "Что нового" после автообновления cars/ с сервера при старте (см.
// app/web/api/sync_api.py: startup_sync/app/update_tracker.py) — пусто на
// первом запуске программы и когда server.json не настроен.
function showUpdatesNotice(changes) {
  const added = changes.filter((c) => c.kind === "added");
  const updated = changes.filter((c) => c.kind === "updated");
  const lines = [];
  const describe = (c) => `  • ${c.label}` + (c.changelog ? `: ${c.changelog}` : "");
  if (added.length) {
    lines.push("Добавлены:", ...added.map(describe));
  }
  if (updated.length) {
    if (lines.length) lines.push("");
    lines.push("Обновлены:", ...updated.map(describe));
  }
  window.notice(lines.join("\n"), { title: "Что нового" });
}

window.addEventListener("pywebviewready", async () => {
  window.initResizer(document.getElementById("app-shell"), document.getElementById("resizer"));

  window.initDialogs();
  window.boostyDialogs.init();
  initAdbConsoleSuggestions();
  // admin_mode здесь уже полностью решён на стороне Python (тихий автовход
  // сохранёнными логином/паролем, если функции администратора когда-то
  // разблокировали на этой машине — см. app/web/bridge.py: WebApi.__init__)
  // — раньше тут ещё был отдельный обязательный экран входа для отдельной
  // admin-сборки (admin_main_web.py), сейчас программа одна и его нет.
  const info = await window.pywebview.api.app_get_info();
  const settingsPreferences = await window.pywebview.api.settings_preferences();
  document.documentElement.classList.toggle("reduce-motion", settingsPreferences.reduced_motion);
  // Win7-сборка (QtWebEngine, не WebView2, см. bridge.py: WebApi.is_win7) —
  // на реальном старом железе backdrop-filter (blur позади каждой кнопки/
  // диалога) оказался очень тяжёлым без аппаратного ускорения; "low-perf"
  // отключает его целиком (см. css/tokens.css), это не пользовательская
  // настройка — от сборки, а не от предпочтения.
  document.documentElement.classList.toggle("low-perf", info.is_win7);

  // ДО sync_startup() — иначе лог-события, которые синхронизация шлёт по
  // ходу (см. app/content_sync.py: sync_tree/list_files_recursive), летят в
  // window.__onBackendEvent (см. events.js) раньше, чем на "log" вообще
  // появится подписчик, и просто теряются: шина не буферизует события для
  // опоздавших слушателей. Из-за этого лог при первом запуске выглядел
  // пустым, будто программа зависла, хотя синхронизация шла нормально.
  window.events.on("log", (event) => log(event.text));
  window.events.on("sync_progress", (event) => window.mainPicker.setStartupProgress(
    event.done, event.total, event.files_done, event.files_total,
  ));

  // Каталог открывается из локального снимка сразу. Центрированный экран
  // загрузки нужен только при первом запуске, когда показывать ещё нечего:
  // проверка/синхронизация большого набора файлов не должна закрывать уже
  // доступное главное окно на десятки секунд.
  const pickerReady = window.mainPicker.init(document.getElementById("picker"), {
    onModelSelected: async (model) => {
      await initializeStageUi();
      onModelSelected(model);
    },
  });
  await pickerReady;
  // Отдаём браузеру один кадр с каталогом, после чего тихо подготавливаем
  // рабочий экран. К моменту первого клика он обычно уже готов; await выше
  // сохраняет корректность и при очень быстром клике.
  initializeStageUi();
  const catalogWasEmpty = window.mainPicker.getBrands().length === 0;
  if (catalogWasEmpty) window.mainPicker.showStartupLoading();
  if (settingsPreferences.auto_sync) {
    window.pywebview.api.sync_startup().then(async (syncResult) => {
      // Первый запуск требует построить каталог после скачивания. В остальных
      // случаях не перерисовываем карточки без причины: раньше это давало
      // заметное второе «обновление» каталога при каждом запуске, даже когда
      // сервер ничего не менял.
      const catalogChanged = Boolean(syncResult.changes && syncResult.changes.length > 0);
      if (catalogWasEmpty || catalogChanged) await window.mainPicker.reload();
      if (catalogWasEmpty) window.mainPicker.hideStartupLoading();
      if (catalogChanged) showUpdatesNotice(syncResult.changes);
    }).catch((error) => {
      console.error("Ошибка фоновой синхронизации:", error);
      if (catalogWasEmpty) {
        window.mainPicker.hideStartupLoading();
        // Без этого первый запуск при неудачной синхронизации выглядит как
        // зависание: анимация гаснет, список марок остаётся пустым, и
        // никакой связи с реальной причиной (нет интернета, антивирус
        // блокирует запись, папка программы недоступна на запись —
        // например, установлена в Program Files без прав) не видно нигде,
        // кроме DevTools console, которую обычный пользователь не откроет.
        const permissionHint = info.under_program_files
          ? "\n\nПохоже, программа установлена в Program Files, куда Windows "
            + "не даёт писать без прав администратора. Решения:\n"
            + "1) Запустить magic_sqd.exe один раз через правый клик → "
            + "\"Запуск от имени администратора\";\n"
            + "2) Или переустановить программу в папку, куда у вас есть "
            + "доступ без прав администратора — например, в AppData (при "
            + "установке выберите \"Установить только для меня\" вместо "
            + "пути по умолчанию)."
          : "";
        window.notice(
          "Не удалось загрузить каталог моделей с сервера. Проверьте "
            + "подключение к интернету и повторите через \"Настройки → "
            + "Проверить обновления сейчас\"." + permissionHint,
          { title: "Не удалось обновить каталог" },
        );
      }
    });
  }

  applyAdminMode(info.admin_mode);
  // Диагностика (см. main_web.py:_enable_debug_log_all, переключается из
  // "Настроек" — settings.js) — показываем client_id в углу, чтобы можно
  // было сверить с папкой debug_logs/<id>/, если включена у нескольких
  // людей одновременно.
  if (info.debug_mode) {
    const badge = document.getElementById("debug-id-badge");
    badge.textContent = `DEBUG · ${info.client_id}`;
    badge.style.display = "";
  }

  // update_log/update_progress/update_finished теперь слушает сам
  // update-dialog (см. dialogs.js) — там же и показывается прогресс.
  // Раньше пропускалось и для admin_mode тоже — имело смысл, пока
  // admin-сборка была ОТДЕЛЬНОЙ (публиковалась только вручную), но после
  // объединения сборок (см. app/web/bridge.py: WebApi.is_win7/admin_mode)
  // admin_mode — это просто переключатель на той же самой установленной
  // копии, которой реально пользуются как рабочей; она не должна навсегда
  // переставать проверять обновления после одной разблокировки. debug_mode
  // всё ещё пропускает — не хотим прерывать диагностику неожиданным
  // автообновлением посреди сессии.
  // Порядок: сначала проверяем обновление программы, и только если его НЕТ —
  // донат-попап (см. checkForUpdate — возвращает true, если нашёл и уже сам
  // показал свой диалог). Оба сетевые и не блокируют остальной старт (см.
  // .then ниже вместо await).
  let updateShown = Promise.resolve(false);
  if (!info.debug_mode) {
    updateShown = checkForUpdate();
  }
  if (!info.admin_mode && !info.debug_mode) {
    updateShown.then((shown) => {
      // Админ и без того поддерживает проект своей работой, ему не нужен
      // донат-попап. catalogWasEmpty (см. выше) — признак самого первого
      // запуска (каталог ещё не синхронизирован ни разу): просить донат
      // раньше, чем человек хоть раз воспользовался программой, неуместно.
      if (!shown && !catalogWasEmpty) window.boostyDialogs.maybeShowWelcomeDialog();
    });
  }

  document.getElementById("add-car-btn").addEventListener("click", openCarEditorForNewModel);
  document.getElementById("edit-car-btn").addEventListener("click", openCarEditorForCurrentModel);
  document.getElementById("workspace-add-car").addEventListener("click", openCarEditorForNewModel);
  document.getElementById("workspace-edit-car").addEventListener("click", openCarEditorForCurrentModel);
  document.getElementById("report-btn").addEventListener("click", () => window.reportDialog.open(currentModel));
  document.getElementById("back-to-catalog").addEventListener("click", returnToCatalog);
  document.getElementById("admin-upload-btn").addEventListener("click", () => window.adminDialog.open());
  document.getElementById("admin-add-apk-btn").addEventListener("click", () => window.adminApkDialog.open());
  document.getElementById("admin-browse-btn").addEventListener("click", () => window.adminBrowseDialog.open());
  document.getElementById("admin-logout-btn").addEventListener("click", async () => {
    if (!(await window.confirmDialog(
      "Выключить функции администратора на этой машине? Сохранённый вход будет забыт — "
      + "чтобы включить снова, потребуется войти заново через 10 тапов по версии в Настройках."))) return;
    await window.pywebview.api.admin_logout();
    applyAdminMode(false);
    window.notice("Функции администратора выключены.");
  });
  document.getElementById("log-toggle").addEventListener("click", () => {
    const card = document.getElementById("log-card");
    // Дальше это уже ручное состояние — клик в карточке инструкции (см.
    // ниже) больше не должен его трогать, только автоматическое
    // разворачивание по фокусу в поле ввода само себя сворачивает так.
    autoExpandedLog = false;
    setLogExpanded(!card.classList.contains("is-expanded"));
  });

  // Разворачиваем лог сам, когда начинают печатать команду в консоли — поле
  // ввода лежит в свёрнутой по умолчанию карточке лога, и результат команды
  // (см. install_console_send) иначе просто не виден без ручного клика по
  // "Развернуть" каждый раз. Сворачивается обратно кликом где-то в карточке
  // инструкции выше — но только если разворачивание было именно
  // автоматическим: ручное состояние (кнопка "Развернуть"/"Свернуть") этим
  // не трогаем — см. autoExpandedLog выше.
  document.getElementById("adb-console-input").addEventListener("focus", () => {
    const card = document.getElementById("log-card");
    if (!card.classList.contains("is-expanded")) {
      autoExpandedLog = true;
      setLogExpanded(true);
    }
  });
  document.getElementById("install-content").closest(".card").addEventListener("click", () => {
    if (autoExpandedLog) {
      autoExpandedLog = false;
      setLogExpanded(false);
    }
  });
  // Сам текст инструкции рендерится в sandboxed <iframe> (см. stage_wizard.js:
  // buildInstructionBlock) — клики внутри него вообще не всплывают в
  // родительский документ (другой browsing context), поэтому клик-обработчик
  // выше их не видит. Клик внутри iframe переносит туда фокус — это, в
  // отличие от клика, ловится на уровне окна: переход фокуса в ЛЮБОЙ дочерний
  // фрейм этого документа вызывает "blur" здесь же, а document.activeElement
  // становится самим элементом <iframe> (что внутри него — родителю не видно
  // из-за sandbox без allow-same-origin, но этого и не нужно).
  window.addEventListener("blur", () => {
    if (autoExpandedLog && document.activeElement && document.activeElement.tagName === "IFRAME") {
      autoExpandedLog = false;
      setLogExpanded(false);
    }
  });

  document.getElementById("adb-console-refresh").addEventListener("click", () => refreshAdbConsoleDevices());
  document.getElementById("adb-console-send").addEventListener("click", () => sendAdbConsoleCommand());
  document.getElementById("adb-console-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") sendAdbConsoleCommand();
  });
  refreshAdbConsoleDevices();

});
