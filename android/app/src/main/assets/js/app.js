// Новый мобильный интерфейс — с нуля, не портирован из desktop-версии.
// Пикер марка->модель[->модификация] на РЕАЛЬНОМ каталоге cars/,
// синхронизируемом с сервера (см. WebBridge.kt/python/content_sync.py) +
// мастер этапов установки, исполняющий _wizard_spec.json напрямую поверх
// ADB/USB-транспорта (см. WebBridge.kt/InstallEngine.kt/UsbFlashSession.kt).
(function () {
  const { el, clear } = window.dom;

  const STATUS_TITLES = { green: "Актуально", yellow: "Черновой способ", blue: "Недавно обновлено", red: "Не работает" };

  let screenPicker, screenWizard, breadcrumbEl, listEl, syncStatusEl, pickerSearchEl;
  let wizardContentEl, wizardBackBtn, wizardVideoBtn, wizardNextBtn, wizardPageLabel, logPanelEl, topTitleEl, topBackBtn, topbarEl, topHelpBtn, topAccountBtn;
  let adbStatusEl, adbConnectBtn, usbStatusEl, usbConnectBtn, usbFormatBtn, adbBarEl, usbBarEl;
  let adbModeToggleEl, adbModeWiredBtn, adbModeWifiBtn;
  let logBarEl, logLastLineEl, logExpandBtn, logOverlayEl, logCollapseBtn, logCopyBtn, logCmdInput, logCmdRunBtn;

  // Один открытый скан за раз — достаточно, оба места, где он запускается
  // (Wi-Fi ADB / telnet), сами по себе модальные и блокируют остальной UI.
  let pendingScanCallback = null;
  // Отдельный callback для mDNS-поиска порта "Беспроводной отладки" (см.
  // scanAdbService в WebBridge.kt) — работает ПАРАЛЛЕЛЬНО с pendingScanCallback
  // выше (тот сканирует хосты по фиксированному порту), не заменяет его.
  let pendingAdbServiceScanCallback = null;
  // Аккаунт техника (см. auth_bridge.py, WebBridge.kt: authLogin/
  // authRegister) — Bridge.call() возвращает "{}" сразу (работа идёт в
  // фоновом Kotlin-потоке), настоящий результат приходит отдельным
  // событием. Пока открыты "Настройки" с разделом "Аккаунт", сюда
  // записана функция перерисовки этого раздела; закрытие модалки её
  // обнуляет (см. showSettingsModal/buildAccountSection ниже).
  let accountRenderCallback = null;
  // Тот же принцип для actions_list_packages (см. renderActionsStage: kind
  // grant_permissions/mock_location) — список пакетов приходит отдельным
  // событием, не синхронным возвратом из Bridge.call.
  let pendingPackagesCallback = null;

  // Какие типы этапов реально используют ADB/флешку — панели подключения
  // показываются только для них, а не постоянно на весь мастер (иначе на
  // instruction/check/manual этапах висят лишние кнопки безо всякого толку).
  const ADB_STAGE_TYPES = new Set(["adb", "apps", "actions"]);
  const USB_STAGE_TYPES = new Set(["usb", "qr_adb"]);

  let carsData = null;
  let selectedBrand = null;
  let selectedGroup = null;

  let model = null;
  let stages = [];
  let currentIndex = 0;
  let nextAction = () => advanceAfter(currentIndex);

  // Классификация строки лога по русским ключевым словам — та же логика
  // (намеренно те же слова), что и в desktop-версии (см. app/web/frontend/
  // js/log_format.js) — оба приложения пишут лог по-русски в похожем
  // стиле, красим уже готовую строку, не тащим уровень через AdbSession/
  // InstallEngine/WebBridge (см. .log-line-* в css/style.css). Английские
  // "Error"/"Exception" и т.п. — на случай непереведённого сырого вывода
  // adb/Android (см. WebBridge.kt: AdbConsoleFormat — переводит только
  // известные частые случаи).
  function classifyLogLevel(text) {
    if (/ошибк|не удал|отклон|не найден|провал|неизвестн|\berror\b|\bexception\b|\bfailed\b|\bfailure\b|permission denial/i.test(text)) return "error";
    if (/внимани|предупрежд/i.test(text)) return "warn";
    if (/готово\.?$|успешно|выдан|установлен|опубликован|подключ[её]н|заверш/i.test(text)) return "success";
    return "info";
  }

  function escapeHtml(text) {
    return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // Отдельные служебные слова в сыром английском выводе — та же логика, что
  // и в desktop-версии (см. app/web/frontend/js/log_format.js:
  // highlightLogKeywords), намеренно тот же список.
  const KEYWORD_TRANSLATIONS = [
    { pattern: /\bfatal\b/gi, text: "Критично", cls: "error" },
    { pattern: /\bexception\b/gi, text: "Исключение", cls: "error" },
    { pattern: /\berror\b/gi, text: "Ошибка", cls: "error" },
    { pattern: /\bfailed\b/gi, text: "Не удалось", cls: "error" },
    { pattern: /\bfailure\b/gi, text: "Сбой", cls: "error" },
    { pattern: /\bdenied\b/gi, text: "Отказано", cls: "error" },
    { pattern: /\bwarning\b/gi, text: "Внимание", cls: "warn" },
    { pattern: /\bdeprecated\b/gi, text: "Устарело", cls: "warn" },
    { pattern: /\bstarting\b/gi, text: "Запуск", cls: "info" },
    { pattern: /\bsuccess(?:fully)?\b/gi, text: "Успешно", cls: "success" },
    { pattern: /\bconnected\b/gi, text: "Подключено", cls: "success" },
    { pattern: /\bdisconnected\b/gi, text: "Отключено", cls: "warn" },
    { pattern: /\baborted\b/gi, text: "Прервано", cls: "error" },
    { pattern: /\btimeout\b/gi, text: "Истекло время ожидания", cls: "error" },
    { pattern: /\bkilled\b/gi, text: "Остановлено", cls: "warn" },
    { pattern: /\bunavailable\b/gi, text: "Недоступно", cls: "warn" },
    { pattern: /\bskipped\b/gi, text: "Пропущено", cls: "warn" },
    { pattern: /\bunauthorized\b/gi, text: "Не авторизовано", cls: "error" },
    { pattern: /\boffline\b/gi, text: "Не отвечает", cls: "error" },
    { pattern: /\bdisabled\b/gi, text: "Отключено", cls: "warn" },
    { pattern: /\benabled\b/gi, text: "Включено", cls: "success" },
  ];

  function highlightKeywords(text) {
    let html = escapeHtml(text);
    for (const { pattern, text: translated, cls } of KEYWORD_TRANSLATIONS) {
      html = html.replace(pattern, `<span class="log-keyword log-keyword-${cls}">${translated}</span>`);
    }
    return html;
  }

  // Автоматический лог одной попытки установки (см. server/backend.py:
  // POST /install_log, app/web/frontend/js/screens/stage_wizard.js — тот же
  // приём на десктопе) — весь текст, что видел техник в этой log-панели за
  // текущий сеанс работы с моделью, плюс отдельный флаг "было ли что-то,
  // кроме чтения инструкции" (sessionHasActivity — взводится ТОЛЬКО в
  // onAdbLog, т.е. реальным действием Kotlin-стороны: ADB/USB/действия;
  // чистая навигация по инструкции/этапам такого не порождает). Сбрасывается
  // в openWizard() на каждую новую модель.
  let sessionLog = [];
  let sessionHasActivity = false;
  let sessionSent = false;

  function log(text) {
    sessionLog.push(text);
    const level = classifyLogLevel(text);
    const line = el("div", { class: `log-line log-line-${level}` });
    line.innerHTML = highlightKeywords(text);
    logPanelEl.appendChild(line);
    logPanelEl.scrollTop = logPanelEl.scrollHeight;
    // Свёрнутая нижняя полоса лога раньше всегда показывала статичное "Лог"
    // — последняя строка была видна, только раскрыв лог целиком. Теперь
    // сворачивание просто прячет ПОЛНУЮ историю, а не сам факт "что-то
    // происходит" — та же логика, что и в desktop-версии (нижняя строка).
    logLastLineEl.textContent = text;
    logLastLineEl.className = `log-last-line log-line-${level}`;
  }

  // success=true — все этапы пройдены (см. advanceAfter); false — техник
  // явно ушёл из мастера (см. __handleBackPress) или открыл другую модель,
  // не долистав эту (см. openWizard). Не шлём, если реальной активности не
  // было (открыл/пролистал инструкцию и ушёл) — незачем копить мусор.
  function flushSessionLog(success) {
    if (sessionSent || !sessionHasActivity || !sessionLog.length) return;
    sessionSent = true;
    Bridge.call("install_log_send", {
      brand: model ? model.brand : "",
      model: model ? (model.display_label || model.name) : "",
      modification: model ? (model.modification || "") : "",
      success, log: sessionLog.join("\n"),
    });
  }

  function setLogOpen(open) {
    logOverlayEl.classList.toggle("open", open);
    logOverlayEl.setAttribute('aria-hidden',String(!open));
    if (open) logPanelEl.scrollTop = logPanelEl.scrollHeight;
  }

  // Окно «этап выполняется → итог» (js/stage_run.js, общее с десктопом) — одно
  // на все этапы с процессом. Перерисовку страницы/переход дальше (afterRunClose)
  // делаем только когда техник закрыл окно с итогом, иначе render() сотрёт
  // страницу прямо под ним.
  let activeRun = null;
  let afterRunClose = null;
  window.StageRun.configure({ openLog: () => setLogOpen(true) });

  function onRunClosed() {
    activeRun = null;
    const after = afterRunClose;
    afterRunClose = null;
    if (after) after();
  }

  // tag — какой именно процесс открыл окно: результат чужого процесса (например,
  // неожиданное событие подключения флешки во время установки) не должен
  // закрыть чужое окно.
  let activeRunTag = null;
  function openStageRun(options) {
    activeRunTag = options.tag || null;
    activeRun = window.StageRun.open({ ...options, onClose: onRunClosed });
    return activeRun;
  }

  function finishRun(outcome, after, tag = null) {
    const run = activeRun;
    if (!run || run.finished || (tag && activeRunTag !== tag)) { after(); return; }
    afterRunClose = after;
    run.finish(outcome);
  }

  // Известные shell-команды консоли (см. WebBridge.kt: adbShellCommand —
  // здесь ВСЕГДА ровно одна shell-команда на уже подключённом устройстве,
  // нет отдельных adb-команд верхнего уровня вроде "devices"/"install
  // <путь-на-компьютере>", в отличие от desktop-версии, поэтому список
  // короче, чем ADB_CONSOLE_SUGGESTIONS в app/web/frontend/js/app.js) — тот
  // же приём: value — что реально выполнится, label — красивая подпись и в
  // подсказке (datalist), и в эхе введённой команды (см. logConsoleCommand).
  const SHELL_CMD_SUGGESTIONS = [
    { value: "pm list packages", label: "Список всех установленных пакетов" },
    { value: "pm list packages -3", label: "Список пользовательских (не системных) приложений" },
    { value: "pm list packages -s", label: "Список системных приложений" },
    { value: "pm path ", label: "Путь к APK установленного пакета — pm path <пакет>" },
    { value: "pm clear ", label: "Очистить данные приложения — pm clear <пакет>" },
    { value: "pm uninstall ", label: "Удалить пакет через pm — pm uninstall <пакет>" },
    { value: "pm grant ", label: "Выдать разрешение — pm grant <пакет> <разрешение>" },
    { value: "pm disable-user ", label: "Отключить приложение — pm disable-user <пакет>" },
    { value: "pm enable ", label: "Включить приложение — pm enable <пакет>" },
    { value: "dumpsys battery", label: "Статус батареи" },
    { value: "dumpsys package ", label: "Подробности о пакете — dumpsys package <пакет>" },
    { value: "dumpsys meminfo ", label: "Использование памяти приложением — dumpsys meminfo <пакет>" },
    { value: "dumpsys activity activities", label: "Стек запущенных активностей" },
    { value: "dumpsys cpuinfo", label: "Загрузка процессора по процессам" },
    { value: "wm size", label: "Разрешение экрана" },
    { value: "wm size reset", label: "Сбросить разрешение экрана к заводскому" },
    { value: "wm density", label: "Плотность экрана (DPI)" },
    { value: "wm density reset", label: "Сбросить плотность экрана к заводской" },
    { value: "svc wifi enable", label: "Включить Wi-Fi" },
    { value: "svc wifi disable", label: "Выключить Wi-Fi" },
    { value: "getprop", label: "Все системные свойства устройства" },
    { value: "getprop ro.product.model", label: "Модель устройства" },
    { value: "getprop ro.build.version.release", label: "Версия Android" },
    { value: "ps -A", label: "Список запущенных процессов" },
    { value: "am start -n ", label: "Запустить activity напрямую — am start -n <пакет>/<activity>" },
    { value: "am start -a android.settings.SETTINGS", label: "Открыть системные настройки Android" },
    { value: "am start -a android.settings.WIFI_SETTINGS", label: "Открыть настройки Wi-Fi" },
    { value: "am start -a android.settings.APPLICATION_DETAILS_SETTINGS -d package:", label: "Открыть настройки конкретного приложения — ...-d package:<пакет>" },
    { value: "am force-stop ", label: "Принудительно остановить приложение — am force-stop <пакет>" },
    { value: "input keyevent 3", label: "Кнопка «Домой»" },
    { value: "input keyevent 4", label: "Кнопка «Назад»" },
    { value: "input keyevent 26", label: "Кнопка питания (вкл/выкл экран)" },
    { value: "screencap -p /sdcard/screen.png", label: "Сделать скриншот на устройство" },
  ];

  let logCmdSuggestionsListEl = null;

  // Свой список подсказок вместо нативного <datalist> — на Android WebView
  // тот открывается как полноэкранный системный оверлей поверх ВСЕГО,
  // включая клавиатуру (реальная жалоба техника). Показываем/прячем прямо
  // внутри уже открытой карточки лога (см. css/style.css:
  // .log-cmd-suggestions-list — абсолютно позиционирован НАД полем ввода,
  // ограничен высотой карточки).
  function initLogCmdSuggestions() {
    logCmdSuggestionsListEl = document.getElementById("log-cmd-suggestions-list");
    if (!logCmdSuggestionsListEl) return;
    logCmdInput.addEventListener("input", updateLogCmdSuggestions);
    // mousedown, не click — срабатывает РАНЬШЕ, чем поле ввода теряет фокус
    // (blur), иначе список уже успевал бы спрятаться (см. ниже) раньше,
    // чем успевал сработать сам выбор подсказки.
    logCmdSuggestionsListEl.addEventListener("mousedown", (e) => e.preventDefault());
  }

  function updateLogCmdSuggestions() {
    if (!logCmdSuggestionsListEl) return;
    const normalized = stripRedundantPrefix(logCmdInput.value).toLowerCase();
    // Пустое поле — подсказки скрыты (не показываем весь список сразу же с
    // пустым вводом), только фильтрация по уже введённому тексту.
    if (!normalized) {
      hideLogCmdSuggestions();
      return;
    }
    const matches = SHELL_CMD_SUGGESTIONS.filter((s) => s.value.toLowerCase().startsWith(normalized)).slice(0, 8);
    renderLogCmdSuggestions(matches);
  }

  function renderLogCmdSuggestions(matches) {
    logCmdSuggestionsListEl.innerHTML = "";
    if (!matches.length) {
      logCmdSuggestionsListEl.hidden = true;
      return;
    }
    for (const suggestion of matches) {
      const item = el("li", {}, [
        el("span", { text: suggestion.label }),
        el("span", { class: "suggestion-value", text: suggestion.value }),
      ]);
      item.addEventListener("click", () => {
        logCmdInput.value = suggestion.value;
        hideLogCmdSuggestions();
        logCmdInput.focus();
      });
      logCmdSuggestionsListEl.appendChild(item);
    }
    logCmdSuggestionsListEl.hidden = false;
  }

  function hideLogCmdSuggestions() {
    if (logCmdSuggestionsListEl) logCmdSuggestionsListEl.hidden = true;
  }

  // Совпадает с AdbConsoleFormat.stripRedundantPrefix на стороне Kotlin —
  // свой нормализатор здесь нужен, чтобы найти красивую подпись даже если
  // человек по привычке дописал "adb"/"shell" в начале.
  function stripRedundantPrefix(command) {
    let result = command;
    const lower = result.toLowerCase();
    if (lower === "adb") return "";
    if (lower.startsWith("adb shell ")) result = result.slice("adb shell ".length);
    else if (lower.startsWith("adb ")) result = result.slice("adb ".length);
    if (result.toLowerCase().startsWith("shell ")) result = result.slice("shell ".length);
    return result.trim();
  }

  function findLogCmdSuggestion(normalized) {
    const exact = SHELL_CMD_SUGGESTIONS.find((s) => s.value.trim() === normalized);
    if (exact) return exact;
    const prefixMatches = SHELL_CMD_SUGGESTIONS.filter(
      (s) => s.value.length < normalized.length && normalized.startsWith(s.value));
    if (!prefixMatches.length) return undefined;
    return prefixMatches.reduce((best, s) => (s.value.length > best.value.length ? s : best));
  }

  function shortLabel(label) {
    const dashIndex = label.indexOf(" — ");
    return dashIndex === -1 ? label : label.slice(0, dashIndex);
  }

  // Эхо введённой команды — та же логика, что и в desktop-версии (см.
  // app/web/frontend/js/app.js: logConsoleCommand), до ответа от Kotlin
  // (который приходит отдельным событием "adb_log" уже из фонового потока,
  // см. WebBridge.kt: adbShellCommand — сам больше не дублирует эхо).
  function logConsoleCommand(command) {
    const normalized = stripRedundantPrefix(command);
    const match = findLogCmdSuggestion(normalized);
    let text;
    if (match) {
      const extraArg = normalized.slice(match.value.trim().length).trim();
      text = `❯ ${shortLabel(match.label)}${extraArg ? `: ${extraArg}` : ""}`;
    } else {
      text = `❯ ${command}`;
    }
    const line = el("div", { class: "log-line log-line-command", text });
    logPanelEl.appendChild(line);
    logPanelEl.scrollTop = logPanelEl.scrollHeight;
  }

  // Тот же ряд ввода обслуживает и свободную ADB-консоль, и чат с ИИ — без
  // отдельного поля/кнопки-переключателя режима: реальные adb/shell-команды
  // (см. SHELL_CMD_SUGGESTIONS выше) всегда латиницей, а вопрос технику
  // естественнее печатать по-русски — кириллица в тексте достаточно
  // надёжно отличает одно от другого.
  const CYRILLIC_RE = /[а-яёА-ЯЁ]/;
  // Кириллица не поможет технику, который решит спросить по-английски (редко,
  // но бывает) — явная команда "/ask <текст>" всегда уходит в чат, независимо
  // от языка, тем же приёмом, что и /clear чуть ниже.
  const CHAT_ASK_PREFIX_RE = /^\/ask\s+/i;
  // /deepseek, /qwen, /auto — принудительное переключение провайдера
  // ИИ-чата (см. chatSendMessage ниже) — тоже команды этого поля, не adb.
  const CHAT_PROVIDER_PREFIX_RE = /^\/(deepseek|qwen|auto)\b/i;

  function isChatQuestion(text) {
    return CYRILLIC_RE.test(text) || CHAT_ASK_PREFIX_RE.test(text) || CHAT_PROVIDER_PREFIX_RE.test(text);
  }

  function onLogCmdRun() {
    const command = logCmdInput.value.trim();
    if (!command) return;
    logCmdInput.value = "";
    hideLogCmdSuggestions();
    // /clear, /clr — очищают лог локально, не ADB-команда, на устройство
    // ничего не уходит (в отличие от остального, что через это же поле
    // идёт в adb_shell_command).
    if (command === "/clear" || command === "/clr") {
      clear(logPanelEl);
      logLastLineEl.textContent = "Лог";
      logLastLineEl.className = "log-last-line";
      return;
    }
    if (isChatQuestion(command)) {
      chatSendMessage(command.replace(CHAT_ASK_PREFIX_RE, ""));
      return;
    }
    logConsoleCommand(command);
    Bridge.call("adb_shell_command", { command });
  }

  // -- Чат с ИИ (см. chat_bridge.py, WebBridge.kt: chatSend/chatConfirmCommand,
  // server/backend.py: POST /chat) — прямо в этом же логе, никакой отдельной
  // панели/пузырей: вопрос техника, ответ и предложенная команда — обычные
  // строки .log-line. Команда, предложенная моделью, — отдельная строка с
  // кнопками "Выполнить"/"Отклонить", ничего не выполняется без явного
  // клика. После подтверждения результат уходит обратно в переписку, и чат
  // сам продолжает диалог (агентный цикл) — до CHAT_MAX_AUTO_TURNS
  // автоматических шагов подряд на одно сообщение техника.
  const CHAT_MAX_AUTO_TURNS = 6;
  const CHAT_HISTORY_SENT_TURNS = 16;
  const CHAT_RECENT_LOG_LINES = 40;
  let chatHistory = [];
  let chatAutoTurnsLeft = 0;
  // Принудительный выбор провайдера — команды /deepseek, /qwen, /auto (см.
  // chatSendMessage ниже). null — обычный автоматический режим (DeepSeek
  // первым, Qwen запасным на сервере). "Липкий" на всю сессию чата, пока не
  // сменят другой командой.
  let chatForcedProvider = null;
  const CHAT_PROVIDER_COMMAND_RE = /^\/(deepseek|qwen|auto)\b\s*(.*)$/is;
  const CHAT_PROVIDER_LABELS = { deepseek: "DeepSeek", qwen: "Qwen" };

  // Тело запроса к /chat на сервере ограничено (см. server/backend.py:
  // CHAT_MAX_BODY_BYTES) — один вывод вроде `dumpsys package …` (десятки
  // КБ) раньше забивал всю переписку: каждый следующий запрос отвечал
  // «некорректный запрос», пока вывод не уходил из последних реплик
  // (реальный лог #331). Модели хватает начала и конца вывода — режем
  // середину; в самом логе на экране вывод остаётся полным.
  const CHAT_OUTPUT_MAX_CHARS = 6000;
  const CHAT_LOG_LINE_MAX_CHARS = 500;

  function clipChatText(text, limit) {
    const value = String(text == null ? "" : text);
    if (value.length <= limit) return value;
    const head = Math.floor(limit * 0.6);
    const tail = limit - head;
    return `${value.slice(0, head)}\n… [обрезано ${value.length - limit} симв.] …\n${value.slice(value.length - tail)}`;
  }

  function chatRecentLogLines() {
    return Array.from(logPanelEl.querySelectorAll(".log-line"))
      .map((e) => clipChatText(e.textContent, CHAT_LOG_LINE_MAX_CHARS)).slice(-CHAT_RECENT_LOG_LINES);
  }

  function renderChatCommandLine(command, reason, providerTag) {
    const line = el("div", { class: "log-line chat-command-line" });
    line.appendChild(el("code", { text: `❯${providerTag || ""} ${command}` }));
    if (reason) line.appendChild(el("span", { class: "chat-command-reason", text: reason }));
    const actions = el("div", { class: "chat-command-actions" });
    const confirmBtn = el("button", { class: "accent", text: "Выполнить" });
    const rejectBtn = el("button", { text: "Отклонить" });
    actions.append(confirmBtn, rejectBtn);
    line.appendChild(actions);
    logPanelEl.appendChild(line);
    logPanelEl.scrollTop = logPanelEl.scrollHeight;

    confirmBtn.addEventListener("click", () => {
      confirmBtn.disabled = true;
      rejectBtn.disabled = true;
      Bridge.call("chat_confirm_command", { command });
    });
    rejectBtn.addEventListener("click", () => {
      confirmBtn.disabled = true;
      rejectBtn.disabled = true;
      log("Команда отклонена — не выполнена.");
      chatHistory.push({ role: "tool_result", command, output: "техник отклонил выполнение команды", ok: false });
    });
  }

  function chatSendTurn() {
    if (chatAutoTurnsLeft <= 0) {
      log("ИИ: достигнут лимит автоматических шагов подряд — напишите новое сообщение, чтобы продолжить.");
      return;
    }
    chatAutoTurnsLeft -= 1;
    Bridge.call("chat_send", {
      history: JSON.stringify(chatHistory.slice(-CHAT_HISTORY_SENT_TURNS)),
      recent_log: JSON.stringify(chatRecentLogLines()),
      provider: chatForcedProvider || "",
    });
    // Ответ придёт отдельным событием "chat_reply" — chatSend в Kotlin
    // работает в фоновом потоке и возвращается сразу.
  }

  function chatSendMessage(text) {
    if (!Bridge.call("settings_preferences", {}).chat_enabled) {
      log("ИИ-чат отключён в настройках.");
      return;
    }
    // /deepseek, /qwen — закрепить провайдера на все следующие сообщения
    // (без автофолбэка на сервере при его ошибке); /auto — обычный режим.
    // Текст после команды ("/qwen почему упала установка?") — уже реальный
    // вопрос, уходит сразу с новым провайдером; голая команда — просто
    // подтверждение режима.
    const commandMatch = CHAT_PROVIDER_COMMAND_RE.exec(text);
    if (commandMatch) {
      const mode = commandMatch[1].toLowerCase();
      const rest = commandMatch[2].trim();
      chatForcedProvider = mode === "auto" ? null : mode;
      log(chatForcedProvider
        ? `Режим чата: только ${CHAT_PROVIDER_LABELS[chatForcedProvider]}, без автопереключения.`
        : "Режим чата: автоматический (DeepSeek, при недоступности — Qwen).");
      if (!rest) return;
      text = rest;
    }
    const line = el("div", { class: "log-line chat-bubble chat-user", text });
    logPanelEl.appendChild(line);
    logPanelEl.scrollTop = logPanelEl.scrollHeight;
    chatHistory.push({ role: "user", content: text });
    chatAutoTurnsLeft = CHAT_MAX_AUTO_TURNS;
    chatSendTurn();
  }

  function onChatReply(event) {
    const reply = event.reply || {};
    if (!reply.ok && reply.error) {
      log(`ИИ: ${reply.error}`);
      return;
    }
    const providerTag = reply.provider && CHAT_PROVIDER_LABELS[reply.provider]
      ? ` [${CHAT_PROVIDER_LABELS[reply.provider]}]` : "";
    if (reply.type === "command" && reply.command) {
      const summary = `Предлагаю выполнить: ${reply.command}` + (reply.reason ? ` — ${reply.reason}` : "");
      chatHistory.push({ role: "assistant", content: summary });
      renderChatCommandLine(reply.command, reply.reason, providerTag);
      return;
    }
    const text = reply.content || "";
    const line=el("div",{class:"log-line chat-bubble chat-assistant",text:`ИИ${providerTag}: ${text}`});logPanelEl.append(line);logPanelEl.scrollTop=logPanelEl.scrollHeight;
    chatHistory.push({ role: "assistant", content: text });
  }

  function onChatCommandResult(event) {
    log(event.output || "(пусто)");
    chatHistory.push({ role: "tool_result", command: event.command,
      output: clipChatText(event.output, CHAT_OUTPUT_MAX_CHARS), ok: !!event.ok });
    // Замыкаем агентный цикл — модель видит результат и может предложить
    // следующий шаг или завершить обычным текстовым ответом.
    chatSendTurn();
  }

  // cars/<brand>/logo.png качается вместе со скриптами модели (см.
  // content_sync.sync_scripts на Python-стороне) в context.filesDir —
  // WebViewAssetLoader отдаёт его под /data/ (см. MainActivity.kt), а не
  // относительно нашей же страницы (та лежит под /assets/), поэтому нужен
  // полный URL, а не относительный путь.
  function dataUrl(path) {
    return `https://appassets.androidplatform.net/data/${String(path).split("/").map(encodeURIComponent).join("/")}`;
  }

  function showScreen(name) {
    document.body.classList.toggle('catalog-mode',name==='picker');
    screenPicker.classList.toggle('active',name==='picker');screenWizard.classList.toggle('active',name==='wizard');
    updateTopBack();topHelpBtn.style.visibility='visible';topAccountBtn.style.visibility='visible';
    clear(topTitleEl);topTitleEl.classList.remove('marquee');
    topTitleEl.appendChild(name==='wizard'&&model?el('span',{text:model.name||model.display_label,title:model.display_label}):el('img',{class:'topbar-logo',src:'img/logo-full-dark.svg',alt:'Magic SQD'}));
  }

  // -- пикер марка -> модель[->модификация] --------------------------------
  // Назад теперь только через кнопку в шапке (см. updateTopBack) — та же
  // позиция и логика, что и в мастере этапов, поэтому здесь просто
  // некликабельная подсказка "где я" (марка/группа), без корневого "Марка"
  // (на уровне списка марок его вообще нет — там и так весь экран это марки).
  function renderBreadcrumb() {
    const parts = [];
    if (selectedBrand) parts.push(selectedBrand.name);
    if (selectedGroup) parts.push(selectedGroup.name);
    clear(breadcrumbEl);
    breadcrumbEl.style.display = parts.length ? "" : "none";
    parts.forEach((text, i) => {
      if (i > 0) breadcrumbEl.appendChild(el("span", { text: " › " }));
      breadcrumbEl.appendChild(el("span", { class: "crumb current", text }));
    });
  }

  function resetPickerScroll() {
    // Экран picker остаётся тем же scroll-контейнером между марками. Без
    // сброса длинный список Geely открывался на старой позиции прокрутки,
    // из-за чего заголовок марки оказывался под первой карточкой.
    screenPicker.scrollTop = 0;
    requestAnimationFrame(() => { screenPicker.scrollTop = 0; });
  }

  // Кнопка "Назад" в шапке (topBackBtn) — единственный способ идти назад и
  // в пикере, и в мастере (та же позиция слева от лого, см. .topbar
  // button.back), цель зависит от того, где мы сейчас: из модификаций — в
  // группы, из групп — в марки, из марок — скрыта (там уже верхний уровень),
  // из мастера — на пикер (как и раньше).
  function updateTopBack() {
    if (screenWizard.classList.contains("active")) {
      topBackBtn.style.visibility = "visible";
      topBackBtn.replaceChildren(LabUI.icon("back"));
      topBackBtn.setAttribute("aria-label","Назад");
      topBackBtn.onclick = () => {if(labInstallBusy){showLabBusyNotice();return;}showScreen("picker");};
    } else if (selectedGroup) {
      topBackBtn.style.visibility = "visible";
      topBackBtn.replaceChildren(LabUI.icon("back"));
      topBackBtn.setAttribute("aria-label","Назад");
      topBackBtn.onclick = () => showGroupStep(selectedBrand);
    } else if (selectedBrand) {
      topBackBtn.style.visibility = "visible";
      topBackBtn.replaceChildren(LabUI.icon("back"));
      topBackBtn.setAttribute("aria-label","Назад");
      topBackBtn.onclick = () => showBrandStep();
    } else {
      topBackBtn.style.visibility = "hidden";
      topBackBtn.onclick = null;
    }
  }

  function vehicleIcon() {
    const icon = el("span", { class: "catalog-vehicle", "aria-hidden": "true" });
    icon.innerHTML = `<svg viewBox="0 0 120 64" focusable="false"><path d="M20 42h80l-5-16c-1.3-4-5-7-9.2-7H46c-4.4 0-8.4 2.5-10.4 6.4L29 38H20c-4.4 0-8 3.6-8 8v5h8v-9Z"/><path d="M34 27h22v11H29l5-11Zm26 0h24c2.8 0 5.2 1.8 6.1 4.4L92 38H60V27Z" class="catalog-vehicle-window"/><circle cx="33" cy="48" r="8"/><circle cx="87" cy="48" r="8"/></svg>`;
    return icon;
  }

  function defaultModelLogo(label) {
    const img = el("img", {
      class: "catalog-model-logo catalog-model-logo-default",
      src: "img/default-model-logo.png", alt: `Логотип ${label}`,
      loading: "lazy",
    });
    img.addEventListener("error", () => img.replaceWith(vehicleIcon()), { once: true });
    return img;
  }

  function renderList(items) {
    document.body.classList.remove("model-detail-mode");
    document.getElementById("cat-heading").textContent=selectedGroup ? selectedGroup.name : selectedBrand ? selectedBrand.name : "Автомобили";
    listEl.dataset.level=selectedGroup ? "modification" : selectedBrand ? "model" : "brand";
    pickerSearchEl.placeholder=selectedGroup ? "Найти версию" : selectedBrand ? "Найти модель" : "Марка или модель";
    const reset=document.getElementById("cat-reset");
    reset.hidden=!pickerSearchEl.value;
    reset.onclick=()=>{pickerSearchEl.value="";pickerSearchEl.dispatchEvent(new Event("input",{bubbles:true}));pickerSearchEl.focus();};
    document.getElementById("cat-count").textContent="";
    clear(listEl);
    if (!items.length) {
      listEl.appendChild(el("p", { class: "empty-hint", text: "Ничего не найдено. Попробуйте другой запрос." }));
      return;
    }
    const query = pickerSearchEl.value.trim().toLocaleLowerCase();
    const visible = query ? items.filter((item) => `${item.label} ${item.meta || ""}`.toLocaleLowerCase().includes(query)) : items;
    if (!visible.length) {
      listEl.appendChild(el("p", { class: "empty-hint", text: "Ничего не найдено. Попробуйте другой запрос." }));
      return;
    }
    document.getElementById("cat-count").textContent=String(visible.length);
    visible.forEach(item => listEl.appendChild(window.CatalogUI.card({kind:item.kind,name:item.label,meta:item.meta,image:item.icon ? dataUrl(item.icon) : null,colors:item.colors || (item.color ? [item.color] : []),action:item.action,onClick:item.onClick})));
  }

  // Та же логика, что и в desktop-версии (см. app/web/frontend/js/screens/
  // main_picker.js: brandCardStatus/groupCardStatus) — на марках только
  // синяя метка, у модели с модификациями сразу все точки вместо одной
  // сведённой (иначе одна сломанная модификация красила бы всю карточку).
  function brandCardColor(brand) {
    return brand.status_color === "blue" ? "blue" : null;
  }

  function groupCardColors(group) {
    if (group.has_modifications) {
      return { colors: group.modifications.map((m) => m.status_color) };
    }
    return { color: group.status_color };
  }

  function plural(number, one, few, many) {
    const mod10 = number % 10;
    const mod100 = number % 100;
    return mod10 === 1 && mod100 !== 11 ? one : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14) ? few : many;
  }

  function searchResults(query) {
    const results = [];
    for (const brand of carsData.brands || []) {
      if (brand.name.toLocaleLowerCase().includes(query)) results.push({ kind: "brand", label: brand.name, meta: `${brand.groups.length} ${plural(brand.groups.length, "модель", "модели", "моделей")}`, color: brandCardColor(brand), icon: brand.logo, action: "Открыть марку", onClick: () => showGroupStep(brand) });
      for (const group of brand.groups) {
        if (group.name.toLocaleLowerCase().includes(query)) results.push({ kind: "model", label: group.name, meta: brand.name, ...groupCardColors(group), icon: group.logo || (group.leaf && group.leaf.logo), action: group.has_modifications ? "Выбрать версию" : "Открыть", onClick: () => showModificationStep(group) });
        for (const modification of group.modifications || []) {
          if ((modification.modification || "").toLocaleLowerCase().includes(query)) results.push({ kind: "variant", label: `${group.name} — ${modification.modification}`, meta: brand.name, color: modification.status_color, icon: modification.logo || group.logo, onClick: () => selectModel(modification) });
        }
      }
    }
    return results;
  }

  function showBrandStep() {
    if(selectedBrand||selectedGroup)pickerSearchEl.value="";
    selectedBrand = null;
    selectedGroup = null;
    renderBreadcrumb();
    updateTopBack();
    resetPickerScroll();
    if (!carsData || !carsData.brands || !carsData.brands.length) {
      renderList([]);
      return;
    }
    const query = pickerSearchEl.value.trim().toLocaleLowerCase();
    if (query) { renderList(searchResults(query)); return; }
    renderList(carsData.brands.map((b) => ({ kind: "brand", label: b.name, meta: `${b.groups.length} ${plural(b.groups.length, "модель", "модели", "моделей")}`, color: brandCardColor(b), icon: b.logo, onClick: () => showGroupStep(b) })));
  }

  function showGroupStep(brand) {
    if(!brand){pickerSearchEl.value="";showBrandStep();return;}
    if(selectedBrand!==brand||selectedGroup)pickerSearchEl.value="";
    selectedBrand = brand;
    selectedGroup = null;
    renderBreadcrumb();
    updateTopBack();
    resetPickerScroll();
    renderList(brand.groups.map((g) => ({
      kind: "model", label: g.name, icon: g.logo || (g.leaf && g.leaf.logo),
      meta: g.has_modifications ? `${g.modifications.length} ${plural(g.modifications.length, "версия", "версии", "версий")}` : g.leaf.no_instruction ? "Способ уточняется" : "Открыть инструкцию",
      action: g.has_modifications ? "Выбрать версию" : "Открыть",
      ...groupCardColors(g),
      onClick: () => showModificationStep(g),
    })));
  }

  function showModificationStep(group) {
    selectedBrand=carsData.brands.find(b=>b.groups.some(g=>g===group))||selectedBrand;
    selectedGroup=group;pickerSearchEl.value='';
    renderBreadcrumb();updateTopBack();resetPickerScroll();
    document.body.classList.add('model-detail-mode');
    listEl.dataset.level='modification';clear(listEl);
    const img=group.logo||group.leaf?.logo;
    const heroImg=group.hero||group.leaf?.hero;
    listEl.append(CatalogUI.detail({brand:selectedBrand?.name||'',group:group.name,src:img?dataUrl(img):null,heroSrc:heroImg?dataUrl(heroImg):'',
      versions:group.has_modifications?group.modifications:[group.leaf],onOpen:selectModel}));
  }

  function openModel(modelSummary) {
    // Флашим лог ПРЕДЫДУЩЕЙ модели (если была реальная активность и его ещё
    // не отправили) — обязательно ДО того, как model перезапишется новой,
    // иначе flushSessionLog отправит уже данные новой модели под старым
    // логом. Обычный уход через "Назад" в каталог обрабатывается отдельно
    // (см. __handleBackPress) — это специально на случай прямого перехода
    // к другой модели, минуя экран каталога.
    if (model) flushSessionLog(false);
    model = Bridge.call("scanner_select_model", {
      key: modelSummary.key,
      brand: modelSummary.brand,
      name: modelSummary.name,
      modification: modelSummary.modification || "",
    });
    openWizard();
  }

  // yellow ("черновой способ") — предупреждаем, но даём продолжить; red
  // ("не работает") — тут "всё равно открыть" вводило бы техника в
  // заблуждение, что способ рабочий, поэтому только закрыть окно.
  function selectModel(modelSummary) {
    if (modelSummary.status_color === "yellow" || modelSummary.status_color === "red") {
      showStatusWarningModal(modelSummary);
      return;
    }
    openModel(modelSummary);
  }

  function loadCars() {
    carsData = Bridge.call("scanner_list_cars", {});
    showBrandStep();
  }

  // -- прогресс-бар синхронизации ------------------------------------------
  // Скачивание идёт в фоновом Kotlin-потоке (см. WebBridge.startSync/
  // syncModelPayload), результат приходит только по завершении (событие
  // sync_finished/model_sync_finished) — поэтому ход дела узнаём опросом
  // get_sync_progress (см. mobile_bridge.py: пишется из content_sync.py
  // on_progress по мере закачки каждого файла). Раньше тут был только
  // статичный текст без вообще какой-либо индикации хода дела.
  let syncPollTimer = null;
  let settingsSyncButton = null;
  let settingsSyncTimeout = null;

  function makeProgressBar() {
    const fill = el("div", { class: "progress-bar-fill" });
    const bar = el("div", { class: "progress-bar indeterminate" }, [fill]);
    return { bar, fill };
  }

  function updateProgressBar(bar, fill, done, total) {
    if (total > 0) {
      bar.classList.remove("indeterminate");
      fill.style.width = `${Math.min(100, Math.round((done / total) * 100))}%`;
    } else {
      bar.classList.add("indeterminate");
      fill.style.width = "";
    }
  }

  // Оверлей первого запуска (см. css/style.css: .catalog-startup-overlay) —
  // портировано из desktop-версии (app/web/frontend/js/screens/main_picker.js:
  // showStartupLoading/setStartupProgress/hideStartupLoading), показывается
  // ТОЛЬКО когда каталог ещё пуст (первый запуск программы, ничего ещё не
  // скачано) — для обычных, не первых синхронизаций остаётся прежний
  // ненавязчивый текстовый статус наверху (.sync-status, см. startSync ниже).
  let startupOverlayEl, startupProgressFillEl, startupProgressLabelEl;

  function showStartupOverlay() {
    if (!startupOverlayEl) return;
    startupOverlayEl.hidden = false;
    // На части WebView CSS-анимация спиннера не запускается сама сразу
    // после снятия [hidden] в том же такте — форсируем reflow между
    // сбросом и восстановлением animation, это гарантированно перезапускает
    // анимацию независимо от движка.
    const spinner = startupOverlayEl.querySelector(".catalog-startup-spinner");
    if (spinner) {
      spinner.style.animation = "none";
      void spinner.offsetWidth;
      spinner.style.animation = "";
    }
    startupProgressFillEl.style.width = "12%";
    startupProgressLabelEl.textContent = "Подготавливаем список моделей…";
  }

  function updateStartupOverlay(done, total) {
    if (!startupOverlayEl || startupOverlayEl.hidden || total <= 0) return;
    const percent = Math.max(4, Math.min(100, Math.round((done / total) * 100)));
    startupProgressFillEl.style.width = `${percent}%`;
    startupProgressLabelEl.textContent = `${percent}% скачано (${done} из ${total})`;
  }

  function hideStartupOverlay() {
    if (!startupOverlayEl) return;
    startupProgressFillEl.style.width = "100%";
    startupOverlayEl.hidden = true;
  }

  function pollSyncProgress(phase, labelEl, labelText, bar, fill, alsoOverlay) {
    stopSyncPoll();
    syncPollTimer = setInterval(() => {
      const p = Bridge.call("get_sync_progress", {});
      if (!p || p.phase !== phase) return;
      updateProgressBar(bar, fill, p.done, p.total);
      labelEl.textContent = p.total > 0 ? `${labelText} (${p.done} из ${p.total})` : labelText;
      if (alsoOverlay) updateStartupOverlay(p.done, p.total);
    }, 300);
  }

  function stopSyncPoll() {
    if (syncPollTimer) {
      clearInterval(syncPollTimer);
      syncPollTimer = null;
    }
  }

  function startSync(isFirstLaunch) {
    clear(syncStatusEl);
    const label = el("span", { text: "Синхронизация каталога с сервером…" });
    const { bar, fill } = makeProgressBar();
    syncStatusEl.appendChild(label);
    syncStatusEl.appendChild(bar);
    syncStatusEl.style.display = "";
    // Список скрыт на время синхронизации — иначе на пустом при первом
    // запуске каталоге показывалась карточка "Пусто" прямо под прогресс-
    // баром, будто марок и правда нет, хотя они просто ещё не скачались.
    listEl.style.display = "none";
    if (isFirstLaunch) showStartupOverlay();
    Bridge.call("start_sync", {});
    pollSyncProgress("cars", label, "Синхронизация каталога с сервером…", bar, fill, isFirstLaunch);
  }

  function onSyncFinished(event) {
    stopSyncPoll();
    syncStatusEl.style.display = "none";
    listEl.style.display = "";
    hideStartupOverlay();
    const result = event.result || {};
    if (settingsSyncTimeout) {
      clearTimeout(settingsSyncTimeout);
      settingsSyncTimeout = null;
    }
    if (settingsSyncButton) {
      settingsSyncButton.disabled = false;
      setMenuActionLabel(settingsSyncButton, result.error ? "Не удалось проверить" : "Каталог обновлён");
      settingsSyncButton = null;
    }
    if (result.error) {
      console.error("sync_finished с ошибкой:", result.error);
      return;
    }
    // Перечитываем каталог и остаёмся на том же шаге пикера, если это
    // всё ещё возможно (марка могла исчезнуть после синка).
    const brandName = selectedBrand ? selectedBrand.name : null;
    loadCars();
    if (brandName) {
      const brand = carsData.brands.find((b) => b.name === brandName);
      if (brand) showGroupStep(brand);
    }
  }

  // -- мастер этапов -------------------------------------------------------
  // Исполняет _wizard_spec.json (см. python/wizard_spec.py) напрямую поверх
  // ADB/USB-транспорта (UsbAdbTransport/AdbInstall) — БЕЗ повторной
  // реализации stages.py/install.py (та исполняется только на desktop).
  // Модели без _wizard_spec.json (написанные вручную) показывают заглушку.
  let adbConnected = false;
  let usbConnected = false;
  // Состояние этапа "qr_adb" (см. renderQrAdbStage/onQrAdbWriteResult/
  // onQrAdbPasswordResult) — храним отдельно от stage, а не мутируем уже
  // отрисованный DOM из обработчика события: та же схема, что и у
  // остального мастера (см. onAdbStageResult — просто render() заново),
  // только тут ещё и сам код нужно показать техника ПОСЛЕ переотрисовки.
  let qrAdbWriteStatus = null;
  let qrAdbResult = null;
  let usbOperation = null;
  let usbStageResults = {};
  let stageOperation = null;
  let stageExecutionResults = {};
  let appsSelection = {}; // stage.index -> {variant, optionalChecked: Set<path>}
  // Список необязательных APK, отмеченных техником на ЛЮБОМ "apps"-этапе —
  // общий на всю установку (аналог desktop ctx.selected_apks), т.к.
  // "usb"-этап с usb_copy_selected_apks просто копирует то, что отметили
  // раньше, независимо от того, на каком именно apps-этапе это было.
  let globalSelectedApks = new Set();
  let appPickerSession = null;
  let inlineAppsSession = null;
  let pendingPersonalPicker = null;
  let appInstallOperation = null;
  let appInstallResults = {};
  // Общая библиотека приложений (apk/, см. python/apk_library.py) —
  // {name, description, category, remote_only, size, path}[], одна на весь
  // мастер, качается в фоне при открытии (см. Bridge.call scanner_list_apks
  // ниже) — список приходит сразу, сами .apk докачиваются точечно перед
  // adb_install_apks/usb_run_stage (см. WebBridge.kt: ensureApksDownloaded).
  let apkLibrary = [];
  let apkLibraryLoaded = false;
  // Свои APK, добавленные техником прямо на этапе через "Добавить свой
  // APK..." (см. renderApkTree) — портовый эквивалент desktop stage_wizard.js:
  // personalApks, только файлы копируются в приватное хранилище приложения
  // через SAF (см. WebBridge.kt: onApksPicked), а не читаются с диска
  // напрямую. Живут только в памяти этого сеанса работы с моделью, как и на
  // desktop — не сохраняются, не публикуются никуда.
  let personalApks = [];
  // "wifi"/"wifi_port" — верхнеуровневые поля _wizard_spec.json (не привязаны
  // к конкретному этапу): если true, у модели нет доступного проводного ADB
  // на adb/apps/actions-этапах — подключение всегда по Wi-Fi (аналог desktop
  // cars/_shared/wifi_adb.py, см. onAdbConnect). lastWifiHost — чтобы не
  // перевводить IP на каждое переподключение в рамках одного мастера.
  let modelWifi = false;
  let modelWifiPort = 5555;
  let lastWifiHost = "";
  let installCompletedShown = false;
  // Выбор техника на apps-этапах с apps_connection == "ask" (провод/Wi-Fi) —
  // по index этапа, чтобы сохранялся при переходах назад-вперёд в рамках
  // одного мастера (аналог appsSelection). См. connectionModeFor.
  let appsConnectionChoice = {};

  function openWizard() {
    sessionLog = [];
    sessionHasActivity = false;
    sessionSent = false;
    historyStack.length = 0;
    currentIndex = 0;
    stages = [];
    appsSelection = {};
    appsConnectionChoice = {};
    globalSelectedApks = new Set();
    inlineAppsSession = null;
    pendingPersonalPicker = null;
    appInstallOperation = null;
    appInstallResults = {};
    apkLibrary = [];
    apkLibraryLoaded = false;
    personalApks = [];
    installCompletedShown = false;
    qrAdbWriteStatus = null;
    qrAdbResult = null;
    usbOperation = null;
    usbStageResults = {};
    stageOperation = null;
    stageExecutionResults = {};
    modelWifi = false;
    modelWifiPort = 5555;
    // Новая модель — потенциально другая физическая магнитола/флешка,
    // старое ADB/USB-соединение (если было) к ней уже не относится. Оставлять
    // статус "подключено" от предыдущей модели было бы вводящим в заблуждение.
    setAdbStatus(false, "ADB: не подключено");
    setUsbStatus(false, "Флешка: не подключена");
    Bridge.call("adb_disconnect", {});
    Bridge.call("usb_disconnect", {});
    showScreen("wizard");
    closePhotoLightbox();
    clear(wizardContentEl);
    // Пока реальные этапы ещё не загрузились — скрываем панели ADB/флешки,
    // иначе на долю секунды видно их состояние от ПРЕДЫДУЩЕЙ модели (или
    // просто "не подключено" не по делу), пока не подъедет спека и не
    // отрисуется настоящий первый этап.
    adbBarEl.style.display = "none";
    usbBarEl.style.display = "none";
    const modelSyncLabel = el("p", { class: "stage-text", text: "Скачиваю файлы модели с сервера..." });
    const { bar: modelSyncBar, fill: modelSyncFill } = makeProgressBar();
    wizardContentEl.appendChild(el("div", { class: "stage-loading" }, [modelSyncLabel, modelSyncBar]));
    renderNav();
    log(`Открыта модель: ${model.display_label}`);
    // Диагностическая шапка лога установки (та же причина, что у десктопной
    // версии — см. app/web/frontend/js/screens/stage_wizard.js: без версии
    // программы и client_id в присылаемом на сервер логе установки
    // невозможно понять, с какой сборки пришла жалоба техника). На Android
    // раньше этой строки не было вовсе — версия/id только тут появились в
    // "app_version" (см. WebBridge.kt).
    try {
      const info = Bridge.call("app_version", {});
      log(`Magic SQD v${info.version} (Android) · client=${info.client_id}`);
    } catch (e) { /* не критично для установки — просто не будет диагностической строки */ }
    Bridge.call("sync_model_payload", { model_key: model.key });
    pollSyncProgress("model", modelSyncLabel, "Скачиваю файлы модели с сервера...", modelSyncBar, modelSyncFill);
    Bridge.call("scanner_list_apks", {});
  }

  function onApkLibraryResult(event) {
    apkLibrary = event.apks || [];
    apkLibraryLoaded = true;
    if (appPickerSession) { appPickerSession.render(); return; }
    if (!labInstallBusy && inlineAppsSession?.page.isConnected) { inlineAppsSession.render(); return; }
    // Если текущий этап как раз показывает общую библиотеку — перерисуем,
    // теперь она подъехала (список обычно приходит уже к моменту, когда
    // техник долистает до нужного этапа, но не гарантированно).
    const current = stages[currentIndex];
    if (!labInstallBusy && current && (current.type === "apps" || (current.type === "usb" && current.usb_copy_selected_apks))) {
      render();
    }
  }

  function onModelSyncFinished(event) {
    stopSyncPoll();
    const result = event.result || {};
    (result.log || []).forEach(log);
    if (result.error) log(`Ошибка синхронизации файлов: ${result.error}`);

    const spec = Bridge.call("install_load_stages", { model_key: model.key });
    if (spec.unsupported) {
      stages = [{
        index: 0, type: "unsupported", supported: false, title: "Не поддерживается",
        description: "Для этой модели нет _wizard_spec.json (написана вручную в редакторе кода) — " +
          "мобильный мастер пока умеет исполнять только модели, собранные визуальным конструктором на десктопе.",
      }];
    } else if (spec.error) {
      log(`Ошибка загрузки этапов: ${spec.error}`);
      stages = [{
        index: 0, type: "unsupported", supported: false, title: "Ошибка",
        description: `Не удалось разобрать этапы установки: ${spec.error}`,
      }];
    } else {
      stages = spec.steps || [];
      modelWifi = !!spec.wifi;
      modelWifiPort = spec.wifi_port || 5555;
    }
    currentIndex = 0;
    render();
  }

  function setAdbStatus(connected, text) {
    adbConnected = connected;
    adbStatusEl.textContent = text;
    adbStatusEl.title = text;
    adbStatusEl.classList.toggle("connected", connected);
    adbConnectBtn.textContent = connected ? "Переподключить" : "Подключить ADB";
  }

  // Способ подключения для ТЕКУЩЕГО этапа: apps-этап сам решает через
  // apps_connection ("wired"/"wifi"/"ask" — см. car_generator.py на
  // desktop, теперь и wizard_spec.py здесь), независимо от modelWifi;
  // adb/actions по-прежнему подчиняются только modelWifi (единой на всю
  // модель) — тот же контракт, что и в desktop stage_wizard.js:
  // buildTransportBar. "ask" — то, что техник выбрал на переключателе
  // (см. appsConnectionChoice/updateTransportBars), по умолчанию "wired".
  function connectionModeFor(stage) {
    if (stage && (stage.type === "apps" || stage.type === "actions")) {
      const mode = (stage.type === "apps" ? stage.apps_connection : stage.actions_connection) || "wired";
      return mode === "ask" ? (appsConnectionChoice[stage.index] || "wired") : mode;
    }
    return modelWifi ? "wifi" : "wired";
  }

  // Порт для ТЕКУЩЕГО этапа — apps-этап несёт свой (apps_wifi_port,
  // задаётся в редакторе на самом этапе), independent от общего
  // modelWifiPort (тот для adb/actions). null — заранее не известен, поле
  // в promptHostPicker останется пустым, техник впишет сам.
  function connectionPortFor(stage) {
    if (stage && stage.type === "apps") return stage.apps_wifi_port;
    if (stage && stage.type === "actions") return stage.actions_wifi_port;
    return modelWifiPort;
  }

  function onAdbConnect() {
    const stage = stages[currentIndex];
    if (connectionModeFor(stage) === "wifi") {
      promptHostPicker(
        "IP-адрес магнитолы для Wi-Fi ADB:",
        connectionPortFor(stage),
        (host, port) => {
          lastWifiHost = host;
          modelWifiPort = port; // на случай переподключения в рамках того же мастера
          setAdbStatus(false, "ADB: подключаюсь по Wi-Fi...");
          Bridge.call("adb_connect_wifi", { host, port });
        },
        { editablePort: true, discoverAdbService: true }
      );
      return;
    }
    setAdbStatus(false, "ADB: подключаюсь...");
    Bridge.call("adb_connect", {});
  }

  function onAdbConnectResult(event) {
    const r = event.result || {};
    if (r.connected) {
      const banner = String(r.banner || "");
      const product = banner.match(/(?:^|[;:])r[od]\.product\.model=([^;]+)/i)?.[1];
      const label = product || (banner && !banner.includes("=") ? banner : "");
      setAdbStatus(true, label ? `Подключено · ${label}` : "ADB подключено");
      adbStatusEl.title = banner || "ADB подключено";
      log(banner ? `ADB подключён: ${banner}` : "ADB подключён.");
    } else {
      setAdbStatus(false, "ADB: не подключено");
      log(`ADB: не удалось подключиться — ${r.reason || "?"}`);
      const stage = stages[currentIndex];
      if (r.no_device && stage && connectionModeFor(stage) !== "wifi") showOtgHintModal();
      if (stage && connectionModeFor(stage) !== "wifi") {
        // USB-C↔USB-C кабель напрямую часто не работает: обе стороны
        // Type-C сами договариваются о роли host/device через CC-пин, и
        // магнитола (тоже умеющая быть host — для флешек) может выиграть
        // эту роль вместо телефона — тогда никто не enumerate-ится вообще,
        // без единой ошибки на экране (см. переписку с клиентом на Huawei
        // Pura 90S Pro — тот же профильный баг у Bugjaeger, официально
        // задокументирован в их FAQ). USB-A↔C кабель через OTG-переходник
        // жёстко фиксирует роль телефона как host — не даёт магнитоле
        // шанса перехватить её.
        log("Если это провод USB-C↔USB-C напрямую — попробуйте USB-A↔C кабель через OTG-переходник: с прямым C↔C телефон и магнитола могут не договориться, кто из них host, и тогда подключение вообще не происходит.");
      }
    }
  }

  function onAdbLog(event) {
    sessionHasActivity = true;
    log(event.line);
    const status=document.querySelector('.run-event');if(status&&!status.closest('.progress08'))status.textContent=event.line;
    const usbStatus=document.querySelector('.usb-operation-detail');if(usbStatus)usbStatus.textContent=event.line;
    const operationStatus=document.querySelector('.flow-operation-detail');if(operationStatus&&!operationStatus.closest('.flow-action-card'))operationStatus.textContent=event.line;
  }

  function setUsbStatus(connected, text) {
    usbConnected = connected;
    usbStatusEl.textContent = text.replace(/^Флешка:\s*/, "");
    usbStatusEl.classList.toggle("connected", connected);
    usbConnectBtn.replaceChildren(usbStageIcon(connected ? "refresh" : "usb"), el("span", { text: connected ? "Переподключить" : "Подключить" }));
    usbConnectBtn.setAttribute("aria-label", connected ? "Переподключить флешку" : "Подключить флешку");
    usbConnectBtn.title = connected ? "Переподключить флешку" : "Подключить флешку";
    usbConnectBtn.disabled = !!usbOperation;
    usbFormatBtn.disabled = !connected || !!usbOperation;
    const page = document.querySelector(".usb-stage");
    if (page) page.dataset.connected = String(connected);
  }

  function onUsbConnect() {
    if (labInstallBusy) return;
    setUsbStatus(false, "Флешка: подключаюсь...");
    usbConnectBtn.disabled = true;
    openStageRun({
      title: "Подключение флешки", icon: "usb", tag: "usb-connect",
      detail: "Ищем накопитель и читаем его файловую систему…",
      retry: onUsbConnect,
    });
    try { Bridge.call("usb_connect", {}); }
    catch (error) {
      const message = error.message || String(error);
      setUsbStatus(false, `Не удалось подключиться: ${message}`);
      finishRun({ success: false, message }, () => {}, "usb-connect");
    }
  }

  function onUsbConnectResult(event) {
    const r = event.result || {};
    if (r.mounted) {
      const bytes = Number(r.capacity || 0);
      const capacity = bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} ГБ` : `${Math.round(bytes / 1024 ** 2)} МБ`;
      setUsbStatus(true, `${r.label || "Без метки"} · ${capacity}`);
      log("Флешка смонтирована.");
      finishRun({ success: true, message: `Флешка подключена: ${r.label || "без метки"} · ${capacity}.` }, () => {}, "usb-connect");
    } else {
      setUsbStatus(false, "Флешка: не подключена");
      log(`Флешка: не удалось подключиться — ${r.reason || "?"}`);
      finishRun({ success: false, message: r.reason || "Не удалось подключить флешку." }, () => {}, "usb-connect");
    }
  }

  function onUsbFormat() {
    if (!usbConnected) { log("Сначала подключи флешку."); return; }
    confirmDialog(
      "Форматировать флешку?",
      "Все данные на флешке будут БЕЗВОЗВРАТНО удалены. Продолжить?",
      () => {
        log("Форматирую флешку...");
        openStageRun({
          title: "Форматирование флешки", icon: "format", tag: "usb-format",
          detail: "Не отключайте флешку. Это может занять около минуты.",
        });
        try { Bridge.call("usb_format", { label: "MAGICSQD" }); }
        catch (error) {
          const message = error.message || String(error);
          log(`Ошибка форматирования: ${message}`);
          finishRun({ success: false, message }, () => {}, "usb-format");
        }
      }
    );
  }

  function onUsbFormatResult(event) {
    const r = event.result || {};
    log(r.success ? "Флешка отформатирована." : `Ошибка форматирования: ${r.reason || "?"}`);
    finishRun({
      success: !!r.success,
      message: r.success ? "Флешка отформатирована в FAT32 и готова к записи." : (r.reason || "Не удалось отформатировать флешку."),
    }, () => {}, "usb-format");
  }

  // Минимальный confirm-диалог (см. onAdbAskInput — та же причина: нативные
  // confirm()/alert() не работают в этом WebView без кастомного
  // WebChromeClient, да и в принципе тут не используются).
  function confirmDialog(title, text, onConfirm) {
    const overlay = el("div", { class: "modal-overlay" });
    const close = () => overlay.remove();
    const box = el("div", { class: "modal-box" }, [
      el("p", { class: "stage-text", text: title }),
      el("p", { class: "stage-text", style: "color: var(--text-dim)", text }),
      el("div", { style: "display: flex; gap: 8px" }, [
        el("button", { text: "Отмена", onclick: close }),
        el("button", { class: "danger", text: "Да, форматировать", onclick: () => { close(); onConfirm(); } }),
      ]),
    ]);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
  }

  let labInstallBusy=false;
  function onAdbStageResult(event) {
    if (usbOperation && (usbOperation.kind !== "files" || usbOperation.index !== event.index)) return;
    if (stageOperation && stageOperation.index !== event.index) return;
    if (appInstallOperation && appInstallOperation.index !== event.index) return;
    labInstallBusy=false;
    const r = event.result || {};
    if (appInstallOperation) {
      appInstallResults[event.index] = { ...r, cancelled: !r.success && Boolean(r.cancelled || r.canceled || appInstallOperation.cancelRequested) };
      appInstallOperation = null;
    }
    screenWizard.classList.remove('is-installing-apps');
    if (stageOperation) {
      stageExecutionResults[event.index] = { ...r, actionIndex: stageOperation.actionIndex };
      stageOperation = null;
    }
    log(r.success ? "Этап выполнен успешно." : `Этап завершился с ошибкой: ${r.reason || "?"}`);
    const finishedStage = stages.find(item => item.index === event.index);
    // Перерисовываем текущий этап заново (сбрасывает задизейбленные во время
    // выполнения кнопки) и продвигаем мастер — но только когда техник закроет
    // окно с итогом (см. finishRun).
    const afterClose = () => {
      if (stages[currentIndex] && stages[currentIndex].index === event.index) {
        const stage=stages[currentIndex];
        if (stage.type === "usb") {
          usbStageResults[stage.index] = r;
          usbOperation = null;
        }
        if(r.success&&stage.type==='apps'){
          advanceAfter(currentIndex);
          if(stage.next==null){
            document.querySelector('.stage-primary-actions')?.remove();clear(wizardContentEl);
            wizardContentEl.append(el('div',{class:'stage-page'},[el('h2',{text:'Установка завершена'}),el('p',{class:'stage-text',text:'Все выбранные приложения установлены.'})]));
            wizardNextBtn.style.display='';wizardNextBtn.textContent='К моделям';nextAction=()=>showScreen('picker');
          }
        }else render();
        if(!r.success && !["apps", "usb", "adb", "actions", "telnet"].includes(stage.type))showLabNotice('Не удалось завершить этап',r.reason||'Откройте лог для подробностей.',true);
      }
    };
    finishRun({
      success: !!r.success,
      cancelled: !!(r.cancelled || r.canceled),
      message: r.success
        ? (finishedStage?.type === "apps" ? "Все выбранные приложения установлены." : "")
        : (r.reason || ""),
    }, afterClose, "stage");
  }

  function showLabNotice(title,message,withLog=false){
    let overlay;const close=()=>overlay.remove();
    const content=[el('h2',{text:title})];
    if(withLog){content.push(el('p',{text:'Проверьте подключение устройства.'}));content.push(el('details',{},[el('summary',{text:'Подробности'}),el('p',{text:message})]));}
    else content.push(el('p',{text:message}));
    const actions=el('div',{class:'modal-actions'});
    actions.append(el('button',{text:withLog?'Открыть лог':'Понятно',onclick:()=>{close();if(withLog)setLogOpen(true);}}));
    if(withLog)actions.append(el('button',{class:'accent',text:'Повторить',onclick:()=>{close();document.querySelector('.apps-install-start,.stage-primary-actions>.accent')?.click();}}));
    content.push(actions);overlay=showModal(content);
  }

  function showLabBusyNotice() {
    if (usbOperation) showLabNotice("Флешка занята", "Дождитесь завершения текущей операции и не отключайте накопитель.");
    else if (stageOperation) showLabNotice("Операция выполняется", "Дождитесь ответа магнитолы. Подробности выполнения доступны в логе.");
    else showLabNotice("Идёт установка", "Дождитесь завершения текущего приложения или остановите очередь.");
  }

  // Android WebView без кастомного WebChromeClient не поддерживает
  // window.prompt/alert/confirm (тихо не срабатывают) — да и в принципе в
  // этом интерфейсе нативные диалоги не используются, свой оверлей.
  // Общий helper — используется и для "#ask" из adb-этапов, и для ввода
  // IP/хоста перед Wi-Fi ADB/telnet-этапами.
  function promptText(title, defaultValue, onSubmit) {
    const overlay = el("div", { class: "modal-overlay" });
    const input = el("input", { type: "text" });
    input.value = defaultValue || "";
    const submit = () => {
      overlay.remove();
      onSubmit(input.value || "");
    };
    const box = el("div", { class: "modal-box" }, [
      el("p", { class: "stage-text", text: title }),
      input,
      el("button", { class: "accent", text: "OK", onclick: submit }),
    ]);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    input.focus();
  }

  // Скан локальной подсети на открытый port (см. NetworkScan.kt, портовый
  // эквивалент cars/_shared/wifi_adb.py:scan_for_adb_hosts) — используется
  // и перед Wi-Fi ADB (port 5555/из _wizard_spec.json), и перед telnet
  // (port 23). Ручной ввод адреса всегда доступен рядом, независимо от
  // результатов скана — так же, как и на desktop.
  function onNetworkScanResult(event) {
    if (!pendingScanCallback) return;
    const cb = pendingScanCallback;
    pendingScanCallback = null;
    cb(event.hosts || [], event.recommended || null);
  }

  // Ответ на scan_adb_service (см. WebBridge.kt/MdnsResolve.kt) — готовые
  // host:port "Беспроводной отладки" (порт у неё динамический, угадать
  // нельзя, только узнать через mDNS-анонс самого устройства).
  function onAdbServiceScanResult(event) {
    if (!pendingAdbServiceScanCallback) return;
    const cb = pendingAdbServiceScanCallback;
    pendingAdbServiceScanCallback = null;
    cb(event.endpoints || []);
  }

  function onActionsPackagesResult(event) {
    if (!pendingPackagesCallback) return;
    const cb = pendingPackagesCallback;
    pendingPackagesCallback = null;
    cb(event.packages || []);
  }

  // Результат "Добавить свой APK..." (см. renderApkTree, WebBridge.kt:
  // onApksPicked) — файлы уже скопированы в приватное хранилище приложения,
  // event.apks — готовые {path, name}, как car_pick_files на desktop.
  // Прямой обработчик, а не pending-callback (как у onNetworkScanResult/
  // onActionsPackagesResult) — тут нет конкретного запроса, ждущего именно
  // этого ответа, просто добавляем в общий список и выбираем сразу.
  function onPersonalApksPicked(event) {
    const apks = event.apks || [];
    const draft = pendingPersonalPicker;
    pendingPersonalPicker = null;
    if (!draft) return;
    if (!apks.length) { log("Не выбрано ни одного файла."); return; }
    for (const apk of apks) {
      if (!personalApks.some((a) => a.path === apk.path)) personalApks.push(apk);
      const activeEditor = appPickerSession || inlineAppsSession;
      if (activeEditor && !activeEditor.personal.some(a => a.path === apk.path)) activeEditor.personal.push(apk);
      if (draft && !labInstallBusy && (appPickerSession === draft || inlineAppsSession === draft && draft.page.isConnected)) {
        if (!draft.personal.some(a => a.path === apk.path)) draft.personal.push(apk);
        draft.selected.add(apk.path);
      }
    }
    if (appPickerSession) appPickerSession.render();
    else if (!labInstallBusy && inlineAppsSession?.page.isConnected) inlineAppsSession.render();
    else if (!labInstallBusy) render();
  }

  // Модалка выбора установленного приложения — для actions-этапов kind
  // grant_permissions/mock_location (см. renderActionsStage), тот же ask_choice,
  // что и на desktop (app/adb_permissions.py). Список может быть длинным
  // (все сторонние APK на магнитоле), поэтому с фильтром по подстроке имени
  // пакета — тот же приём, что и у promptHostPicker выше со списком хостов.
  function promptPackagePicker(title, packages, onSubmit) {
    const overlay = el("div", { class: "modal-overlay dismissible" });
    overlay.addEventListener("click", (e) => { if (e.target === overlay) closeDismissibleModal(); });
    const listWrap = el("div", { class: "host-scan-list" });
    const filterInput = el("input", { type: "text", placeholder: "Фильтр по имени пакета" });
    const submit = (pkg) => { overlay.remove(); onSubmit(pkg); };
    function renderList() {
      clear(listWrap);
      const f = filterInput.value.trim().toLowerCase();
      const filtered = f ? packages.filter((p) => p.toLowerCase().includes(f)) : packages;
      if (!filtered.length) {
        listWrap.appendChild(el("p", { class: "stage-text", style: "color: var(--text-dim)", text: "Ничего не найдено." }));
        return;
      }
      filtered.forEach((pkg) => {
        const btn = el("button", { text: pkg });
        btn.addEventListener("click", () => submit(pkg));
        listWrap.appendChild(btn);
      });
    }
    filterInput.addEventListener("input", renderList);
    renderList();
    const box = el("div", { class: "modal-box" }, [
      el("p", { class: "stage-text", text: title }),
      filterInput,
      listWrap,
    ]);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    filterInput.focus();
  }

  // Закрываемая модалка (тап мимо карточки/системный "назад" — см.
  // window.__handleBackPress) — в отличие от promptText ("#ask" из
  // running-этапа, где Kotlin-сторона реально блокирующе ждёт ответ в
  // SynchronousQueue, закрывать её без ответа нельзя).
  function closeDismissibleModal() {
    const overlay = document.querySelector(".modal-overlay.dismissible");
    if (!overlay) return false;
    pendingScanCallback = null;
    pendingAdbServiceScanCallback = null;
    overlay.remove();
    return true;
  }

  // opts.editablePort — показать поле порта рядом со сканом (по умолчанию
  // то, что задано в _wizard_spec.json редактором на desktop, см. onAdbConnect)
  // с возможностью поменять "на всякий случай" и пересканировать по новому
  // порту, не закрывая модалку. opts.discoverAdbService — дополнительно
  // искать актуальный порт "Беспроводной отладки" по mDNS (см. onAdbConnect,
  // MdnsResolve.resolveAdbTlsConnectEndpoints) — только для Wi-Fi ADB, не
  // для telnet (там фиксированный порт 23, mDNS-служба другая). onSubmit(host,
  // port) — port тот, что был актуален на момент выбора адреса (свой у
  // найденной через mDNS службы, иначе — изменённый техником или дефолтный).
  function promptHostPicker(title, port, onSubmit, opts) {
    opts=opts||{};
    let cancelScan=null;
    LabUI.connection({title:port===23?'Подключение к магнитоле':'Подключение по Wi-Fi',port:port||5555,host:lastWifiHost||'',
      scan:p=>new Promise(resolve=>{
        let hosts=[],services=[],waiting=opts.discoverAdbService?2:1;
        const finish=()=>{if(--waiting===0){cancelScan=null;resolve([...services,...hosts.filter(h=>!services.some(s=>s.host===h))]);}};
        cancelScan=()=>resolve([]);
        pendingScanCallback=(found,recommended)=>{pendingScanCallback=null;hosts=[...new Set([recommended,...found].filter(Boolean))];finish();};
        if(opts.discoverAdbService){pendingAdbServiceScanCallback=endpoints=>{pendingAdbServiceScanCallback=null;services=endpoints;finish();};Bridge.call('scan_adb_service',{});}
        Bridge.call('scan_hosts',{port:p});
      }),
      connect:(host,p)=>{onSubmit(host,p);return {ok:true};},
      onClose:()=>{pendingScanCallback=null;pendingAdbServiceScanCallback=null;cancelScan?.();}
    });
  }

  // Системный жест/кнопка "назад" (см. MainActivity.kt: onBackPressedDispatcher
  // зовёт это через evaluateJavascript) — всё состояние в JS, поэтому решение
  // тоже тут: закрыть модалку/лог, если открыты; иначе на шаг назад по
  // мастеру или пикеру; и только если деться больше некуда — реально выйти
  // из приложения (Kotlin делает это по возврату "exit").
  // Best-effort на случай, если техник свернул/закрыл приложение, не
  // долистав мастер до конца и не нажав "Назад" явно (см. MainActivity.kt:
  // onStop) — WebView в этот момент ещё жив, в отличие от полного убийства
  // процесса системой, которое поймать вообще нечем.
  window.__flushInstallLogOnStop = function () {
    flushSessionLog(false);
  };

  window.__handleBackPress = function () {
    if (document.querySelector("dialog[open]")) { document.querySelector("dialog[open]").dispatchEvent(new Event("cancel", {cancelable:true})); const d=document.querySelector("dialog[open]"); if(d && !d.querySelector(".accent:disabled")) d.close(); return "handled"; }
    if (closePhotoLightbox()) return "handled";
    if (closeDismissibleModal()) return "handled";
    if (logOverlayEl.classList.contains("open")) { setLogOpen(false); return "handled"; }
    if (screenWizard.classList.contains("active")) {
      if(labInstallBusy){showLabBusyNotice();return "handled";}
      if (historyStack.length) { goBack(); return "handled"; }
      flushSessionLog(false);
      showScreen("picker");
      return "handled";
    }
    if (selectedGroup) { showGroupStep(selectedBrand); return "handled"; }
    if (selectedBrand) { showBrandStep(); return "handled"; }
    return "exit";
  };

  function onAdbAskInput(event) {
    promptText(event.prompt, "", (value) => {
      Bridge.call("adb_ask_input_response", { requestId: event.requestId, value });
    });
  }

  function filesByNameFrom(paths) {
    const map = {};
    (paths || []).forEach((p) => { map[p.split("/").pop()] = p; });
    return map;
  }

  function basename(p) {
    return p.split("/").pop();
  }

  // Граф исполнения — полностью явный (см. app/car_generator.py: StepSpec.
  // next/next_options, тот же _wizard_spec.json/wizard_spec.py, что и на
  // desktop) — каждый этап хранит id следующего (или, для "check", id на
  // каждый вариант отдельно), никаких переменных/условий здесь больше нет.
  // historyStack — реально пройденный путь, а не "предыдущий индекс по
  // порядку массива" (тот же фикс, что и в desktop stage_wizard.js).
  const historyStack = [];

  function indexById(id) {
    return stages.findIndex((s) => s.id === id);
  }

  function goBack() {
    if(labInstallBusy)return;
    if (!historyStack.length) return;
    show(historyStack.pop());
  }

  // optionIndex — только для стадии типа "check": какой вариант выбрал
  // техник (см. renderCheckStage-эквивалент ниже).
  function advanceAfter(index, optionIndex) {
    const stage = stages[index];
    const nextId = stage.type === "check" ? (stage.next_options || [])[optionIndex] : stage.next;
    if (nextId == null) {
      log("Все этапы установки выполнены.");
      flushSessionLog(true);
      if (!installCompletedShown && stages.length) {
        installCompletedShown = true;
        showCompletionModal();
      }
      renderNav();
      return;
    }
    const nextIndex = indexById(nextId);
    if (nextIndex === -1) {
      renderNav();
      return;
    }
    historyStack.push(index);
    show(nextIndex);
  }

  function show(index) {
    if(labInstallBusy)return;
    currentIndex = index;
    nextAction = () => advanceAfter(currentIndex);
    render();
  }

  // Лайтбокс фото инструкции (см. app/instruction_html.py: LIGHTBOX_SCRIPT)
  // на Android живёт не внутри iframe этапа, а прямо в этом, верхнем
  // документе (см. комментарий про topWin/window.frameElement там же) —
  // потому что у iframe с инструкцией нет своего скролла, и position:fixed
  // внутри него не считает настоящий видимый экран. Обратная сторона: если
  // iframe, который его открыл, потом уничтожается (замена содержимого
  // мастера — см. render() ниже, или системный "назад", см.
  // __handleBackPress) БЕЗ явного закрытия лайтбокса самим тапом/эскейпом —
  // его открывшие обработчики (click/keydown), навешанные ИЗ ТОГО iframe,
  // умирают вместе с его JS-контекстом, и оверлей остаётся видимым, но
  // навсегда не реагирующим ни на что — ровно баг "открыл фото, нажал
  // системное назад, закрыть больше никак". Закрываем его отсюда напрямую
  // (этот код всегда живёт в самом верхнем документе, его контекст не
  // умирает вместе с iframe этапа) при каждой смене содержимого мастера и
  // как первый пункт обработки "назад" — раньше самого лайтбокса тут ничего
  // не знало вообще.
  function closePhotoLightbox() {
    const overlay = document.getElementById("magicsqd-lightbox");
    if (!overlay || !overlay.classList.contains("is-open")) return false;
    overlay.classList.remove("is-open");
    const img = overlay.querySelector("img");
    if (img) { img.removeAttribute("src"); img.style.transform = ""; img.style.opacity = ""; }
    return true;
  }

  function render() {
    closePhotoLightbox();
    inlineAppsSession = null;
    if (!labInstallBusy) screenWizard.classList.remove('is-installing-apps');
    if (adbBarEl.parentElement !== screenWizard) screenWizard.insertBefore(adbBarEl, wizardContentEl);
    clear(wizardContentEl);
    document.querySelector('.stage-primary-actions')?.remove();
    if (!stages.length) {
      wizardContentEl.appendChild(el("p", { class: "stage-text", text: "Для этой модели нет заданных этапов установки." }));
      renderNav();
      return;
    }
    renderStage(stages[currentIndex]);
    renderNav();
  }

  function renderNav() {
    wizardBackBtn.disabled = labInstallBusy || !historyStack.length;
    wizardNextBtn.disabled = labInstallBusy;
    if (!stages.length) {
      wizardNextBtn.style.display = "none";
    } else {
      wizardNextBtn.style.display = "";
      // "check" — у разных вариантов может быть разное продолжение (или
      // вовсе никакого), заранее неизвестно, пока техник не выбрал. Клик
      // по кнопке-варианту продвигает сразу (см. renderStage), а "Далее" —
      // на случай, когда техник и так знает, куда идти, и просто
      // пролистывает мастер (по умолчанию первый вариант).
      const stage = stages[currentIndex];
      const isLast = stage && stage.type !== "check" && stage.next == null;
      wizardNextBtn.textContent = isLast ? "Готово" : "Далее";
    }
    const videoStage = stages.length ? stages[currentIndex] : null;
    const hasVideo = Boolean(videoStage && videoStage.video_url);
    if (hasVideo) {
      wizardVideoBtn.hidden = false;
      wizardVideoBtn.replaceChildren(usbStageIcon('play'), el('span', {text:videoStage.video_label || "Смотреть видео"}));
    } else {
      wizardVideoBtn.hidden = true;
    }
    // На узком экране "Этап X из Y" и кнопка видео вместе не помещаются
    // рядом с "Назад"/"Далее" — кнопка видео нужнее (это действие, а не
    // просто справочный текст), поэтому прячем подпись, пока она активна.
    wizardPageLabel.textContent = (!hasVideo && stages.length) ? `Этап ${currentIndex + 1} из ${stages.length}` : "";
  }

  // Докачивает video_file (если ещё нет на диске) в фоне на Kotlin-стороне
  // (см. WebBridge.kt: ensureVideoDownloaded — может быть до 150 МБ,
  // Bridge.call синхронный и заблокировал бы JS-поток) и по готовности
  // переходит на video_url — тот же appassets.androidplatform.net origin,
  // что и у страницы, WebView штатно проигрывает mp4 при прямой навигации.
  function playStageVideo() {
    const stage = stages[currentIndex];
    if (!stage || !stage.video_url) return;
    wizardVideoBtn.disabled = true;
    wizardVideoBtn.textContent = "Скачиваю...";
    Bridge.call("ensure_video_downloaded", { path: stage.video_file });
  }

  function onVideoReady(event) {
    const stage = stages[currentIndex];
    if (!stage || stage.video_file !== event.path) return;
    wizardVideoBtn.disabled = false;
    window.location.href = stage.video_url;
  }

  function describeCommand(cmd) {
    if (typeof cmd === "string") return cmd;
    switch (cmd.kind) {
      case "sleep": return `#sleep ${cmd.seconds}`;
      case "reboot": return "#reboot";
      case "reboot_nowait": return "#reboot_nowait";
      case "wait_device": return "#wait_device";
      case "ask": return `#ask ${cmd.prompt}`;
      case "root": return "#root";
      case "disable_verity": return "#disable_verity";
      case "remount": return "#remount";
      case "push": return `#push ${cmd.file} -> ${cmd.remote}`;
      case "install": return `#install ${cmd.file}`;
      case "install_stream": return `#install_stream ${cmd.file}`;
      case "shell": return cmd.command;
      default: return "";
    }
  }

  function flowCard(title, description, symbol, state = "idle") {
    const card = el("section", { class: "flow-card", "data-state": state });
    const badge = el("span", { class: "flow-card-symbol", "aria-hidden": "true" }, [usbStageIcon(symbol)]);
    const text = el("div", { class: "flow-card-copy" }, [el("h3", { text: title })]);
    if (description) text.append(el("p", { text: description }));
    card.append(el("header", { class: "flow-card-heading" }, [badge, text]));
    return card;
  }

  function flowTechnicalDetails(card, text, label = "Команды этапа") {
    if (!text) return;
    card.append(el("details", { class: "flow-technical" }, [
      el("summary", { text: label }), el("pre", { text }),
    ]));
  }

  function flowResult(card, result) {
    if (!result) return;
    card.dataset.state = result.success ? "done" : "error";
    const row = el("div", { class: "flow-result", role: "status" }, [
      usbStageIcon(result.success ? "check" : "alert"),
      el("span", { text: result.success ? "Операция выполнена." : result.reason || "Не удалось выполнить операцию." }),
    ]);
    card.append(row);
    if (!result.success) card.append(usbStageButton("Открыть лог", "log", () => setLogOpen(true)));
  }

  function runStageOperation(method, args, page, card, actionIndex = null) {
    if (labInstallBusy) return;
    stageOperation = { index: args.index, actionIndex };
    labInstallBusy = true;
    renderNav();
    page.querySelectorAll("button, input, select").forEach(node => { node.disabled = true; });
    card.dataset.state = "running";
    openStageRun({
      title: actionIndex != null
        ? (card.querySelector("h3")?.textContent || "Выполнение действия")
        : method === "telnet_run_stage" ? "Подключение по сети" : "Выполнение команд на магнитоле",
      stageIndex: args.index, tag: "stage",
      icon: method === "telnet_run_stage" ? "wifi" : "terminal",
      detail: "Выполняем команды. Дождитесь ответа магнитолы…",
      // Повтор после закрытия окна: страница к этому моменту уже перерисована,
      // поэтому карточку ищем заново.
      retry: () => {
        const freshPage = document.querySelector(".flow-stage");
        const freshCard = actionIndex == null
          ? freshPage?.querySelector(".flow-card")
          : freshPage?.querySelectorAll(".flow-action-card")[actionIndex];
        if (freshPage && freshCard) runStageOperation(method, args, freshPage, freshCard, actionIndex);
      },
    });
    try { Bridge.call(method, args); }
    catch (error) { onAdbStageResult({ index: args.index, result: { success: false, reason: error.message || String(error) } }); }
  }

  function renderAdbStage(page, stage) {
    page.classList.add("flow-stage");
    const card = flowCard("Команды ADB", "Выполнение на подключённой магнитоле.", "terminal");
    const commandsText = (stage.commands || []).map(describeCommand).filter(Boolean).join("\n");
    flowTechnicalDetails(card, commandsText, `Команды этапа · ${(stage.commands || []).length}`);
    const btn = usbStageButton("Выполнить", "play", () => {
      if (!adbConnected) { showLabNotice("Нет подключения","Подключите магнитолу к ADB с помощью кнопки над этапом."); return; }
      runStageOperation("adb_run_stage", {
        index: stage.index,
        commands: stage.commands || [],
        filesByName: filesByNameFrom(stage.adb_files),
      }, page, card);
    }, true);
    card.append(btn);
    flowResult(card, stageExecutionResults[stage.index]);
    page.append(card);
  }

  function renderActionsStage(page, stage) {
    page.classList.add("flow-stage");
    (stage.actions || []).forEach((action, actionIndex) => {
      const supported = !action.kind || ["command", "grant_permissions", "mock_location"].includes(action.kind);
      const card = flowCard(action.label || action.kind || "Действие", supported ? "" : "Доступно только в версии для Windows.", action.kind === "grant_permissions" ? "shield" : action.kind === "mock_location" ? "location" : "terminal");
      card.classList.add('flow-action-card');
      const btn = usbStageButton("Выполнить", "play", () => {}, supported);
      btn.disabled = !supported;
      btn.addEventListener("click", () => {
        if (labInstallBusy) return;
        // grant_permissions/mock_location — техник выбирает установленное
        // приложение (ask_choice на desktop), дальше AdbPermissions.kt (см.
        // WebBridge.kt: actionsGrantPermissions/actionsMockLocation, портовая
        // копия cars/_shared/adb_permissions.py). disable_app/enable_app пока
        // не портированы — остаются с явным "не поддерживается" ниже, вместо
        // того чтобы молча выполнить 0 команд как "успех".
        if (action.kind === "grant_permissions" || action.kind === "mock_location") {
          if (!adbConnected) { showLabNotice("Нет подключения","Подключите магнитолу к ADB с помощью кнопки над этапом."); return; }
          log("Получаю список приложений...");
          card.querySelector('.flow-action-note')?.remove();
          const note=el('p',{class:'flow-action-note',role:'status',text:'Получаем список приложений…'});
          card.append(note);btn.disabled=true;
          pendingPackagesCallback = (packages) => {
            btn.disabled=false;
            if (!page.isConnected || labInstallBusy) return;
            if (!packages.length) { note.textContent='Не удалось получить список приложений.';log(note.textContent);return; }
            note.textContent='Выберите приложение.';
            promptPackagePicker("Выберите приложение", packages, (pkg) => {
              if (!page.isConnected || labInstallBusy) return;
              try {
                if (action.kind === "grant_permissions") {
                  log(`Выдаю разрешения: ${pkg}`);
                  Bridge.call("actions_grant_permissions", { pkg });
                } else {
                  log(`Приложение для фиктивных местоположений: ${pkg}`);
                  Bridge.call("actions_mock_location", { pkg });
                }
                note.textContent='Запрос отправлен. Результат появится в логе.';
              } catch(error) {note.textContent=error.message||'Не удалось выполнить действие.';log(note.textContent);}
            });
          };
          try { Bridge.call("actions_list_packages", { thirdPartyOnly: true }); }
          catch(error){pendingPackagesCallback=null;btn.disabled=false;note.textContent=error.message||'Не удалось получить список приложений.';log(note.textContent);}
          return;
        }
        if (action.kind && action.kind !== "command") {
          log(`Действие "${action.label}" (${action.kind}) пока не поддерживается в мобильной версии.`);
          return;
        }
        if (!adbConnected) { showLabNotice("Нет подключения","Подключите магнитолу к ADB с помощью кнопки над этапом."); return; }
        log(`Выполняю действие: ${action.label}`);
        runStageOperation("adb_run_stage", {
          index: stage.index, commands: action.commands || [],
          filesByName: filesByNameFrom(action.files),
        }, page, card, actionIndex);
      });
      card.append(btn);
      const result = stageExecutionResults[stage.index];
      if (result?.actionIndex === actionIndex) flowResult(card, result);
      page.append(card);
    });
    if (!(stage.actions || []).length) page.append(flowCard("Действия не заданы", "Для этого этапа пока нет доступных действий.", "settings"));
  }

  // Общая структура для "apps" и "usb"-этапов (см. renderApkTree ниже) —
  // выбор варианта живёт в appsSelection[stage.index], те же поля что и
  // раньше (variant/optional), просто теперь используется обоими рендерами.
  function selectionFor(stage) {
    let sel = appsSelection[stage.index];
    if (!sel) {
      sel = { variant: (stage.variants && stage.variants[0] && stage.variants[0].name) || null, optional: new Set() };
      appsSelection[stage.index] = sel;
    }
    return sel;
  }

  function renderVariantPicker(page, stage, sel) {
    if (stage.variants && stage.variants.length > 1) {
      const variantSelect = el("select", {}, stage.variants.map((v) => el("option", { value: v.name, text: v.name })));
      variantSelect.value = sel.variant;
      variantSelect.addEventListener("change", () => { sel.variant = variantSelect.value; render(); });
      page.appendChild(variantSelect);
    }
  }

  /**
   * Три секции — обязательные/необязательные приложения САМОЙ модели (из
   * _wizard_spec.json, см. wizard_spec.py) + "Дополнительные приложения" —
   * общая библиотека apk/ (см. apkLibrary/python/apk_library.py), сгруппированная
   * по категориям, показывается на "apps"-этапах и на "usb"-этапах с
   * usb_copy_selected_apks (аналог desktop stage_wizard.js:buildAppsTree —
   * там это тоже одна и та же функция для обоих типов этапа). Выбор
   * необязательных/общих пишется в globalSelectedApks — общий на весь мастер
   * список (аналог desktop ctx.selected_apks), из него usb-этап потом берёт
   * usb_copy_selected_apks, а apps-этап — то, что реально ставить.
   */
  function stageApkLists(stage, sel = selectionFor(stage)) {
    const source = stage.variants?.length ? stage.variants.find(v => v.name === sel.variant) || stage.variants[0] : stage;
    const entries = values => (values || []).map(value => typeof value === "string" ? { path: value, name: basename(value) } : value).filter(value => value?.path);
    return { required: entries(source.standard_apks), optional: entries(source.standard_apks_optional) };
  }

  function renderApkTree(page, stage, sel, draft) {
    const rawLists = stageApkLists(stage, sel);
    const selectedApks = draft.selected;
    const requiredPaths = new Set(rawLists.required.map(apk => apk.path));
    // standard_apks/standard_apks_optional теперь {path,name,description}
    // (см. wizard_spec.py: _apk_entry — раньше "красивое" имя из редактора
    // терялось и техник видел голое имя файла) — остальной код (установка,
    // globalSelectedApks) по-прежнему работает с путями строками, поэтому
    // наружу отдаём только их, а name/description используем тут же для
    // самого рендера строк ниже.
    const lists = { required: rawLists.required.map((a) => a.path),
                     optional: rawLists.optional.map((a) => a.path) };

    function buildAppRow(apk, required, checked, onChange) {
      required = required || requiredPaths.has(apk.path);
      const row = el("li", { class: "app-choice", "data-apk-path":apk.path });
      const checkbox = el("input", { type: "checkbox" });
      checkbox.checked = required || checked;
      if (required) checkbox.disabled = true;
      if (onChange && !required) checkbox.addEventListener("change", () => { onChange(checkbox.checked); draft.updateCount(); });
      row.append(LabUI.appIcon(apk.path,apk.icon), checkbox, el("span", { class: "app-choice-name", text: apk.name || basename(apk.path) }));
      row.addEventListener("click", (event) => {
        if (checkbox.disabled || event.target.closest("input, button, a") || window.getSelection().toString()) return;
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event("change", { bubbles: true }));
      });
      if (apk.remote_only) row.appendChild(el("span", { class: "app-download-mark", text: "Будет скачано" }));
      if (apk.description) {
        const description = el("div", { class: "apk-desc", text: apk.description, id: `apk-desc-${Math.random().toString(36).slice(2)}` });
        const info = el("button", { class: "apk-info-btn", type: "button", text: "i", "aria-label": "Описание приложения", "aria-expanded": "false" });
        info.addEventListener("click", () => {
          const open = row.classList.toggle("description-open");
          info.setAttribute("aria-expanded", String(open));
        });
        row.append(info, description);
      }
      return row;
    }

    function appendSection(title, kind, apks, required, selected) {
      if (!apks.length) return;
      const section = el(required ? "details" : "section", { class: `apps-section apps-section-${kind}` });
      section.appendChild(el(required ? "summary" : "h3", { class: "apps-section-title", text: required ? `${title} · ${apks.length}` : title }));
      const list = el("ul", { class: "stage-apps-list stage-apps-grid" });
      apks.forEach((apk) => {
        list.appendChild(buildAppRow(
          apk,
          required,
          required || selected.has(apk.path),
          (checked) => {
            if (checked) { selected.add(apk.path); selectedApks.add(apk.path); }
            else { selected.delete(apk.path); selectedApks.delete(apk.path); }
          },
        ));
      });
      section.appendChild(list);
      page.appendChild(section);
    }

    // Свои APK — техник сам выбирает файл(ы) на телефоне, минуя общую
    // библиотеку apk/ (та наполняется только администратором) — портовый
    // эквивалент desktop stage_wizard.js:pickPersonalApks. Кнопка всегда
    // видна сверху, вне остальных секций — так же, как на desktop.
    page.appendChild(el("button", {
      class: "app-personal-apk-add", type: "button", text: "Добавить свой APK...",
      onclick: () => {
        if (labInstallBusy || pendingPersonalPicker) return;
        log("Выберите файл...");
        pendingPersonalPicker = draft;
        try { Bridge.call("pick_personal_apks", {}); }
        catch(error){pendingPersonalPicker=null;showLabNotice('Не удалось выбрать файл',error.message||String(error));}
      },
    }));
    if (draft.personal.length) {
      const section = el("section", { class: "apps-section apps-section-personal" });
      section.appendChild(el("h3", { class: "apps-section-title", text: "Свои APK" }));
      const list = el("ul", { class: "stage-apps-list stage-apps-grid" });
      draft.personal.forEach((apk) => {
        const row = buildAppRow(apk, false, selectedApks.has(apk.path), (checked) => {
          if (checked) selectedApks.add(apk.path);
          else selectedApks.delete(apk.path);
        });
        const removeBtn = el("button", { type: "button", class: "app-personal-apk-remove", text: "Убрать" });
        removeBtn.addEventListener("click", () => {
          draft.personal = draft.personal.filter((a) => a.path !== apk.path);
          selectedApks.delete(apk.path);
          draft.render();
        });
        row.appendChild(removeBtn);
        list.appendChild(row);
      });
      section.appendChild(list);
      page.appendChild(section);
    }

    const represented = new Set([...rawLists.required, ...rawLists.optional, ...apkLibrary, ...draft.personal].map(apk => apk.path));
    const previous = selectedAppsForStage(stage, draft.previousPaths).entries.filter(apk => !represented.has(apk.path));
    appendSection("Из других этапов", "previous", previous, false, selectedApks);
    appendSection("Обязательные приложения", "required", rawLists.required, true, new Set());
    appendSection("Дополнительно", "optional", rawLists.optional, false, selectedApks);

    const showGeneralLibrary = stage.type === "apps" || (stage.type === "usb" && stage.usb_copy_selected_apks);
    if (showGeneralLibrary) {
      const library=el('details',{class:'apps-library'});library.append(el('summary',{text:'Библиотека приложений'}));page.append(library);
      if (!apkLibrary.length) {
        library.appendChild(el("p", { class: "stage-text", style: "color: var(--text-dim)", text: apkLibraryLoaded ? "Библиотека пока пуста." : "Загружаем список приложений…" }));
      } else {
        const byCategory = {};
        apkLibrary.forEach((a) => { (byCategory[a.category || ""] = byCategory[a.category || ""] || []).push(a); });
        Object.keys(byCategory).sort((a, b) => a.localeCompare(b)).forEach((cat) => {
          const details = el("details", { class: "apk-category apps-section apps-section-library" });
          details.appendChild(el("summary", { text: cat || "Без категории" }));
          const ul = el("ul", { class: "stage-apps-list stage-apps-grid" });
          byCategory[cat].forEach((apk) => {
            ul.appendChild(buildAppRow(apk, false, selectedApks.has(apk.path), (checked) => {
              if (checked) selectedApks.add(apk.path);
              else selectedApks.delete(apk.path);
            }));
          });
          details.appendChild(ul);
          library.appendChild(details);
        });
      }
    }

    return lists;
  }

  function selectedAppsForStage(stage, selected = globalSelectedApks) {
    const lists = stageApkLists(stage);
    const required = stage.type === "apps" ? lists.required : [];
    const byPath = new Map();
    for (const candidate of stages) {
      for (const source of [candidate, ...(candidate.variants || [])]) {
        for (const entry of [...(source.standard_apks || []), ...(source.standard_apks_optional || [])]) {
          const item = typeof entry === "string" ? { path: entry, name: basename(entry) } : entry;
          if (item?.path) byPath.set(item.path, item);
        }
      }
    }
    for (const item of [...apkLibrary, ...personalApks, ...lists.optional, ...required]) byPath.set(item.path, item);
    const paths = [...new Set([...required.map(item => item.path), ...selected])];
    return { required, entries: paths.map(path => byPath.get(path) || { path, name: basename(path) }) };
  }

  function applyAppSelection(stage, draft) {
    globalSelectedApks = new Set(draft.selected);
    personalApks = draft.personal.slice();
    for (const stored of Object.values(appsSelection)) stored.optional = new Set([...stored.optional].filter(path => globalSelectedApks.has(path)));
    const sel = selectionFor(stage);
    sel.optional = new Set(stageApkLists(stage, sel).optional.filter(apk => globalSelectedApks.has(apk.path)).map(apk => apk.path));
  }

  function filterAppRows(body, search, empty) {
    const query = search.value.trim().toLocaleLowerCase('ru-RU');
    body.querySelectorAll('.app-choice').forEach(row => { row.hidden = query !== '' && !row.textContent.toLocaleLowerCase('ru-RU').includes(query); });
    [...body.querySelectorAll('.apps-section,.apps-library')].reverse().forEach(section => {
      const rows = [...section.querySelectorAll('.app-choice')];
      section.hidden = rows.length > 0 && rows.every(row => row.hidden);
      if (query && section.tagName === 'DETAILS') section.open = true;
    });
    empty.hidden = query === '' || [...body.querySelectorAll('.app-choice')].some(row => !row.hidden);
  }

  function appendInlineAppSelection(parent, stage, page) {
    const sel = selectionFor(stage);
    const draft = { stage, page, selected: new Set(globalSelectedApks), previousPaths: new Set(globalSelectedApks), personal: personalApks.slice(), variant: sel.variant, expanded: new Map() };
    const required = new Set(stageApkLists(stage, sel).required.map(apk => apk.path));
    const count = el('p', { class:'apps-inline-count', role:'status' });
    const search = el('input', {type:'search', class:'apps-picker-search', placeholder:'Поиск приложений', 'aria-label':'Поиск приложений', autocomplete:'off'});
    const body = el('div', {class:'apps-picker-body apps-inline-body'});
    const empty = el('p', {class:'apps-picker-empty', text:'Ничего не найдено. Попробуйте другое название.', hidden:''});
    draft.updateCount = () => {
      if (labInstallBusy) return;
      applyAppSelection(stage, draft);
      count.textContent = `Выбрано: ${new Set([...required,...draft.selected]).size}`;
      body.querySelectorAll('.app-choice').forEach(row => {
        const input = row.querySelector('input');
        input.checked = required.has(row.dataset.apkPath) || draft.selected.has(row.dataset.apkPath);
      });
    };
    draft.render = () => {
      if (labInstallBusy) return;
      const scroll = wizardContentEl.scrollTop;
      body.querySelectorAll('details').forEach(node => draft.expanded.set(node.querySelector('summary')?.textContent,node.open));
      clear(body);
      renderApkTree(body, stage, sel, draft);
      body.querySelectorAll('details').forEach(node => { node.open = draft.expanded.get(node.querySelector('summary')?.textContent) ?? !node.classList.contains('apps-section-required'); });
      body.append(empty);
      draft.updateCount();filterAppRows(body,search,empty);
      wizardContentEl.scrollTop = scroll;
    };
    search.addEventListener('input',()=>filterAppRows(body,search,empty));
    parent.append(el('div',{class:'apps-inline-heading'},[count]),el('div',{class:'apps-picker-search-wrap apps-inline-search'},[search]),body);
    inlineAppsSession = draft;
    draft.render();
  }

  function openAppSelection(stage) {
    if (labInstallBusy || appPickerSession) return;
    const sel = selectionFor(stage);
    const draft = { stage, selected: new Set(globalSelectedApks), previousPaths: new Set(globalSelectedApks), personal: personalApks.slice(), variant: sel.variant, expanded: new Map() };
    const required = new Set(stageApkLists(stage, sel).required.map(apk => apk.path));
    const dialog = el("dialog", { class: "apps-picker-dialog", "aria-labelledby": "apps-picker-title" });
    const count = el("p", { class: "apps-picker-count", role: "status" });
    const closeButton = usbStageButton("Закрыть", "close", () => dialog.close());
    closeButton.classList.add("apps-picker-close");
    closeButton.setAttribute("aria-label", "Закрыть выбор приложений");
    const heading = el("header", { class: "apps-picker-heading" }, [
      el("div", {}, [el("h2", { id: "apps-picker-title", text: "Выбор приложений" }), count]), closeButton,
    ]);
    const search = el("input", { type: "search", class: "apps-picker-search", placeholder: "Поиск приложений", "aria-label": "Поиск приложений", autocomplete: "off" });
    const body = el("div", { class: "apps-picker-body" });
    const empty = el("p", { class: "apps-picker-empty", text: "Ничего не найдено. Попробуйте другое название.", hidden: "" });
    const cancel = el("button", { type: "button", text: "Отмена", onclick: () => dialog.close() });
    const apply = el("button", { type: "button", class: "accent", text: "Готово" });
    const scrollTop = wizardContentEl.scrollTop;

    draft.updateCount = () => {
      const total = new Set([...required, ...draft.selected]).size;
      count.textContent = required.size ? `Выбрано: ${total} · обязательных: ${required.size}` : `Выбрано: ${total}`;
      body.querySelectorAll(".app-choice").forEach(row => {
        const input = row.querySelector("input");
        input.checked = required.has(row.dataset.apkPath) || draft.selected.has(row.dataset.apkPath);
      });
    };
    function filterRows() {
      filterAppRows(body, search, empty);
    }
    draft.render = () => {
      const scroll = body.scrollTop;
      body.querySelectorAll("details").forEach(node => draft.expanded.set(node.querySelector("summary")?.textContent, node.open));
      clear(body);
      renderApkTree(body, stage, { variant: draft.variant, optional: new Set() }, draft);
      body.querySelectorAll("details").forEach(node => { node.open = draft.expanded.get(node.querySelector("summary")?.textContent) ?? true; });
      body.append(empty);
      draft.updateCount();
      filterRows();
      body.scrollTop = scroll;
    };
    apply.addEventListener("click", () => {
      if (labInstallBusy) return;
      applyAppSelection(stage, draft);
      dialog.close();
    });
    search.addEventListener("input", filterRows);
    dialog.append(heading, el("div", { class: "apps-picker-search-wrap" }, [search]), body, el("footer", { class: "apps-picker-actions" }, [cancel, apply]));
    dialog.addEventListener("click", event => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    });
    dialog.addEventListener("close", () => {
      if (appPickerSession === draft) appPickerSession = null;
      dialog.remove();
      if (!labInstallBusy) {
        render();
        queueMicrotask(() => { wizardContentEl.scrollTop = scrollTop; document.querySelector(".app-select-open")?.focus({ preventScroll: true }); });
      }
    }, { once: true });
    appPickerSession = draft;
    draft.render();
    document.body.append(dialog);
    dialog.showModal();
    closeButton.focus({ preventScroll: true });
  }

  function appendAppSelection(parent, stage) {
    const selection = selectedAppsForStage(stage);
    const card = el("section", { class: "app-selection-card" });
    const copy = el("div", { class: "app-selection-copy" });
    if (stage.type === "usb") copy.append(el("h3", { text: "Приложения" }));
    copy.append(el("p", { class: "app-selection-total", text: selection.entries.length ? `Выбрано: ${selection.entries.length}` : "Выберите приложения для " + (stage.type === "usb" ? "записи" : "установки") }));
    if (selection.required.length) copy.append(el("span", { class: "app-selection-required", text: `Обязательных: ${selection.required.length}` }));
    card.append(copy);
    if (selection.entries.length) {
      const preview = el("div", { class: "app-selection-preview", "aria-label": "Выбранные приложения" });
      selection.entries.slice(0, 4).forEach(apk => {
        const item = el("span", { class: "app-selection-preview-item", title: apk.name || basename(apk.path) }, [LabUI.appIcon(apk.path, apk.icon)]);
        preview.append(item);
      });
      if (selection.entries.length > 4) preview.append(el("span", { class: "app-selection-more", text: `+${selection.entries.length - 4}` }));
      card.append(preview);
    }
    const open = usbStageButton("Выбрать приложения", "apps", () => openAppSelection(stage));
    open.classList.add("app-select-open");
    card.append(open);
    parent.append(card);
  }

  function renderAppsStage(page, stage) {
    const sel = selectionFor(stage);
    renderVariantPicker(page, stage, sel);
    const card = el('section',{class:'apps-inline-card'});
    appendInlineAppSelection(card, stage, page);
    const btn = usbStageButton('Начать установку', 'play', () => {}, true);
    btn.classList.add('apps-install-start');
    btn.addEventListener("click", () => {
      if (labInstallBusy) return;
      if (!adbConnected) { showLabNotice("Нет подключения","Подключите магнитолу к ADB с помощью кнопки над этапом."); return; }
      const selection = selectedAppsForStage(stage);
      const apkPaths = selection.entries.map(apk => apk.path);
      if (!apkPaths.length) { showLabNotice("Выберите приложения","Отметьте приложения, которые нужно установить."); return; }
      btn.disabled = true;
      labInstallBusy=true;
      appInstallOperation={index:stage.index,cancelRequested:false};
      renderNav();
      openStageRun({
        title: 'Установка приложений', stageIndex: stage.index, tag: 'stage',
        items: selection.entries.map(apk=>({name:apk.name||basename(apk.path),path:apk.path})),
        cancellable: true,
        onCancel: () => {
          if (!appInstallOperation || appInstallOperation.cancelRequested) return;
          appInstallOperation.cancelRequested = true;
          try { Bridge.call('adb_cancel_install',{}); }
          catch(error){ appInstallOperation.cancelRequested = false; log(`Не удалось остановить установку: ${error.message||error}`); }
        },
        retry: () => document.querySelector('.apps-install-start')?.click(),
      });
      // Помеченные админом GPS-приложения (флаг mock_location из сайдкара, только
      // общая библиотека): если выбрано ровно ОДНО — после установки ему выдаётся
      // фиктивное местоположение; если несколько — не выдаём никому (какое из них
      // нужно технику, неизвестно).
      const flaggedPaths = apkPaths.filter(p => apkLibrary.some(a => a.path === p && a.mock_location));
      if (flaggedPaths.length > 1) {
        log("Выбрано несколько GPS-приложений с автовыдачей фиктивного местоположения — автоматически оно не выдаётся, выберите приложение вручную на этапе «Доп. действия».");
      }
      try {
        Bridge.call("adb_install_apks", {
          index: stage.index, apkPaths, appsInstallMethod: stage.apps_install_method || "",
          modelKey: model.key, mockLocationPath: flaggedPaths.length === 1 ? flaggedPaths[0] : "",
        });
      } catch(error) { onAdbStageResult({index:stage.index,result:{success:false,reason:error.message||String(error)}}); }
    });
    card.insertBefore(btn, card.querySelector('.apps-inline-search'));
    const result=appInstallResults[stage.index];
    if(result&&!result.success){
      const message=el('div',{class:'apps-install-result',role:'status','data-state':result.cancelled?'cancelled':'error'},[usbStageIcon(result.cancelled?'stop':'report'),el('span',{text:result.cancelled?'Установка остановлена.':result.reason||'Не удалось установить приложения.'})]);
      card.append(message);
      if(!result.cancelled)card.append(usbStageButton('Открыть лог','log',()=>setLogOpen(true)));
    }
    page.append(card);
  }

  function usbStageIcon(name) {
    if (window.AppIcons) return window.AppIcons.icon(name);
    const paths = {
      file: "M7 3h7l5 5v13H7V3Zm7 0v6h5M10 13h6m-6 4h6",
      car: "m5 8 2-5h10l2 5M3 9h18v9H3V9Zm3 9v3m12-3v3M6 12h2m8 0h2M8 15h8",
      key: "M14 3a6 6 0 0 0-5 9L2 19v3h4v-3h3v-3l3-3A6 6 0 1 0 14 3Zm2 4h.01",
      download: "M12 3v12m-5-5 5 5 5-5M4 15v6h16v-6",
      format: "M4 6h16M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7",
      terminal: "M3 4h18v16H3V4Zm4 5 3 3-3 3m6 1h4",
      shield: "m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6l8-4Zm-4 10 3 3 5-6",
      location: "M12 22S4 14 4 9a8 8 0 1 1 16 0c0 5-8 13-8 13Zm0-16a3 3 0 1 0 0 6 3 3 0 0 0 0-6",
      alert: "m12 3 10 18H2L12 3Zm0 6v5m0 3h.01",
    };
    if (!paths[name]) return LabUI.symbol(name);
    const icon = LabUI.icon("apps");
    icon.querySelector("svg").innerHTML = `<path d="${paths[name]}"/>`;
    return icon;
  }

  function usbStageButton(text, symbol, handler, accent = false) {
    const button = el("button", { class: "usb-step-action" + (accent ? " accent" : ""), type: "button" });
    button.append(usbStageIcon(symbol), el("span", { text }));
    button.addEventListener("click", handler);
    return button;
  }

  function usbStepCard(number, title, description, symbol, state = "idle") {
    const card = el("section", { class: "usb-step-card", "data-state": state });
    const heading = el("div", { class: "usb-step-heading" }, [
      el("span", { class: "usb-step-number", text: state === "done" ? "✓" : String(number), "aria-hidden": "true" }),
      usbStageIcon(symbol),
      el("div", { class: "usb-step-copy" }, [el("h3", { text: title }), el("p", { text: description })]),
    ]);
    card.append(heading);
    return card;
  }

  function prepareUsbStage(page, stage) {
    page.classList.add("usb-stage");
    page.dataset.connected = String(usbConnected);
    page.querySelector(".stage-chip").textContent = "Подготовка флешки";
    page.querySelectorAll(":scope > .stage-text").forEach(node => node.remove());
    const intro = el("p", { class: "usb-stage-intro" });
    if (stage.type === "qr_adb") intro.append(
      el("span", { class: "usb-intro-full", text: "Запишите файл на USB-накопитель и выполните дальнейшие шаги на магнитоле." }),
      el("span", { class: "usb-intro-compact", text: "Запишите файл и следуйте шагам ниже." }),
    );
    else intro.textContent = "Подключите USB-накопитель и запишите файлы для вашей магнитолы.";
    page.append(intro);
    if (!usbBarEl.classList.contains("usb-device-card")) {
      usbBarEl.classList.add("usb-device-card");
      const details = el("div", { class: "usb-device-details" }, [el("strong", { text: "USB-накопитель" }), usbStatusEl]);
      const controls = el("div", { class: "usb-device-controls" }, [usbConnectBtn]);
      usbFormatBtn.replaceChildren(usbStageIcon("format"), el("span", { text: "Форматировать флешку" }));
      usbFormatBtn.setAttribute("aria-label", "Форматировать флешку");
      usbFormatBtn.title = "Форматировать флешку";
      usbBarEl.replaceChildren(usbStageIcon("usb"), details, controls);
    }
    usbFormatBtn.disabled = !usbConnected || !!usbOperation;
    usbConnectBtn.disabled = !!usbOperation;
    page.append(usbBarEl);
  }

  function appendUsbOptions(page) {
    page.append(el("details", { class: "usb-device-options" }, [
      el("summary", { text: "Параметры флешки" }),
      el("div", { class: "usb-device-options-body" }, [
        el("p", { text: "Форматирование удалит все данные с накопителя." }), usbFormatBtn,
      ]),
    ]));
  }

  function openUsbInstructions(stage) {
    const overlay = el("div", { class: "modal-overlay dismissible usb-instruction-overlay" });
    const close = () => { closePhotoLightbox(); overlay.remove(); };
    const closeButton = usbStageButton("Закрыть", "close", close);
    const box = el("section", { class: "modal-box usb-instruction-dialog", role: "dialog", "aria-modal": "true", "aria-labelledby": "usb-instruction-title" });
    const title = el("h2", { id: "usb-instruction-title", text: "Шаги на магнитоле" });
    box.append(el("header", { class: "usb-instruction-heading" }, [usbStageIcon("book"), title, closeButton]));
    const content = el("div", { class: "usb-instruction-content" });
    if (stage.description) content.append(el("p", { class: "stage-text", text: stage.description }));
    if (stage.instruction_html) {
      const iframe = el("iframe", {
        class: "usb-instruction-frame", title: "Инструкция для магнитолы",
        sandbox: "allow-scripts allow-popups allow-popups-to-escape-sandbox",
      });
      iframe.srcdoc = LabUI.reader(stage.instruction_html);
      content.append(iframe);
    }
    if (stage.type === "qr_adb") {
      const list = el("ol", { class: "usb-instruction-list" });
      ["Откройте инженерное меню с QR-кодом на магнитоле. Не закрывайте этот экран.",
        "Вставьте в магнитолу ту же флешку, на которую записали файл.",
        "Дождитесь надписи «QNX OK» на экране магнитолы, затем извлеките флешку.",
        "Верните флешку в телефон, нажмите «Переподключить», затем «Получить пароль»."
      ].forEach(text => list.append(el("li", { text })));
      content.append(list);
    }
    if (stage.video_url) content.append(usbStageButton(stage.video_label || "Смотреть видео", "play", () => { close(); playStageVideo(); }));
    box.append(content, el("footer", { class: "dialog-actions" }, [usbStageButton("Понятно", "check", close, true)]));
    overlay.append(box);
    overlay.addEventListener("click", event => { if (event.target === overlay) close(); });
    document.body.append(overlay);
    closeButton.focus({ preventScroll: true });
  }

  function beginUsbOperation(kind, stage, card) {
    if (labInstallBusy) return false;
    if (!usbConnected) {
      usbStatusEl.textContent = "Сначала подключите флешку к телефону";
      usbConnectBtn.focus({ preventScroll: true });
      usbBarEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
      return false;
    }
    usbOperation = { kind, index: stage.index };
    labInstallBusy = true;
    renderNav();
    document.querySelectorAll(".usb-stage button, .usb-stage select, .usb-stage input").forEach(button => { button.disabled = true; });
    card.dataset.state = "running";
    const button = card.querySelector(":scope > .usb-step-action");
    button.querySelector("span:not(.ui-icon)").textContent = kind === "password" ? "Получаем пароль…" : "Записываем…";
    openStageRun({
      title: { files: "Запись файлов на флешку", flag: "Запись файла на флешку", password: "Получение пароля ADB" }[kind] || "Работа с флешкой",
      stageIndex: stage.index, icon: kind === "password" ? "key" : "usb",
      tag: kind === "files" ? "stage" : `qr-${kind}`,
      detail: kind === "password" ? "Читаем сохранённые данные с флешки…" : "Не отключайте флешку до завершения записи.",
      // После закрытия окна страница перерисована — кнопку действия ищем заново.
      retry: () => {
        const cards = [...document.querySelectorAll(".usb-stage > .usb-step-card")];
        (kind === "password" ? cards[2] : cards[0])?.querySelector(":scope > .usb-step-action")?.click();
      },
    });
    return true;
  }

  function sendUsbOperation(method, args, stage) {
    try { Bridge.call(method, args); }
    catch (error) {
      const message = error.message || String(error);
      if (method === "qr_adb_write_flag") onQrAdbWriteResult({ result: { ok: false, error: message } });
      else if (method === "qr_adb_get_password") onQrAdbPasswordResult({ result: { ok: false, error: message } });
      else onAdbStageResult({ index: stage.index, result: { success: false, reason: message } });
    }
  }

  function renderUsbStage(page, stage) {
    prepareUsbStage(page, stage);
    const sel = selectionFor(stage);

    function currentFiles() {
      if (stage.variants && stage.variants.length) {
        const v = stage.variants.find((x) => x.name === sel.variant) || stage.variants[0];
        return v.usb_files || [];
      }
      return stage.usb_files || [];
    }

    const result = usbStageResults[stage.index];
    const writeCard = usbStepCard(1, "Запишите файлы на флешку", "Файлы этапа будут скопированы на подключённый накопитель.", "file", result?.success ? "done" : result ? "error" : "active");
    renderVariantPicker(writeCard, stage, sel);
    const files = currentFiles();
    if (files.length) {
      const list = el("details", { class: "usb-file-list" }, [el("summary", { text: `Файлы для записи · ${files.length}` })]);
      const names = el("ul");
      files.forEach(file => names.append(el("li", { text: basename(file) })));
      list.append(names);
      writeCard.append(list);
    }
    if (stage.usb_copy_selected_apks) {
      appendAppSelection(writeCard, stage);
    }
    if (stage.usb_shared_folder) writeCard.append(el("p", { class: "usb-bundle-note" }, [usbStageIcon("file"), el("span", { text: "Комплект файлов для магнитолы" })]));

    const btn = usbStageButton(result?.success ? "Записать ещё раз" : "Записать файлы", "download", () => {
      if (!beginUsbOperation("files", stage, writeCard)) return;
      sendUsbOperation("usb_run_stage", {
        index: stage.index,
        files,
        sharedFolder: stage.usb_shared_folder || "",
        selectedApks: stage.usb_copy_selected_apks ? Array.from(globalSelectedApks) : [],
        apksDest: stage.usb_apks_dest || "",
      }, stage);
    }, !result?.success);
    writeCard.append(btn);
    if (result) writeCard.append(el("p", { class: "usb-step-feedback", role: "status", text: result.success ? "Файлы записаны. Можно извлечь флешку." : result.reason || "Не удалось записать файлы. Проверьте подключение и повторите запись." }));
    page.append(writeCard);
    const nextInstruction = stages.find(candidate => candidate.id === stage.next && candidate.type === "instruction");
    const instructionStage = stage.description || stage.instruction_html || stage.video_url ? stage : nextInstruction;
    if (instructionStage && (instructionStage.description || instructionStage.instruction_html || instructionStage.video_url)) {
      const instructions = usbStepCard(2, "Выполните шаги на магнитоле", "Следуйте инструкции для выбранной модели автомобиля.", "car");
      instructions.append(usbStageButton("Открыть инструкцию", "book", () => openUsbInstructions(instructionStage)));
      page.append(instructions);
    }
    appendUsbOptions(page);
  }

  // Порт desktop-версии (app/web/frontend/js/screens/stage_wizard.js:
  // renderQrAdbStage) — та же флешка/сессия, что и у "usb"-этапа (см.
  // USB_STAGE_TYPES выше), поэтому свой транспортный бар не нужен, техник
  // подключает её тем же баром сверху. Каждый шаг процедуры — своя
  // карточка (номер + текст + действие сразу под ним, если есть) — тот же
  // приём, что и на desktop, чтобы взгляд не метался между описанием и
  // кнопкой в разных концах экрана.
  function renderQrAdbStage(page, stage) {
    prepareUsbStage(page, stage);
    const writeCard = usbStepCard(1, "Запишите файл на флешку", "Будет создан файл svlog.flag для получения кода ADB.", "file", qrAdbWriteStatus?.ok ? "done" : qrAdbWriteStatus ? "error" : "active");
    const writeBtn = usbStageButton(qrAdbWriteStatus?.ok ? "Записать ещё раз" : "Записать файл", "download", () => {
      if (!beginUsbOperation("flag", stage, writeCard)) return;
      qrAdbResult = null;
      sendUsbOperation("qr_adb_write_flag", {}, stage);
    }, !qrAdbWriteStatus?.ok);
    writeCard.append(writeBtn);
    if (qrAdbWriteStatus) {
      writeCard.append(el("p", {
        class: "usb-step-feedback", role: "status",
        text: qrAdbWriteStatus.ok
          ? "Готово — теперь вставьте эту флешку в магнитолу (шаг 2)."
          : (qrAdbWriteStatus.error || "Не удалось записать файл."),
      }));
    }
    const instructionCard = usbStepCard(2, "Выполните шаги на магнитоле", "Следуйте инструкции на экране автомобиля.", "car", qrAdbWriteStatus?.ok && !qrAdbResult?.ok ? "active" : "idle");
    instructionCard.append(usbStageButton("Открыть инструкцию", "book", () => openUsbInstructions(stage)));
    const passwordCard = usbStepCard(3, "Подключите флешку снова", "После надписи «QNX OK» верните флешку в телефон и получите пароль.", "key", qrAdbResult?.ok ? "done" : qrAdbResult ? "error" : "idle");
    const getBtn = usbStageButton("Получить пароль", "key", () => {
      if (!beginUsbOperation("password", stage, passwordCard)) return;
      sendUsbOperation("qr_adb_get_password", {}, stage);
    });
    passwordCard.append(getBtn);
    if (qrAdbResult) {
      if (qrAdbResult.ok) {
        passwordCard.append(el("div", { class: "usb-password-result", role: "status" }, [
          el("span", { text: "Пароль ADB" }), el("output", { class: "qr-adb-code", text: qrAdbResult.code }),
          el("small", { text: `SN: ${qrAdbResult.sn || "?"}` }),
        ]));
      } else {
        passwordCard.append(el("p", { class: "usb-step-feedback", role: "status", text: qrAdbResult.error || "Не удалось получить пароль." }));
      }
    }
    page.append(writeCard, instructionCard, passwordCard);
    appendUsbOptions(page);
  }

  function onQrAdbWriteResult(event) {
    if (!usbOperation || usbOperation.kind !== "flag") return;
    usbOperation = null;
    labInstallBusy = false;
    qrAdbWriteStatus = event.result || { ok: false, error: "неизвестная ошибка" };
    finishRun({
      success: !!qrAdbWriteStatus.ok,
      message: qrAdbWriteStatus.ok ? "Файл записан. Теперь вставьте эту флешку в магнитолу." : (qrAdbWriteStatus.error || "Не удалось записать файл."),
    }, () => { if (stages[currentIndex] && stages[currentIndex].type === "qr_adb") render(); }, "qr-flag");
  }

  function onQrAdbPasswordResult(event) {
    if (!usbOperation || usbOperation.kind !== "password") return;
    usbOperation = null;
    labInstallBusy = false;
    qrAdbResult = event.result || { ok: false, error: "неизвестная ошибка" };
    finishRun({
      success: !!qrAdbResult.ok,
      message: qrAdbResult.ok ? "Пароль получен. Введите его на экране магнитолы." : (qrAdbResult.error || "Не удалось получить пароль."),
    }, () => { if (stages[currentIndex] && stages[currentIndex].type === "qr_adb") render(); }, "qr-password");
  }

  // "telnet"-этап: включает ADB-отладку на магнитоле удалённо (см.
  // TelnetAdb.kt, порт cars/_shared/telnet_adb.py) — своё, отдельное от
  // AdbSession TCP-соединение на каждую команду, поэтому не завязан на
  // верхнюю ADB-панель (та для проводного/Wi-Fi ADB, это другое). commands
  // — сырые строки (не DSL, см. wizard_spec.py), каждая шлётся отдельным
  // telnet-подключением.
  function renderTelnetStage(page, stage) {
    page.classList.add("flow-stage");
    const card = flowCard("Подключение по сети", "Укажите адрес магнитолы для выполнения команд через Telnet, порт 23.", "wifi");
    flowTechnicalDetails(card, (stage.commands || []).join("\n"), `Команды этапа · ${(stage.commands || []).length}`);
    const btn = usbStageButton("Указать адрес и выполнить", "wifi", () => {
      promptHostPicker("Адрес магнитолы для telnet (порт 23):", 23, (host) => {
        lastWifiHost = host;
        runStageOperation("telnet_run_stage", { index: stage.index, host, commands: stage.commands || [] }, page, card);
      });
    }, true);
    card.append(btn);
    flowResult(card, stageExecutionResults[stage.index]);
    page.append(card);
  }

  function updateTransportBars(stage) {
    adbConnectBtn.disabled = labInstallBusy;
    adbModeWiredBtn.disabled = labInstallBusy;
    adbModeWifiBtn.disabled = labInstallBusy;
    adbBarEl.style.display = ADB_STAGE_TYPES.has(stage.type) ? "flex" : "none";
    usbBarEl.style.display = USB_STAGE_TYPES.has(stage.type) ? "flex" : "none";
    // Переключатель "Провод/Wi-Fi" — только для apps-этапа с
    // apps_connection == "ask" (техник сам выбирает на месте, см.
    // connectionModeFor); для "wired"/"wifi" способ уже задан автором
    // модели, показывать нечего.
    const isAsk = (stage.type === "apps" && (stage.apps_connection || "wired") === "ask")
      || (stage.type === "actions" && (stage.actions_connection || "wired") === "ask");
    adbModeToggleEl.style.display = isAsk ? "flex" : "none";
    if (isAsk) {
      const current = appsConnectionChoice[stage.index] || "wired";
      adbModeWiredBtn.className = current === "wired" ? "accent" : "";
      adbModeWifiBtn.className = current === "wifi" ? "accent" : "";
    }
  }

  function renderStage(stage) {
    updateTransportBars(stage);
    const page = el("div", { class: "stage-page", "data-stage-type": stage.type, "data-stage-index":stage.index });
    page.appendChild(el("div", { class: "stage-chip", text: stage.type === "apps" ? "Приложения" : stage.title || stage.type || "" }));
    if (stage.description && !['actions', 'instruction'].includes(stage.type)) {
      const description = el("div", { class: "stage-text", text: stage.description });
      if (stage.type === "apps") {
        page.appendChild(el("details", { class: "lab-stage-help" }, [
          el("summary", { text: "Инструкция к этапу" }), description,
        ]));
      } else page.appendChild(description);
    }
    if (["adb", "actions"].includes(stage.type)) page.append(adbBarEl);

    nextAction = () => advanceAfter(stage.index);

    if (stage.type === "check") {
      page.classList.add("flow-stage");
      const options = stage.check_options || [];
      const list = el("div", { class: "check-options-list" });
      options.forEach((opt, i) => {
        const btn = el("button", { class: "flow-check-option", type: "button" }, [
          el("span", { class: "flow-option-number", text: String(i + 1), "aria-hidden": "true" }),
          el("span", { class: "flow-option-label", text: opt }), usbStageIcon("chevron"),
        ]);
        // Клик сразу продвигает по выбранной ветке (см. app/car_generator.py:
        // StepSpec.next_options).
        btn.addEventListener("click", () => advanceAfter(stage.index, i));
        list.appendChild(btn);
      });
      page.appendChild(list);
      if (!options.length) page.append(flowCard("Нет вариантов выбора", "Для этого этапа пока не заданы варианты.", "alert"));
      // "Далее" — для техника, который и так знает нужную ветку и просто
      // пролистывает мастер: по умолчанию первый вариант.
      nextAction = () => advanceAfter(stage.index, 0);
    } else if (stage.type === "manual") {
      page.classList.add("flow-stage");
      page.querySelectorAll(":scope > .stage-text").forEach(node => node.remove());
      const card = flowCard("На магнитоле", stage.description || "Выполните действия из инструкции на экране автомобиля.", "car");
      card.append(el("p", { class: "flow-action-note", text: "После выполнения нажмите «Далее»." }));
      if (stage.instruction_html) card.append(usbStageButton("Открыть инструкцию", "book", () => openUsbInstructions(stage)));
      page.append(card);
    } else if (stage.type === "instruction") {
      // instruction_html — ПОЛНЫЙ HTML-документ (<!DOCTYPE>/<html>/<head>
      // со своим <style>, см. instr_N/instruction.html), не фрагмент.
      // Раньше вставлялся через innerHTML прямо в страницу — его
      // <style>body{margin:16px; background:...}</style> в fragment-парсинге
      // ПРИМЕНЯЛСЯ К НАСТОЯЩЕМУ <body> всего приложения (не только к
      // контейнеру), перекрашивая/сдвигая его на каждом instruction-этапе —
      // это и был баг "интерфейс обрезан по краям", репортнутый техником
      // (совпадает 1:1: только на instruction-этапах, не на usb/apps/adb).
      // instruction_html уже пришёл с переписанными <img src> на абсолютные
      // https://appassets.androidplatform.net/data/... (см.
      // wizard_spec._rewrite_instruction_images/MainActivity.kt:
      // WebViewAssetLoader) — картинки отображаются и внутри iframe тоже,
      // тот же origin.
      {
        const iframe = el("iframe", { class: "stage-instruction-frame", title: stage.title || "Инструкция", scrolling: "no" });
        iframe.addEventListener("load", () => {
          try {
            const doc = iframe.contentDocument;
            // iframe — отдельный browsing context: если внутри него самого
            // остаётся хоть немного overflow (высота посчиталась чуть
            // заниженной — картинки/шрифты могли доподгрузиться уже ПОСЛЕ
            // load), палец должен свайпнуть ДВАЖДЫ — сначала скроллится
            // внешняя страница, и только вторым жестом, если он попал уже
            // именно на iframe, скроллится то немногое, что осталось
            // внутри него (техник это и заметил). overflow:hidden внутри
            // iframe убирает у него собственный скролл вообще — вся прокрутка
            // всегда только у внешней страницы, одним жестом.
            doc.documentElement.style.overflow = "hidden";
            if (doc.body) doc.body.style.overflow = "hidden";
            const syncHeight = () => {
              const height = Math.max(200, Math.ceil(doc.body?.getBoundingClientRect().height || 0), doc.body?.scrollHeight || 0);
              if (iframe.style.height !== `${height}px`) iframe.style.height = `${height}px`;
            };
            syncHeight();
            // ResizeObserver подхватывает досрочно занятую высоту (позднюю
            // подгрузку картинок/веб-шрифтов) без повторного load-события.
            if (window.ResizeObserver) {
              new ResizeObserver(syncHeight).observe(doc.body);
            }
          } catch (e) { /* останется дефолтная высота — не критично */ }
        });
        page.appendChild(iframe);
        iframe.srcdoc = LabUI.reader(stage.instruction_html || Instructions12.textDocument(stage.description || "Для этого этапа нет отдельной инструкции."), { title: stage.title || "Инструкция", description: stage.instruction_html ? stage.description : "" });
      }
    } else if (stage.type === "adb") {
      renderAdbStage(page, stage);
    } else if (stage.type === "apps") {
      renderAppsStage(page, stage);
    } else if (stage.type === "actions") {
      renderActionsStage(page, stage);
    } else if (stage.type === "usb") {
      renderUsbStage(page, stage);
    } else if (stage.type === "qr_adb") {
      renderQrAdbStage(page, stage);
    } else if (stage.type === "telnet") {
      renderTelnetStage(page, stage);
    } else if (!stage.supported || stage.type === "exe" || stage.type === "uart") {
      page.classList.add("flow-stage");
      const message = stage.type === "uart"
        ? "Подключение по UART пока не поддерживается в Android-версии. Выполните этот этап на компьютере."
        : stage.type === "exe"
        ? "Запуск программ Windows недоступен на Android. Выполните этот этап на компьютере."
        : `Этап «${stage.type}» пока не поддерживается в Android-версии.`;
      const card = flowCard("Нужен компьютер", message, "alert", "notice");
      if (stage.exe_file) flowTechnicalDetails(card, basename(stage.exe_file), "Программа этапа");
      if (stage.type === "uart") flowTechnicalDetails(card, (stage.commands || []).join("\n"), "Команды этапа");
      page.append(card);
    }

    wizardContentEl.appendChild(page);
  }

  // -- информационные всплывающие окна (приветствие/статус/поздравление) --
  // svg-иконки строятся через innerHTML (не el()) — document.createElement
  // не создаёт настоящие SVG-узлы (нет namespace), через innerHTML браузер
  // парсит их как foreign content правильно.
  function svgIcon(pathD, viewBox) {
    const span = el("span", { class: "icon" });
    span.innerHTML = `<svg viewBox="${viewBox || "0 0 24 24"}" width="18" height="18" fill="currentColor"><path d="${pathD}"/></svg>`;
    return span;
  }

  const STAR_ICON_PATH = "M12 2l2.9 6.26L22 9.27l-5 4.87L18.2 21 12 17.77 5.8 21 7 14.14l-5-4.87 7.1-1.01L12 2z";
  const HEART_ICON_PATH = "M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z";

  // Boosty — не собственная реализация оплаты (см. память проекта: "чистый
  // донат", без тиров/платного контента) — просто внешние ссылки, реально
  // открываются в системном браузере (см. MainActivity.kt:
  // shouldOverrideUrlLoading), не внутри WebView.
  function boostyLinksRow() {
    return el("div", { class: "boosty-links" }, [
      el("a", { class: "boosty-link", href: "https://boosty.to/magic_sqd", target: "_blank" }, [
        svgIcon(STAR_ICON_PATH), el("span", { text: "Подписаться на Boosty" }),
      ]),
      el("a", { class: "boosty-link", href: "https://boosty.to/magic_sqd/donate", target: "_blank" }, [
        svgIcon(HEART_ICON_PATH), el("span", { text: "Разовый донат" }),
      ]),
    ]);
  }

  // Автообновление ПРИЛОЖЕНИЯ (не cars/, см. onSyncFinished) — desktop-версия
  // (app.js) умеет тихо переустановить себя, здесь только ссылка на apk (нет
  // прав тихо заменить пакет без root, см. WebBridge.kt: startUpdateCheck) —
  // техник сам скачивает и ставит через системный установщик. Ссылка ведёт
  // на github.com, поэтому открывается во внешнем браузере (см.
  // MainActivity.kt: shouldOverrideUrlLoading), а не внутри WebView.
  function checkForUpdate() {
    try {
      Bridge.call("app_update_check", {});
    } catch (e) { /* сбой проверки не должен мешать обычной работе */ }
  }

  function onUpdateCheckResult(event) {
    const update = event.result;
    if (!update || !update.available) return;
    let overlay;
    overlay = showModal([
      el("img", { class: "modal-logo", src: "img/logo-full-dark.svg", alt: "Magic SQD" }),
      el("p", { class: "stage-text", style: "font-weight: 600; font-size: 17px", text: `Доступна новая версия: ${update.version}` }),
      el("p", {
        class: "stage-text", style: "color: var(--text-dim); white-space: pre-wrap",
        text: `Что нового:\n${update.changelog || "—"}`,
      }),
      el("a", {
        class: "accent", href: update.download_url, target: "_blank",
        text: "Скачать APK",
        onclick: () => overlay.remove(),
      }),
      el("button", { text: "Позже", onclick: () => overlay.remove() }),
    ]);
  }

  function showModal(boxChildren) {
    const overlay = el("div", { class: "modal-overlay dismissible" });
    overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
    overlay.appendChild(el("div", { class: "modal-box info-modal" }, boxChildren));
    document.body.appendChild(overlay);
    return overlay;
  }

  // Окно «нужен OTG-переходник»: показывается, когда техник нажал «Подключить ADB»
  // (проводное подключение), а по USB не найдено ни одного устройства с ADB
  // (см. WebBridge.kt: adb_connect_result.no_device). Самая частая причина —
  // кабель воткнут в телефон напрямую без OTG-переходника, или переходник
  // вставлен уже ПОСЛЕ кабеля. Анимация — зацикленный анимированный WebP на всё окно
  // (img/otg_hint.webp, ~270 КБ, сделан в Higgsfield/Kling: сначала переходник входит
  // в телефон, потом штекер кабеля — в переходник), а не <video>: автозапуск видео в
  // WebView зависит от настроек воспроизведения, картинка играет всегда. При включённом
  // системном «уменьшении движения» — статичный кадр img/otg_hint_still.webp.
  function showOtgHintModal() {
    if (document.querySelector(".otg-hint-overlay")) return; // не плодить копии при повторных нажатиях
    const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let overlay;
    // Анимация занимает ВСЁ окно (фон), текст и кнопки лежат поверх неё в свободных полосах
    // сверху и снизу кадра — сама сцена вставки (низ телефона, переходник, штекер) остаётся чистой.
    overlay = showModal([
      el("div", { class: "otg-sheet" }, [
        el("img", { class: "otg-hint-anim", alt: "", "aria-hidden": "true",
          src: reduceMotion ? "img/otg_hint_still.webp" : "img/otg_hint.webp" }),
        el("div", { class: "otg-top" }, [
          el("p", { class: "otg-hint-title", text: "Нужен OTG-переходник" }),
          el("ol", { class: "otg-hint-steps" }, [
            el("li", { text: "Сначала вставьте OTG-переходник в телефон." }),
            el("li", { text: "И только потом подключите к нему кабель от магнитолы." }),
          ]),
          el("p", { class: "otg-hint-note", text: "Всё подключено, а окно появилось? Включите отладку по USB на магнитоле." }),
        ]),
        el("div", { class: "otg-bottom" }, [
          el("button", { class: "accent", text: "Подключить снова", onclick: () => { overlay.remove(); onAdbConnect(); } }),
          el("button", { text: "Закрыть", onclick: () => overlay.remove() }),
        ]),
      ]),
    ]);
    overlay.classList.add("otg-hint-overlay");
    overlay.querySelector(".modal-box").classList.add("otg-hint-box");
  }

  const WELCOME_SHOWN_KEY = "magicsqd_welcome_shown_at";

  function maybeShowWelcomeModal() {
    const last = Number(localStorage.getItem(WELCOME_SHOWN_KEY) || 0);
    if (Date.now() - last < 60 * 60 * 1000) return; // раз в час, не при каждом запуске
    localStorage.setItem(WELCOME_SHOWN_KEY, String(Date.now()));
    let overlay;
    overlay = showModal([
      el("img", { class: "modal-logo", src: "img/logo-full-dark.svg", alt: "Magic SQD" }),
      el("p", { class: "stage-text", style: "font-weight: 600; font-size: 17px", text: "Добро пожаловать!" }),
      el("p", {
        class: "stage-text", style: "color: var(--text-dim)",
        text: "Мобильная версия Magic SQD пока в стадии тестирования — что-то может работать нестабильно. " +
          "Если найдёшь баг — дай знать нам.",
      }),
      boostyLinksRow(),
      el("button", { class: "accent", text: "Понятно", onclick: () => overlay.remove() }),
    ]);
  }

  // Значок "?" в шапке (только на списке марок) — что за приложение,
  // версия (раньше нигде не была видна вовсе, см. WebBridge.kt: app_version
  // читает versionName из PackageManager) и расшифровка цветных меток
  // статуса у моделей (те же формулировки, что и в самом списке —
  // STATUS_TITLES; название цвета пишем текстом рядом с точкой — одного
  // цвета точки мало, если цвет плохо различим на экране/для человека).
  const STATUS_COLOR_NAMES = { green: "Зелёный", blue: "Синий", yellow: "Жёлтый", red: "Красный" };

  function showAboutModal() {
    let overlay;
    let version = "?";
    try { version = Bridge.call("app_version", {}).version; } catch (e) { /* см. фолбэк выше */ }
    const legendRow = (color) => el("div", { class: "row", style: "gap: 8px; margin: 6px 0" }, [
      el("span", { class: `status-dot status-dot-${color}` }),
      el("span", { class: "stage-text", text: `${STATUS_COLOR_NAMES[color]} — ${STATUS_TITLES[color]}` }),
    ]);
    overlay = showModal([
      el("img", { class: "modal-logo", src: "img/logo-full-dark.svg", alt: "Magic SQD" }),
      el("p", { class: "stage-text", style: "font-weight: 600; font-size: 17px", text: `Magic SQD — версия ${version}` }),
      el("p", {
        class: "stage-text", style: "color: var(--text-dim); margin-top: 4px",
        text: "Ставит приложения на Android-магнитолы китайских авто — по ADB " +
          "(провод или Wi-Fi) или через USB-флешку, прямо с телефона, без компьютера рядом.",
      }),
      el("p", { class: "stage-text", style: "color: var(--text-dim); margin-top: 12px", text: "Цветные метки у моделей:" }),
      legendRow("green"),
      legendRow("blue"),
      legendRow("yellow"),
      legendRow("red"),
      el("button", { class: "accent", text: "Понятно", onclick: () => overlay.remove() }),
    ]);
  }

  // Аккаунт техника (см. auth_bridge.py, WebBridge.kt) — вход/регистрация
  // email+пароль (подтверждение почты по ссылке при регистрации, как и на
  // desktop), либо "Вы вошли как ..." + "Выйти", если сессия уже есть.
  // Только вход/просмотр своих машин на модерации — редактирования на
  // Android нет вовсе (см. план: сначала нужен сам редактор).
  function menuAction(text, symbol, className = "") {
    return el("button", { class: `menu13-action ${className}`, type: "button" }, [
      AppIcons.icon(symbol), el("span", { class: "menu13-action-label", text }),
    ]);
  }

  function setMenuActionLabel(button, text) {
    const label = button.querySelector(".menu13-action-label");
    if (label) label.textContent = text;
    else button.textContent = text;
  }

  function menuHeading(title, symbol) {
    return el("div", { class: "menu13-section-heading" }, [AppIcons.icon(symbol), el("h3", { text: title })]);
  }

  // A single sheet frame keeps the close button fixed while only its content scrolls.
  function showMenuModal(title, symbol, content, options = {}) {
    const previousFocus = document.activeElement;
    const overlay = el("div", { class: `modal-overlay dismissible menu13-overlay ${options.className || ""}` });
    const box = el("section", { class: "modal-box menu13-sheet", role: "dialog", "aria-modal": "true", "aria-label": title, tabindex: "-1" });
    const close = menuAction("", "close", "menu13-close");
    close.setAttribute("aria-label", "Закрыть");
    const heading = el("header", { class: "menu13-heading" }, [
      el("span", { class: "menu13-symbol" }, [AppIcons.icon(symbol)]),
      el("h2", { text: title }), close,
    ]);
    box.append(heading, el("div", { class: "menu13-body" }, content));
    if (options.footer) box.append(el("footer", { class: "menu13-footer" }, options.footer));
    overlay.append(box);
    const remove = overlay.remove.bind(overlay);
    let removed = false;
    overlay.remove = () => {
      if (removed) return;
      removed = true;
      document.removeEventListener("keydown", onKey);
      options.onClose?.();
      remove();
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    };
    function onKey(event) {
      if (event.key === "Escape") { event.preventDefault(); overlay.remove(); return; }
      if (event.key !== "Tab") return;
      const targets = [...box.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),[tabindex="0"]')]
        .filter(node => !node.hidden && node.getClientRects().length);
      if (!targets.length) { event.preventDefault(); box.focus(); return; }
      const first = targets[0], last = targets[targets.length - 1];
      if (event.shiftKey && (document.activeElement === first || !box.contains(document.activeElement))) {
        event.preventDefault(); last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !box.contains(document.activeElement))) {
        event.preventDefault(); first.focus();
      }
    }
    close.addEventListener("click", () => overlay.remove());
    overlay.addEventListener("click", event => { if (event.target === overlay) overlay.remove(); });
    document.addEventListener("keydown", onKey);
    document.body.append(overlay);
    close.focus({ preventScroll: true });
    return overlay;
  }

  function buildAccountSection() {
    const container = el("section", { class: "settings-section menu13-account" });
    let mode = "login"; // "login" | "register"
    let savedEmail = "";

    function render(email) {
      clear(container);
      if (email) {
        container.append(
          el("div", { class: `menu13-profile${Bridge.call("auth_status", {}).subscriber ? " is-subscriber" : ""}` }, [
            el("span", { class: "menu13-avatar" }, [AppIcons.icon("user")]),
            el("div", { class: "menu13-profile-copy" }, [el("strong", { text: email }), el("span", { text: "Аккаунт техника" })]),
          ]),
          el("div", { class: "menu13-account-note" }, [AppIcons.icon("car"), el("p", { text: "Ваши модели на модерации появляются в каталоге автоматически." })]),
        );
        const logoutBtn = menuAction("Выйти из аккаунта", "back", "menu13-logout");
        logoutBtn.addEventListener("click", () => {
          logoutBtn.disabled = true;
          Bridge.call("auth_logout", {});
        });
        container.appendChild(logoutBtn);
        return;
      }
      container.append(el("div", { class: "menu13-account-intro" }, [
        el("h3", { text: mode === "login" ? "Вход в аккаунт" : "Создать аккаунт" }),
        el("p", { text: mode === "login" ? "Ваши модели и заявки на модерации." : "Подтверждение придёт на вашу почту." }),
      ]));
      const form = el("form", { class: "menu13-account-form", novalidate: "" });
      const emailInput = el("input", { type: "email", inputmode: "email", autocomplete: "email", autocapitalize: "none", spellcheck: "false", placeholder: "name@example.com", required: "" });
      emailInput.value = savedEmail;
      const passwordInput = el("input", { type: "password", autocomplete: mode === "login" ? "current-password" : "new-password", placeholder: "Введите пароль", required: "" });
      // Появляется только после неудачного входа (не при регистрации) — до
      // этого её показ намекал бы, что с паролем уже что-то не так.
      const forgotBtn = el("button", { class: "link-btn account-forgot-btn", type: "button", text: "Забыли пароль?", hidden: true });
      const statusEl = el("p", { class: "settings-muted menu13-status", role: "status", "aria-live": "polite", text: "" });
      const submitBtn = menuAction(mode === "login" ? "Войти" : "Зарегистрироваться", "user", "accent");
      submitBtn.type = "submit";
      const switchBtn = el("button", {
        class: "link-btn menu13-mode-switch", type: "button",
        text: mode === "login" ? "Нет аккаунта? Зарегистрироваться" : "Уже есть аккаунт? Войти",
      });
      switchBtn.addEventListener("click", () => {
        savedEmail = emailInput.value.trim();
        mode = mode === "login" ? "register" : "login";
        render(null);
      });
      form.addEventListener("submit", event => {
        event.preventDefault();
        if (submitBtn.disabled) return;
        const email = emailInput.value.trim();
        const password = passwordInput.value;
        if (!email || !password) {
          statusEl.textContent = "Введите email и пароль.";
          statusEl.dataset.state = "error";
          (!email ? emailInput : passwordInput).focus();
          return;
        }
        savedEmail = email;
        submitBtn.disabled = true;
        switchBtn.disabled = true;
        forgotBtn.hidden = true;
        statusEl.dataset.state = "pending";
        statusEl.textContent = mode === "login" ? "Вхожу..." : "Регистрирую...";
        Bridge.call(mode === "login" ? "auth_login" : "auth_register", { email, password });
        // Результат придёт событием auth_login_result/auth_register_result
        // (см. accountRenderCallback выше) — Bridge.call тут же возвращает
        // "{}", реальная сетевая работа идёт в фоновом Kotlin-потоке.
      });
      forgotBtn.addEventListener("click", () => {
        const email = emailInput.value.trim();
        if (!email) { statusEl.textContent = "Введите email, на который зарегистрирован аккаунт."; return; }
        forgotBtn.disabled = true;
        statusEl.textContent = "Отправляю письмо...";
        Bridge.call("auth_forgot_password", { email });
        // Результат — событием auth_forgot_password_result (см. ниже).
      });
      form.append(
        el("label", { class: "menu13-field" }, [el("span", { text: "Email" }), emailInput]),
        el("label", { class: "menu13-field" }, [el("span", { text: "Пароль" }), passwordInput]),
        forgotBtn, statusEl, submitBtn, switchBtn,
      );
      container.append(form);
    }

    accountRenderCallback = (kind, result) => {
      if (!container.isConnected) return;
      if (kind === "logout") {
        render(null);
        return;
      }
      if (kind === "forgot_password") {
        const statusEl = container.querySelector("p.settings-muted");
        const forgotBtn = container.querySelector("button.account-forgot-btn");
        if (forgotBtn) forgotBtn.disabled = false;
        // Сервер намеренно отвечает одинаково независимо от того, есть ли
        // такой email в базе (см. server/backend.py:_handle_auth_forgot_
        // password) — не подтверждаем/опровергаем существование аккаунта.
        if (statusEl) {
          statusEl.dataset.state = result.ok ? "success" : "error";
          statusEl.textContent = result.ok
            ? "Если такой аккаунт есть, письмо со ссылкой для сброса пароля отправлено."
            : result.error;
        }
        return;
      }
      if (!result.ok) {
        const statusEl = container.querySelector("p.settings-muted");
        if (statusEl) { statusEl.textContent = result.error; statusEl.dataset.state = "error"; }
        const submitBtn = container.querySelector("button.accent");
        if (submitBtn) submitBtn.disabled = false;
        const switchBtn = container.querySelector(".menu13-mode-switch");
        if (switchBtn) switchBtn.disabled = false;
        if (kind === "login") {
          const forgotBtn = container.querySelector("button.account-forgot-btn");
          if (forgotBtn) forgotBtn.hidden = false;
        }
        return;
      }
      if (kind === "register") {
        mode = "login";
        render(null);
        const statusEl = container.querySelector("p.settings-muted");
        if (statusEl) {
          statusEl.dataset.state = "success";
          statusEl.textContent = "Письмо с подтверждением отправлено — перейдите по ссылке, потом войдите здесь.";
        }
        return;
      }
      render(result.email); // kind === "login"
    };

    const initialInfo = Bridge.call("auth_status", {});
    render(initialInfo.email);
    if (initialInfo.email) Bridge.call("auth_refresh_subscriber", {});
    return container;
  }

  function showAccountModal() {
    showMenuModal("Аккаунт", "user", [buildAccountSection()], {
      className: "menu13-account-overlay", onClose: () => { accountRenderCallback = null; },
    });
  }

  function showSettingsModal() {
    const formatBytes = (value) => {
      const units = ["Б", "КБ", "МБ", "ГБ"]; let size = value || 0; let index = 0;
      while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
      return `${size >= 100 || index === 0 ? Math.round(size) : size.toFixed(1)} ${units[index]}`;
    };
    const info = Bridge.call("settings_info", {});
    const auth = Bridge.call("auth_status", {});
    const version = Bridge.call("app_version", {}).version || "";
    let overlay;
    const cacheLabel = el("strong", { text: formatBytes(info.cache_bytes) });
    const makeToggle = (text, key) => {
      const input = el("input", { type: "checkbox", class: "menu13-switch", role: "switch", "data-preference": key });
      input.checked = !!info.preferences[key];
      input.addEventListener("change", () => {
        const preferences = Bridge.call("settings_set_preferences", { [key]: input.checked });
        document.documentElement.classList.toggle("reduce-motion", preferences.reduced_motion);
        document.getElementById("app").classList.toggle("compact-log", preferences.compact_log);
      });
      return el("label", { class: "settings-toggle menu13-toggle" }, [el("span", { text }), input]);
    };
    const clear = menuAction("Очистить кэш", "trash");
    clear.dataset.action = "clear-cache";
    clear.addEventListener("click", () => {
      // Android WebView не показывает нативный confirm() без отдельного
      // WebChromeClient; кнопка уже однозначно подписана и очищает только
      // безопасный перекачиваемый кэш, поэтому дополнительный диалог тут
      // намеренно не нужен.
      const result = Bridge.call("settings_clear_cache", {});
      cacheLabel.textContent = formatBytes(result.remaining_bytes);
      setMenuActionLabel(clear, `Освобождено: ${formatBytes(result.freed_bytes)}`);
    });
    const sync = menuAction("Обновить каталог", "refresh");
    sync.dataset.action = "sync";
    sync.addEventListener("click", () => {
      sync.disabled = true;
      setMenuActionLabel(sync, "Проверяем…");
      settingsSyncButton = sync;
      if (settingsSyncTimeout) clearTimeout(settingsSyncTimeout);
      settingsSyncTimeout = setTimeout(() => {
        if (settingsSyncButton !== sync) return;
        sync.disabled = false;
        setMenuActionLabel(sync, "Не удалось проверить");
        settingsSyncButton = null;
        settingsSyncTimeout = null;
      }, 45000);
      try {
        Bridge.call("settings_sync_now", {});
      } catch (error) {
        clearTimeout(settingsSyncTimeout);
        settingsSyncTimeout = null;
        settingsSyncButton = null;
        sync.disabled = false;
        setMenuActionLabel(sync, "Не удалось проверить");
        console.error("Не удалось запустить проверку обновлений:", error);
      }
    });
    const copyLog = menuAction("Скопировать лог", "copy");
    copyLog.dataset.action = "copy-log";
    copyLog.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(logPanelEl.innerText); setMenuActionLabel(copyLog, "Лог скопирован"); }
      catch (_) { setMenuActionLabel(copyLog, "Не удалось скопировать"); log("Не удалось скопировать лог автоматически."); }
    });
    const account = el("button", { class: "menu13-account-link", type: "button", "data-action": "account" }, [
      el("span", { class: "menu13-avatar" }, [AppIcons.icon("user")]),
      el("span", { class: "menu13-link-copy" }, [el("strong", { text: "Аккаунт" }), el("small", { text: auth.email || "Вход и регистрация" })]),
      AppIcons.icon("chevron"),
    ]);
    account.classList.toggle("is-subscriber", Boolean(auth.email && auth.subscriber));
    account.addEventListener("click", () => { overlay.remove(); showAccountModal(); });
    if (auth.email) Bridge.call("auth_refresh_subscriber", {}); // цвет обновится событием auth_subscriber_result
    const github = el("a", { class: "menu13-action menu13-link", href: "https://github.com/torisar93/magic_sqd", target: "_blank", rel: "noopener" }, [
      AppIcons.icon("link"), el("span", { class: "menu13-action-label", text: "GitHub проекта" }), AppIcons.icon("chevron"),
    ]);
    overlay = showMenuModal("Настройки", "settings", [
      account,
      el("section", { class: "settings-section menu13-card" }, [
        menuHeading("Интерфейс", "settings"),
        makeToggle("Уменьшить анимации", "reduced_motion"),
        makeToggle("Не выключать экран", "keep_screen_on"),
        makeToggle("Компактный лог", "compact_log"),
        makeToggle("ИИ-чат в логе", "chat_enabled"),
      ]),
      el("section", { class: "settings-section menu13-card" }, [
        menuHeading("Каталог", "refresh"),
        makeToggle("Обновлять при запуске", "auto_sync"), sync,
      ]),
      el("section", { class: "settings-section menu13-card" }, [
        menuHeading("Хранилище", "folder"),
        el("div", { class: "menu13-storage" }, [
          el("div", {}, [el("span", { text: "Приложение" }), el("strong", { text: formatBytes(info.app_bytes) })]),
          el("div", {}, [el("span", { text: "Кэш" }), cacheLabel]),
        ]), clear,
        el("small", { class: "menu13-hint", text: "Сценарии и настройки сохранятся." }),
      ]),
      el("section", { class: "settings-section menu13-card" }, [menuHeading("Диагностика", "log"), copyLog, github]),
      el("p", { class: "menu13-version", text: `Magic SQD${version ? " · " + version : ""}` }),
    ], { className: "menu13-settings-overlay" });
    document.documentElement.classList.toggle("reduce-motion", info.preferences.reduced_motion);
  }

  function showStatusWarningModal(modelSummary) {
    const isRed = modelSummary.status_color === "red";
    let overlay;
    const actions = isRed
      ? [el("button", { class: "accent", text: "Понятно", onclick: () => overlay.remove() })]
      : [
          el("button", { text: "Отмена", onclick: () => overlay.remove() }),
          el("button", {
            class: "accent", text: "Всё равно открыть",
            onclick: () => { overlay.remove(); openModel(modelSummary); },
          }),
        ];
    overlay = showModal([
      el("p", {
        class: "stage-text", style: `font-weight: 600; font-size: 17px; color: var(--${isRed ? "danger" : "text"})`,
        text: isRed ? "Установка недоступна" : "Черновой способ установки",
      }),
      el("p", {
        class: "stage-text", style: "color: var(--text-dim)",
        text: isRed
          ? "Установка этой модели с Android-устройства сейчас не работает — способ помечен как нерабочий на desktop."
          : "Установка этой модели с Android-устройства ещё не готова полностью — способ черновой, возможны проблемы.",
      }),
      el("div", { class: "modal-actions" }, actions),
    ]);
  }

  function showCompletionModal() {
    let overlay;
    overlay = showModal([
      el("p", { class: "stage-text", style: "font-weight: 600; font-size: 19px", text: "Всё готово" }),
      el("p", {
        class: "stage-text", style: "color: var(--text-dim)",
        text: "Установка завершена. Если Magic SQD экономит тебе время — поддержи проект на Boosty, это реально помогает развитию.",
      }),
      boostyLinksRow(),
      el("button", { class: "accent", text: "Понятно", onclick: () => overlay.remove() }),
    ]);
  }

  document.addEventListener("DOMContentLoaded", () => {
    screenPicker = document.getElementById("screen-picker");
    screenWizard = document.getElementById("screen-wizard");
    breadcrumbEl = document.getElementById("picker-breadcrumb");
    syncStatusEl = document.getElementById("picker-sync-status");
    listEl = document.getElementById("picker-list");
    startupOverlayEl = document.getElementById("catalog-startup-overlay");
    startupProgressFillEl = document.getElementById("catalog-startup-progress-fill");
    startupProgressLabelEl = document.getElementById("catalog-startup-progress-label");
    pickerSearchEl = document.getElementById("picker-search");
    pickerSearchEl.addEventListener("input", () => {
      if (selectedGroup) showModificationStep(selectedGroup);
      else if (selectedBrand) showGroupStep(selectedBrand);
      else showBrandStep();
    });
    topHelpBtn = document.getElementById("top-help");
    topHelpBtn.addEventListener("click", showSettingsModal);
    topAccountBtn = document.getElementById("top-account");
    topAccountBtn.addEventListener("click", showAccountModal);
    wizardContentEl = document.getElementById("wizard-content");
    wizardBackBtn = document.getElementById("wizard-back");
    wizardVideoBtn = document.getElementById("wizard-video");
    wizardNextBtn = document.getElementById("wizard-next");
    wizardPageLabel = document.getElementById("wizard-page-label");
    logPanelEl = document.getElementById("log-panel");
    topTitleEl = document.getElementById("top-title");
    topBackBtn = document.getElementById("top-back");
    topbarEl = document.querySelector(".topbar");
    adbStatusEl = document.getElementById("adb-status");
    adbConnectBtn = document.getElementById("adb-connect-btn");
    usbStatusEl = document.getElementById("usb-status");
    usbConnectBtn = document.getElementById("usb-connect-btn");
    usbFormatBtn = document.getElementById("usb-format-btn");
    adbBarEl = document.getElementById("adb-bar");
    usbBarEl = document.getElementById("usb-bar");
    adbModeToggleEl = document.getElementById("adb-mode-toggle");
    adbModeWiredBtn = document.getElementById("adb-mode-wired");
    adbModeWifiBtn = document.getElementById("adb-mode-wifi");
    logBarEl = document.getElementById("log-bar");
    logLastLineEl = document.getElementById("log-last-line");
    logExpandBtn = document.getElementById("log-expand-btn");
    logOverlayEl = document.getElementById("log-overlay");
    document.getElementById('app').append(logOverlayEl);
    document.getElementById('top-log').addEventListener('click',()=>setLogOpen(true));
    logCollapseBtn = document.getElementById("log-collapse-btn");
    logCopyBtn = document.getElementById("log-copy-btn");
    logCmdInput = document.getElementById("log-cmd-input");
    logCmdRunBtn = document.getElementById("log-cmd-run");

    wizardBackBtn.addEventListener("click", goBack);
    wizardVideoBtn.addEventListener("click", playStageVideo);
    wizardNextBtn.addEventListener("click", () => nextAction());
    adbConnectBtn.addEventListener("click", onAdbConnect);
    adbModeWiredBtn.addEventListener("click", () => {
      appsConnectionChoice[stages[currentIndex].index] = "wired";
      updateTransportBars(stages[currentIndex]);
    });
    adbModeWifiBtn.addEventListener("click", () => {
      appsConnectionChoice[stages[currentIndex].index] = "wifi";
      updateTransportBars(stages[currentIndex]);
    });
    usbConnectBtn.addEventListener("click", onUsbConnect);
    usbFormatBtn.addEventListener("click", onUsbFormat);
    logBarEl.addEventListener("click", () => setLogOpen(true));
    logCollapseBtn.addEventListener("click", () => setLogOpen(false));
    logCopyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(logPanelEl.innerText);
        const original = logCopyBtn.innerHTML;
        logCopyBtn.replaceChildren(usbStageIcon('check'));
        setTimeout(() => { logCopyBtn.innerHTML = original; }, 1500);
      } catch (_) {
        log("Не удалось скопировать лог автоматически.");
      }
    });
    logOverlayEl.addEventListener("click", (e) => { if (e.target === logOverlayEl) setLogOpen(false); });
    logCmdRunBtn.addEventListener("click", onLogCmdRun);
    logCmdInput.addEventListener("keydown", (e) => { if (e.key === "Enter") onLogCmdRun(); });
    // Фокус в поле ввода команды сам открывает лог, если он ещё свёрнут —
    // та же идея, что и в desktop-версии (см. app/web/frontend/js/app.js),
    // но проще: лог здесь и так модальный оверлей с закрытием по клику на
    // затемнённый фон (см. logOverlayEl выше) — эта же кнопка "закрыть
    // куда-то мимо" уже работает одинаково независимо от того, открыли лог
    // вручную или так, отдельного "автозакрытия" не нужно.
    // updateLogCmdSuggestions() тут раньше вызывался и на "focus" — с пустым
    // полем это показывало ВЕСЬ список подсказок сразу же по клику в поле,
    // ещё до того как техник начал печатать. Подсказки теперь появляются
    // только по вводу текста (см. "input" listener в initLogCmdSuggestions).
    logCmdInput.addEventListener("focus", () => { setLogOpen(true); });
    logCmdInput.addEventListener("blur", () => {
      // Небольшая задержка — подстраховка для выбора подсказки тачем/
      // клавиатурной навигацией без mousedown (см. initLogCmdSuggestions
      // выше — там основной путь для мыши/тача).
      setTimeout(hideLogCmdSuggestions, 150);
    });
    initLogCmdSuggestions();
    window.events.on("chat_reply", onChatReply);
    window.events.on("chat_command_result", onChatCommandResult);
    window.events.on("auth_register_result", (event) => { if (accountRenderCallback) accountRenderCallback("register", event.result); });
    window.events.on("auth_login_result", (event) => { if (accountRenderCallback) accountRenderCallback("login", event.result); });
    window.events.on("auth_logout_result", (event) => { if (accountRenderCallback) accountRenderCallback("logout", event.result); });
    window.events.on("auth_forgot_password_result", (event) => { if (accountRenderCallback) accountRenderCallback("forgot_password", event.result); });
    // Статус подписчика Boosty (ставится вручную) — значок аккаунта в настройках и профиль окрашиваются в цвет Boosty.
    window.events.on("auth_subscriber_result", (event) => {
      document.querySelectorAll(".menu13-account-link, .menu13-profile").forEach((node) => node.classList.toggle("is-subscriber", Boolean(event.subscriber)));
    });
    // Свои заявки на модерации подтянулись (при старте с сохранённой
    // сессией или сразу после входа, см. WebBridge.kt: authSyncMyCars) —
    // список машин нужно перечитать, иначе они не появятся в каталоге до
    // ручного перезапуска приложения.
    window.events.on("auth_sync_finished", (event) => {
      if (event.result && event.result.ok && event.result.synced && event.result.synced.length) {
        loadCars();
      }
    });
    window.events.on("network_scan_result", onNetworkScanResult);
    window.events.on("adb_service_scan_result", onAdbServiceScanResult);
    window.events.on("actions_packages_result", onActionsPackagesResult);
    window.events.on("personal_apks_picked", onPersonalApksPicked);
    window.events.on("sync_finished", onSyncFinished);
    window.events.on("model_sync_finished", onModelSyncFinished);
    window.events.on("adb_connect_result", onAdbConnectResult);
    window.events.on("adb_log", onAdbLog);
    window.events.on("adb_ask_input", onAdbAskInput);
    window.events.on("adb_stage_result", onAdbStageResult);
    window.events.on("usb_connect_result", onUsbConnectResult);
    window.events.on("usb_format_result", onUsbFormatResult);
    window.events.on("qr_adb_write_result", onQrAdbWriteResult);
    window.events.on("qr_adb_password_result", onQrAdbPasswordResult);
    window.events.on("apk_library_result", onApkLibraryResult);
    window.events.on("update_check_result", onUpdateCheckResult);
    window.events.on("video_ready", onVideoReady);

    showScreen("picker");
    const preferences = Bridge.call("settings_preferences", {});
    document.documentElement.classList.toggle("reduce-motion", preferences.reduced_motion);
    document.getElementById("app").classList.toggle("compact-log", preferences.compact_log);
    if (!preferences.auto_sync) syncStatusEl.style.display = "none";
    loadCars();
    const catalogWasEmpty = !carsData || !carsData.brands || carsData.brands.length === 0;
    if (preferences.auto_sync) startSync(catalogWasEmpty);
    maybeShowWelcomeModal();
    checkForUpdate();
  });
})();
