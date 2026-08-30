// Новый мобильный интерфейс — с нуля, не портирован из desktop-версии.
// Пикер марка->модель[->модификация] на РЕАЛЬНОМ каталоге cars/,
// синхронизируемом с сервера (см. WebBridge.kt/python/content_sync.py) +
// мастер этапов установки, исполняющий _wizard_spec.json напрямую поверх
// ADB/USB-транспорта (см. WebBridge.kt/InstallEngine.kt/UsbFlashSession.kt).
(function () {
  const { el, clear } = window.dom;

  const STATUS_TITLES = { green: "Актуально", yellow: "Черновой способ", blue: "Недавно обновлено", red: "Не работает" };

  let screenPicker, screenWizard, breadcrumbEl, listEl, syncStatusEl, pickerSearchEl;
  let wizardContentEl, wizardBackBtn, wizardNextBtn, wizardPageLabel, logPanelEl, topTitleEl, topBackBtn, topbarEl, topHelpBtn;
  let adbStatusEl, adbConnectBtn, usbStatusEl, usbConnectBtn, usbFormatBtn, adbBarEl, usbBarEl;
  let adbModeToggleEl, adbModeWiredBtn, adbModeWifiBtn;
  let logBarEl, logLastLineEl, logExpandBtn, logOverlayEl, logCollapseBtn, logCmdInput, logCmdRunBtn;

  // Один открытый скан за раз — достаточно, оба места, где он запускается
  // (Wi-Fi ADB / telnet), сами по себе модальные и блокируют остальной UI.
  let pendingScanCallback = null;

  // Какие типы этапов реально используют ADB/флешку — панели подключения
  // показываются только для них, а не постоянно на весь мастер (иначе на
  // instruction/check/manual этапах висят лишние кнопки безо всякого толку).
  const ADB_STAGE_TYPES = new Set(["adb", "apps", "actions"]);
  const USB_STAGE_TYPES = new Set(["usb"]);

  let carsData = null;
  let selectedBrand = null;
  let selectedGroup = null;

  let model = null;
  let stages = [];
  let currentIndex = 0;
  let vars = {};
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

  function log(text) {
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

  function setLogOpen(open) {
    logOverlayEl.classList.toggle("open", open);
    if (open) logPanelEl.scrollTop = logPanelEl.scrollHeight;
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
    const matches = (normalized
      ? SHELL_CMD_SUGGESTIONS.filter((s) => s.value.toLowerCase().startsWith(normalized))
      : SHELL_CMD_SUGGESTIONS
    ).slice(0, 8);
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

  function onLogCmdRun() {
    const command = logCmdInput.value.trim();
    if (!command) return;
    logCmdInput.value = "";
    hideLogCmdSuggestions();
    logConsoleCommand(command);
    Bridge.call("adb_shell_command", { command });
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
    screenPicker.classList.toggle("active", name === "picker");
    screenWizard.classList.toggle("active", name === "wizard");
    updateTopBack();
    // Кнопка настроек показывается только в каталоге, как и на desktop.
    topHelpBtn.style.visibility = name === "picker" ? "visible" : "hidden";
    clear(topTitleEl);
    topTitleEl.classList.remove("marquee");
    topTitleEl.style.left = "";
    topTitleEl.style.right = "";
    if (name === "wizard" && model) {
      const span = el("span", { text: model.display_label });
      topTitleEl.appendChild(span);
      // Длинные названия моделей не влезают в отведённую под заголовок
      // ширину — вместо обрезки многоточием (конец названия вообще не
      // видно) едет бегущей строкой, но только когда реально не влезло.
      requestAnimationFrame(() => {
        if (span.scrollWidth <= topTitleEl.clientWidth) return;
        topTitleEl.classList.add("marquee");
        // В режиме бегущей строки отдаём под неё всю ширину справа от
        // кнопки "Назад" (а не узкие 62% по центру, как для лого/короткого
        // текста) — иначе почти вся полоса сверху простаивала без дела.
        const barRect = topbarEl.getBoundingClientRect();
        const leftEdge = topBackBtn.style.visibility === "hidden"
          ? 12
          : Math.round(topBackBtn.getBoundingClientRect().right - barRect.left) + 14;
        topTitleEl.style.left = `${leftEdge}px`;
        topTitleEl.style.right = "12px";
        // Скорость (px/с), а не фиксированная длительность — иначе длинные
        // названия пролетают слишком быстро, а короткие тащатся зря долго.
        const distancePx = span.scrollWidth * 2;
        const speedPxPerSec = 45;
        span.style.animationDuration = `${Math.max(8, distancePx / speedPxPerSec)}s`;
      });
    } else {
      topTitleEl.appendChild(el("img", { class: "topbar-logo", src: "img/logo-full-dark.svg", alt: "Magic SQD" }));
    }
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
      topBackBtn.textContent = "Назад";
      topBackBtn.onclick = () => showScreen("picker");
    } else if (selectedGroup) {
      topBackBtn.style.visibility = "visible";
      topBackBtn.textContent = "Назад";
      topBackBtn.onclick = () => showGroupStep(selectedBrand);
    } else if (selectedBrand) {
      topBackBtn.style.visibility = "visible";
      topBackBtn.textContent = "Назад";
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
    visible.forEach((item, index) => {
      const card = el("button", {
        class: `catalog-card catalog-card-${item.kind || "model"}`,
        type: "button", onclick: item.onClick,
        "aria-label": `${item.label}. ${item.meta || "Открыть"}`,
      });
      card.style.setProperty("--card-order", Math.min(index, 10));
      const visual = el("span", { class: "catalog-card-visual" });
      if (item.icon) {
        const img = el("img", {
          class: item.kind === "brand" ? "catalog-brand-logo" : "catalog-model-logo",
          src: dataUrl(item.icon), alt: `Логотип ${item.label}`,
          loading: "lazy",
        });
        img.addEventListener("error", () => { img.replaceWith(item.kind === "brand" ? el("span", { class: "catalog-monogram", text: item.label.slice(0, 2).toLocaleUpperCase() }) : defaultModelLogo(item.label)); }, { once: true });
        visual.appendChild(img);
      } else if (item.kind === "brand") {
        visual.appendChild(el("span", { class: "catalog-monogram", text: item.label.slice(0, 2).toLocaleUpperCase() }));
      } else {
        visual.appendChild(defaultModelLogo(item.label));
      }
      const content = el("span", { class: "catalog-card-content" }, [
        el("span", { class: "catalog-card-title", text: item.label }),
        el("span", { class: "catalog-card-meta", text: item.meta || "Открыть инструкцию" }),
      ]);
      const footer = el("span", { class: "catalog-card-footer" });
      if (item.color) footer.appendChild(el("span", { class: `status-dot status-dot-${item.color}`, title: STATUS_TITLES[item.color] || "", "aria-label": STATUS_TITLES[item.color] || "Статус" }));
      footer.appendChild(el("span", { class: "catalog-card-action", text: item.action || "Открыть" }));
      card.append(visual, content, footer);
      listEl.appendChild(card);
    });
  }

  function plural(number, one, few, many) {
    const mod10 = number % 10;
    const mod100 = number % 100;
    return mod10 === 1 && mod100 !== 11 ? one : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14) ? few : many;
  }

  function searchResults(query) {
    const results = [];
    for (const brand of carsData.brands || []) {
      if (brand.name.toLocaleLowerCase().includes(query)) results.push({ kind: "brand", label: brand.name, meta: `${brand.groups.length} ${plural(brand.groups.length, "модель", "модели", "моделей")}`, color: brand.status_color, icon: brand.logo, action: "Открыть марку", onClick: () => showGroupStep(brand) });
      for (const group of brand.groups) {
        if (group.name.toLocaleLowerCase().includes(query)) results.push({ kind: "model", label: group.name, meta: brand.name, color: group.status_color, icon: group.logo || (group.leaf && group.leaf.logo), action: group.has_modifications ? "Выбрать версию" : "Открыть", onClick: () => group.has_modifications ? showModificationStep(group) : selectModel(group.leaf) });
        for (const modification of group.modifications || []) {
          if ((modification.modification || "").toLocaleLowerCase().includes(query)) results.push({ kind: "variant", label: `${group.name} — ${modification.modification}`, meta: brand.name, color: modification.status_color, icon: modification.logo || group.logo, onClick: () => selectModel(modification) });
        }
      }
    }
    return results;
  }

  function showBrandStep() {
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
    renderList(carsData.brands.map((b) => ({ kind: "brand", label: b.name, meta: `${b.groups.length} ${plural(b.groups.length, "модель", "модели", "моделей")}`, color: b.status_color, icon: b.logo, onClick: () => showGroupStep(b) })));
  }

  function showGroupStep(brand) {
    selectedBrand = brand;
    selectedGroup = null;
    renderBreadcrumb();
    updateTopBack();
    resetPickerScroll();
    renderList(brand.groups.map((g) => ({
      kind: "model", label: g.name, icon: g.logo || (g.leaf && g.leaf.logo),
      meta: g.has_modifications ? `${g.modifications.length} ${plural(g.modifications.length, "версия", "версии", "версий")}` : g.leaf.no_instruction ? "Способ уточняется" : "Открыть инструкцию",
      action: g.has_modifications ? "Выбрать версию" : "Открыть",
      color: g.status_color,
      onClick: () => (g.has_modifications ? showModificationStep(g) : selectModel(g.leaf)),
    })));
  }

  function showModificationStep(group) {
    selectedGroup = group;
    renderBreadcrumb();
    updateTopBack();
    resetPickerScroll();
    renderList(group.modifications.map((m) => ({ kind: "variant", label: m.modification, meta: m.no_instruction ? "Способ уточняется" : "Открыть инструкцию", icon: m.logo || group.logo, color: m.status_color, onClick: () => selectModel(m) })));
  }

  function openModel(modelSummary) {
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
      settingsSyncButton.textContent = result.error ? "Не удалось проверить" : "Обновления проверены";
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
  let appsSelection = {}; // stage.index -> {variant, optionalChecked: Set<path>}
  // Список необязательных APK, отмеченных техником на ЛЮБОМ "apps"-этапе —
  // общий на всю установку (аналог desktop ctx.selected_apks), т.к.
  // "usb"-этап с usb_copy_selected_apks просто копирует то, что отметили
  // раньше, независимо от того, на каком именно apps-этапе это было.
  let globalSelectedApks = new Set();
  // Общая библиотека приложений (apk/, см. python/apk_library.py) —
  // {name, description, category, remote_only, size, path}[], одна на весь
  // мастер, качается в фоне при открытии (см. Bridge.call scanner_list_apks
  // ниже) — список приходит сразу, сами .apk докачиваются точечно перед
  // adb_install_apks/usb_run_stage (см. WebBridge.kt: ensureApksDownloaded).
  let apkLibrary = [];
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
    vars = {};
    currentIndex = 0;
    stages = [];
    appsSelection = {};
    appsConnectionChoice = {};
    globalSelectedApks = new Set();
    apkLibrary = [];
    installCompletedShown = false;
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
    Bridge.call("sync_model_payload", { model_key: model.key });
    pollSyncProgress("model", modelSyncLabel, "Скачиваю файлы модели с сервера...", modelSyncBar, modelSyncFill);
    Bridge.call("scanner_list_apks", {});
  }

  function onApkLibraryResult(event) {
    apkLibrary = event.apks || [];
    // Если текущий этап как раз показывает общую библиотеку — перерисуем,
    // теперь она подъехала (список обычно приходит уже к моменту, когда
    // техник долистает до нужного этапа, но не гарантированно).
    const current = stages[currentIndex];
    if (current && (current.type === "apps" || (current.type === "usb" && current.usb_copy_selected_apks))) {
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
        { editablePort: true }
      );
      return;
    }
    setAdbStatus(false, "ADB: подключаюсь...");
    Bridge.call("adb_connect", {});
  }

  function onAdbConnectResult(event) {
    const r = event.result || {};
    if (r.connected) {
      setAdbStatus(true, `ADB: подключено (${r.banner || "ok"})`);
      log("ADB подключён.");
    } else {
      setAdbStatus(false, "ADB: не подключено");
      log(`ADB: не удалось подключиться — ${r.reason || "?"}`);
    }
  }

  function onAdbLog(event) {
    log(event.line);
  }

  function setUsbStatus(connected, text) {
    usbConnected = connected;
    usbStatusEl.textContent = text;
    usbStatusEl.classList.toggle("connected", connected);
    usbConnectBtn.textContent = connected ? "Переподключить" : "Подключить флешку";
  }

  function onUsbConnect() {
    setUsbStatus(false, "Флешка: подключаюсь...");
    Bridge.call("usb_connect", {});
  }

  function onUsbConnectResult(event) {
    const r = event.result || {};
    if (r.mounted) {
      const mb = Math.round((r.capacity || 0) / 1024 / 1024);
      setUsbStatus(true, `Флешка: подключена (${r.label || "без метки"}, ${mb}МБ)`);
      log("Флешка смонтирована.");
    } else {
      setUsbStatus(false, "Флешка: не подключена");
      log(`Флешка: не удалось подключиться — ${r.reason || "?"}`);
    }
  }

  function onUsbFormat() {
    if (!usbConnected) { log("Сначала подключи флешку."); return; }
    confirmDialog(
      "Форматировать флешку?",
      "Все данные на флешке будут БЕЗВОЗВРАТНО удалены. Продолжить?",
      () => {
        log("Форматирую флешку...");
        Bridge.call("usb_format", { label: "MAGICSQD" });
      }
    );
  }

  function onUsbFormatResult(event) {
    const r = event.result || {};
    log(r.success ? "Флешка отформатирована." : `Ошибка форматирования: ${r.reason || "?"}`);
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

  function onAdbStageResult(event) {
    const r = event.result || {};
    log(r.success ? "Этап выполнен успешно." : `Этап завершился с ошибкой: ${r.reason || "?"}`);
    // Перерисовываем текущий этап заново, если результат относится к нему —
    // сбрасывает задизейбленные во время выполнения кнопки.
    if (stages[currentIndex] && stages[currentIndex].index === event.index) {
      render();
    }
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

  // Закрываемая модалка (тап мимо карточки/системный "назад" — см.
  // window.__handleBackPress) — в отличие от promptText ("#ask" из
  // running-этапа, где Kotlin-сторона реально блокирующе ждёт ответ в
  // SynchronousQueue, закрывать её без ответа нельзя).
  function closeDismissibleModal() {
    const overlay = document.querySelector(".modal-overlay.dismissible");
    if (!overlay) return false;
    pendingScanCallback = null;
    overlay.remove();
    return true;
  }

  // opts.editablePort — показать поле порта рядом со сканом (по умолчанию
  // то, что задано в _wizard_spec.json редактором на desktop, см. onAdbConnect)
  // с возможностью поменять "на всякий случай" и пересканировать по новому
  // порту, не закрывая модалку. onSubmit(host, port) — port тот, что был
  // актуален на момент выбора адреса (изменённый техником или дефолтный).
  function promptHostPicker(title, port, onSubmit, opts) {
    opts = opts || {};
    // port может быть null (заранее не известен — см. connectionPortFor) —
    // для реального скана/подключения всё равно нужно с чего-то начать,
    // но само поле ниже покажется пустым с подсказкой, а не "5555" молча
    // выданным за реальный порт магнитолы.
    let currentPort = port || 5555;
    const overlay = el("div", { class: "modal-overlay dismissible" });
    overlay.addEventListener("click", (e) => { if (e.target === overlay) closeDismissibleModal(); });
    const listWrap = el("div", { class: "host-scan-list" });
    const input = el("input", { type: "text" });
    input.value = lastWifiHost || "";
    const submitHost = (host) => {
      pendingScanCallback = null;
      overlay.remove();
      onSubmit(host, currentPort);
    };
    const manualSubmit = () => {
      if (!input.value.trim()) return;
      submitHost(input.value.trim());
    };

    function runScan() {
      clear(listWrap);
      listWrap.appendChild(el("p", { class: "stage-text", style: "color: var(--text-dim)", text: "Сканирую сеть..." }));
      // recommended — mDNS-резолв "android.local" (см. MdnsResolve.kt):
      // именно так техники реально находят магнитолу вручную через Termux
      // ("telnet android.local" / "ping6 android.local"), поэтому это не
      // просто ещё один пункт списка, а явно выделенный рекомендованный
      // вариант НАД результатами скана порта.
      pendingScanCallback = (hosts, recommended) => {
        clear(listWrap);
        if (recommended) {
          const recBtn = el("button", { class: "accent", text: `${recommended}  (android.local, рекомендуется)` });
          recBtn.addEventListener("click", () => submitHost(recommended));
          listWrap.appendChild(recBtn);
        }
        const rest = hosts.filter((h) => h !== recommended);
        if (!rest.length) {
          if (!recommended) {
            listWrap.appendChild(el("p", {
              class: "stage-text", style: "color: var(--text-dim)",
              text: "Не нашёл устройств в сети. Введите адрес вручную ниже.",
            }));
          }
          return;
        }
        listWrap.appendChild(el("p", { class: "stage-text", style: "color: var(--text-dim)", text: "Другие устройства в сети:" }));
        rest.forEach((host) => {
          const btn = el("button", { text: host });
          btn.addEventListener("click", () => submitHost(host));
          listWrap.appendChild(btn);
        });
      };
      Bridge.call("scan_hosts", { port: currentPort });
    }

    const boxChildren = [el("p", { class: "stage-text", text: title })];
    if (opts.editablePort) {
      const portInput = el("input", {
        type: "number", class: "host-port-input",
        placeholder: port == null ? String(currentPort) : undefined,
      });
      portInput.value = port != null ? String(port) : "";
      const rescan = () => {
        const p = parseInt(portInput.value, 10);
        if (!p || p === currentPort) return;
        currentPort = p;
        runScan();
      };
      portInput.addEventListener("blur", rescan);
      portInput.addEventListener("keydown", (e) => { if (e.key === "Enter") rescan(); });
      boxChildren.push(el("div", { class: "host-port-row" }, [
        el("span", { class: "stage-text", text: "Порт:" }),
        portInput,
      ]));
    }
    boxChildren.push(
      listWrap,
      input,
      el("button", { class: "accent", text: "Подключиться по этому адресу", onclick: manualSubmit }),
    );
    const box = el("div", { class: "modal-box" }, boxChildren);
    overlay.appendChild(box);
    document.body.appendChild(overlay);
    runScan();
  }

  // Системный жест/кнопка "назад" (см. MainActivity.kt: onBackPressedDispatcher
  // зовёт это через evaluateJavascript) — всё состояние в JS, поэтому решение
  // тоже тут: закрыть модалку/лог, если открыты; иначе на шаг назад по
  // мастеру или пикеру; и только если деться больше некуда — реально выйти
  // из приложения (Kotlin делает это по возврату "exit").
  window.__handleBackPress = function () {
    if (closeDismissibleModal()) return "handled";
    if (logOverlayEl.classList.contains("open")) { setLogOpen(false); return "handled"; }
    if (screenWizard.classList.contains("active")) {
      if (currentIndex > 0) { show(prevVisibleIndex(currentIndex)); return "handled"; }
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

  function isStageVisible(stage) {
    if (!stage.condition_var) return true;
    return (stage.condition_values || []).includes(vars[stage.condition_var]);
  }

  function nextVisibleIndex(index) {
    let candidate = index + 1;
    while (candidate < stages.length && !isStageVisible(stages[candidate])) candidate += 1;
    return candidate;
  }

  function prevVisibleIndex(index) {
    let candidate = index - 1;
    while (candidate > 0 && !isStageVisible(stages[candidate])) candidate -= 1;
    return candidate;
  }

  function advanceAfter(index) {
    const nxt = nextVisibleIndex(index);
    if (nxt >= stages.length) {
      log("Все этапы установки выполнены.");
      if (!installCompletedShown && stages.length) {
        installCompletedShown = true;
        showCompletionModal();
      }
      renderNav();
      return;
    }
    show(nxt);
  }

  function show(index) {
    currentIndex = index;
    nextAction = () => advanceAfter(currentIndex);
    render();
  }

  function render() {
    clear(wizardContentEl);
    if (!stages.length) {
      wizardContentEl.appendChild(el("p", { class: "stage-text", text: "Для этой модели нет заданных этапов установки." }));
      renderNav();
      return;
    }
    renderStage(stages[currentIndex]);
    renderNav();
  }

  function renderNav() {
    wizardBackBtn.disabled = currentIndex <= 0;
    if (!stages.length) {
      wizardNextBtn.style.display = "none";
    } else {
      wizardNextBtn.style.display = "";
      wizardNextBtn.textContent = nextVisibleIndex(currentIndex) >= stages.length ? "Готово" : "Далее";
    }
    wizardPageLabel.textContent = stages.length ? `Этап ${currentIndex + 1} из ${stages.length}` : "";
  }

  function describeCommand(cmd) {
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

  function renderAdbStage(page, stage) {
    const commandsText = (stage.commands || []).map(describeCommand).filter(Boolean).join("\n");
    if (commandsText) page.appendChild(el("div", { class: "stage-commands", text: commandsText }));
    const btn = el("button", { class: "accent", text: "Выполнить" });
    btn.addEventListener("click", () => {
      if (!adbConnected) { log("Сначала подключись к ADB (кнопка вверху)."); return; }
      btn.disabled = true;
      btn.textContent = "Выполняю...";
      Bridge.call("adb_run_stage", {
        index: stage.index,
        commands: stage.commands || [],
        filesByName: filesByNameFrom(stage.adb_files),
      });
    });
    page.appendChild(btn);
  }

  function renderActionsStage(page, stage) {
    (stage.actions || []).forEach((action) => {
      const btn = el("button", { text: action.label || action.kind });
      btn.addEventListener("click", () => {
        // kind != "command" (grant_permissions/mock_location/disable_app/
        // enable_app) на desktop идёт через ctx.ask_choice + adb_permissions.py,
        // не через мини-DSL команд — action.commands там пуст. Явно говорим
        // "не поддерживается", а не молча выполняем 0 команд как "успех".
        if (action.kind && action.kind !== "command") {
          log(`Действие "${action.label}" (${action.kind}) пока не поддерживается в мобильной версии.`);
          return;
        }
        if (!adbConnected) { log("Сначала подключись к ADB (кнопка вверху)."); return; }
        log(`Выполняю действие: ${action.label}`);
        Bridge.call("adb_run_stage", {
          index: stage.index, commands: action.commands || [],
          filesByName: filesByNameFrom(action.files),
        });
      });
      page.appendChild(btn);
    });
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
  function renderApkTree(page, stage, sel) {
    function currentLists() {
      if (stage.variants && stage.variants.length) {
        const v = stage.variants.find((x) => x.name === sel.variant) || stage.variants[0];
        return { required: v.standard_apks || [], optional: v.standard_apks_optional || [] };
      }
      return { required: stage.standard_apks || [], optional: stage.standard_apks_optional || [] };
    }
    const rawLists = currentLists();
    // standard_apks/standard_apks_optional теперь {path,name,description}
    // (см. wizard_spec.py: _apk_entry — раньше "красивое" имя из редактора
    // терялось и техник видел голое имя файла) — остальной код (установка,
    // globalSelectedApks) по-прежнему работает с путями строками, поэтому
    // наружу отдаём только их, а name/description используем тут же для
    // самого рендера строк ниже.
    const lists = { required: rawLists.required.map((a) => a.path),
                     optional: rawLists.optional.map((a) => a.path) };

    function buildAppRow(apk, required, checked, onChange) {
      const row = el("li", { class: "app-choice" });
      const checkbox = el("input", { type: "checkbox" });
      checkbox.checked = checked;
      if (required) checkbox.disabled = true;
      if (onChange) checkbox.addEventListener("change", () => onChange(checkbox.checked));
      row.append(checkbox, el("span", { class: "app-choice-name", text: apk.name || basename(apk.path) }));
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
      const section = el("section", { class: `apps-section apps-section-${kind}` });
      section.appendChild(el("h3", { class: "apps-section-title", text: title }));
      const list = el("ul", { class: "stage-apps-list stage-apps-grid" });
      apks.forEach((apk) => {
        list.appendChild(buildAppRow(
          apk,
          required,
          required || selected.has(apk.path),
          (checked) => {
            if (checked) { selected.add(apk.path); globalSelectedApks.add(apk.path); }
            else { selected.delete(apk.path); globalSelectedApks.delete(apk.path); }
          },
        ));
      });
      section.appendChild(list);
      page.appendChild(section);
    }

    appendSection("Обязательные приложения", "required", rawLists.required, true, new Set());
    appendSection("Необязательные приложения", "optional", rawLists.optional, false, sel.optional);

    const showGeneralLibrary = stage.type === "apps" || (stage.type === "usb" && stage.usb_copy_selected_apks);
    if (showGeneralLibrary) {
      page.appendChild(el("h3", { class: "apps-library-title", text: "Дополнительные приложения" }));
      if (!apkLibrary.length) {
        page.appendChild(el("p", { class: "stage-text", style: "color: var(--text-dim)", text: "Загружаю список..." }));
      } else {
        const byCategory = {};
        apkLibrary.forEach((a) => { (byCategory[a.category || ""] = byCategory[a.category || ""] || []).push(a); });
        Object.keys(byCategory).sort((a, b) => a.localeCompare(b)).forEach((cat) => {
          const details = el("details", { class: "apk-category apps-section apps-section-library" });
          details.appendChild(el("summary", { text: cat || "Без категории" }));
          const ul = el("ul", { class: "stage-apps-list stage-apps-grid" });
          byCategory[cat].forEach((apk) => {
            ul.appendChild(buildAppRow(apk, false, globalSelectedApks.has(apk.path), (checked) => {
              if (checked) globalSelectedApks.add(apk.path);
              else globalSelectedApks.delete(apk.path);
            }));
          });
          details.appendChild(ul);
          page.appendChild(details);
        });
      }
    }

    return lists;
  }

  function renderAppsStage(page, stage) {
    const sel = selectionFor(stage);
    renderVariantPicker(page, stage, sel);
    const lists = renderApkTree(page, stage, sel);

    const btn = el("button", { class: "accent", text: "Установить" });
    btn.addEventListener("click", () => {
      if (!adbConnected) { log("Сначала подключись к ADB (кнопка вверху)."); return; }
      const apkPaths = Array.from(new Set(lists.required.concat(Array.from(globalSelectedApks))));
      if (!apkPaths.length) { log("Не выбрано ни одного приложения."); return; }
      btn.disabled = true;
      btn.textContent = "Устанавливаю...";
      Bridge.call("adb_install_apks", { index: stage.index, apkPaths });
    });
    page.appendChild(btn);
  }

  function renderUsbStage(page, stage) {
    const sel = selectionFor(stage);

    function currentFiles() {
      if (stage.variants && stage.variants.length) {
        const v = stage.variants.find((x) => x.name === sel.variant) || stage.variants[0];
        return v.usb_files || [];
      }
      return stage.usb_files || [];
    }

    renderVariantPicker(page, stage, sel);

    const files = currentFiles();
    if (files.length) {
      page.appendChild(el("div", { class: "stage-commands", text: files.map(basename).join("\n") }));
    }
    if (stage.usb_copy_selected_apks) {
      renderApkTree(page, stage, sel); // у usb-этапа своих standard_apks нет — фактически только общая библиотека
    }

    const btn = el("button", { class: "accent", text: "Записать на флешку" });
    btn.addEventListener("click", () => {
      if (!usbConnected) { log("Сначала подключи флешку (кнопка вверху)."); return; }
      btn.disabled = true;
      btn.textContent = "Записываю...";
      Bridge.call("usb_run_stage", {
        index: stage.index,
        files,
        sharedFolder: stage.usb_shared_folder || "",
        selectedApks: stage.usb_copy_selected_apks ? Array.from(globalSelectedApks) : [],
        apksDest: stage.usb_apks_dest || "",
      });
    });
    page.appendChild(btn);
  }

  // "telnet"-этап: включает ADB-отладку на магнитоле удалённо (см.
  // TelnetAdb.kt, порт cars/_shared/telnet_adb.py) — своё, отдельное от
  // AdbSession TCP-соединение на каждую команду, поэтому не завязан на
  // верхнюю ADB-панель (та для проводного/Wi-Fi ADB, это другое). commands
  // — сырые строки (не DSL, см. wizard_spec.py), каждая шлётся отдельным
  // telnet-подключением.
  function renderTelnetStage(page, stage) {
    if ((stage.commands || []).length) {
      page.appendChild(el("div", { class: "stage-commands", text: stage.commands.join("\n") }));
    }
    const btn = el("button", { class: "accent", text: "Выполнить (telnet)" });
    btn.addEventListener("click", () => {
      promptHostPicker("Адрес магнитолы для telnet (порт 23):", 23, (host) => {
        lastWifiHost = host;
        btn.disabled = true;
        btn.textContent = "Выполняю...";
        Bridge.call("telnet_run_stage", { index: stage.index, host, commands: stage.commands || [] });
      });
    });
    page.appendChild(btn);
  }

  function updateTransportBars(stage) {
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
    const page = el("div", { class: "stage-page" });
    page.appendChild(el("div", { class: "stage-chip", text: (stage.title || stage.type || "").toUpperCase() }));
    if (stage.description) {
      page.appendChild(el("div", { class: "stage-text", text: stage.description }));
    }
    if (!isStageVisible(stage)) {
      page.appendChild(el("div", { class: "stage-text", style: "color: var(--text-dim)", text: "Этот этап можно пропустить при текущем выборе." }));
    }

    nextAction = () => advanceAfter(stage.index);

    if (stage.type === "check") {
      const options = stage.check_options || [];
      const select = el("select", {}, options.map((o) => el("option", { value: o, text: o })));
      page.appendChild(select);
      nextAction = () => {
        if (stage.check_var) vars[stage.check_var] = select.value;
        log(`Проверка: ${stage.check_var} = ${select.value}`);
        advanceAfter(stage.index);
      };
    } else if (stage.type === "manual") {
      page.appendChild(el("p", { class: "stage-text", text: "Выполните шаги из инструкции на самой магнитоле, затем нажмите «Далее»." }));
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
      if (stage.instruction_html) {
        const iframe = el("iframe", { class: "stage-instruction-frame", scrolling: "no" });
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
              const height = Math.max(doc.documentElement.scrollHeight, doc.body ? doc.body.scrollHeight : 0);
              iframe.style.height = `${height}px`;
            };
            syncHeight();
            // ResizeObserver подхватывает досрочно занятую высоту (позднюю
            // подгрузку картинок/веб-шрифтов) без повторного load-события.
            if (window.ResizeObserver) {
              new ResizeObserver(syncHeight).observe(doc.documentElement);
            }
          } catch (e) { /* останется дефолтная высота — не критично */ }
        });
        page.appendChild(iframe);
        iframe.srcdoc = stage.instruction_html;
      }
    } else if (stage.type === "adb") {
      renderAdbStage(page, stage);
    } else if (stage.type === "apps") {
      renderAppsStage(page, stage);
    } else if (stage.type === "actions") {
      renderActionsStage(page, stage);
    } else if (stage.type === "usb") {
      renderUsbStage(page, stage);
    } else if (stage.type === "telnet") {
      renderTelnetStage(page, stage);
    } else if (!stage.supported) {
      page.appendChild(el("div", {
        class: "stage-text", style: "color: var(--danger)",
        text: `Тип этапа "${stage.type}" пока не поддерживается в мобильной версии — можно только пропустить.`,
      }));
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

  function showSettingsModal() {
    const formatBytes = (value) => {
      const units = ["Б", "КБ", "МБ", "ГБ"]; let size = value || 0; let index = 0;
      while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
      return `${size >= 100 || index === 0 ? Math.round(size) : size.toFixed(1)} ${units[index]}`;
    };
    const info = Bridge.call("settings_info", {});
    let overlay;
    const cacheLabel = el("span", { text: formatBytes(info.cache_bytes) });
    const makeToggle = (text, key) => {
      const input = el("input", { type: "checkbox" }); input.checked = !!info.preferences[key];
      input.addEventListener("change", () => {
        const preferences = Bridge.call("settings_set_preferences", { [key]: input.checked });
        document.documentElement.classList.toggle("reduce-motion", preferences.reduced_motion);
        document.getElementById("app").classList.toggle("compact-log", preferences.compact_log);
      });
      return el("label", { class: "settings-toggle" }, [input, el("span", { text })]);
    };
    const clear = el("button", { class: "danger", text: "Очистить кэш" });
    clear.addEventListener("click", () => {
      // Android WebView не показывает нативный confirm() без отдельного
      // WebChromeClient; кнопка уже однозначно подписана и очищает только
      // безопасный перекачиваемый кэш, поэтому дополнительный диалог тут
      // намеренно не нужен.
      const result = Bridge.call("settings_clear_cache", {});
      cacheLabel.textContent = formatBytes(result.remaining_bytes);
      clear.textContent = `Освобождено: ${formatBytes(result.freed_bytes)}`;
    });
    const sync = el("button", { text: "Проверить обновления сейчас" });
    sync.addEventListener("click", () => {
      sync.disabled = true;
      sync.textContent = "Проверяем…";
      settingsSyncButton = sync;
      if (settingsSyncTimeout) clearTimeout(settingsSyncTimeout);
      settingsSyncTimeout = setTimeout(() => {
        if (settingsSyncButton !== sync) return;
        sync.disabled = false;
        sync.textContent = "Не удалось проверить";
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
        sync.textContent = "Не удалось проверить";
        console.error("Не удалось запустить проверку обновлений:", error);
      }
    });
    const copyLog = el("button", { text: "Скопировать лог" });
    copyLog.addEventListener("click", async () => { try { await navigator.clipboard.writeText(logPanelEl.innerText); copyLog.textContent = "Лог скопирован"; } catch (_) { log("Не удалось скопировать лог автоматически."); } });
    const close = el("button", { class: "settings-close-mobile", type: "button", text: "Закрыть" });
    close.addEventListener("click", () => overlay.remove());
    overlay = showModal([
      el("img", { class: "modal-logo", src: "img/logo-full-dark.svg", alt: "Magic SQD" }),
      el("div", { class: "settings-modal-header" }, [el("p", { class: "stage-text", style: "font-weight: 650; font-size: 17px", text: "Настройки" }), close]),
      el("section", { class: "settings-section" }, [el("strong", { text: "Хранилище" }), el("span", { class: "settings-muted", text: `Приложение: ${formatBytes(info.app_bytes)} · кэш: ` }), cacheLabel, clear, el("small", { text: "Сценарии и настройки останутся на месте." })]),
      el("section", { class: "settings-section" }, [el("strong", { text: "Синхронизация" }), el("span", { class: "settings-muted", text: "Сервер подключён" }), sync, makeToggle("Обновлять каталог при запуске", "auto_sync")]),
      el("section", { class: "settings-section" }, [el("strong", { text: "Интерфейс" }), makeToggle("Уменьшить анимации", "reduced_motion"), makeToggle("Не выключать экран во время работы", "keep_screen_on"), makeToggle("Компактный лог", "compact_log")]),
      el("section", { class: "settings-section" }, [el("strong", { text: "Диагностика" }), copyLog, el("a", { href: "https://github.com/torisar93/magic_sqd", target: "_blank", text: "GitHub проекта" })]),
      el("button", { class: "accent", text: "Готово", onclick: () => overlay.remove() }),
    ]);
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
      el("p", { class: "stage-text", style: "font-weight: 600; font-size: 19px", text: "Готово!" }),
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
    wizardContentEl = document.getElementById("wizard-content");
    wizardBackBtn = document.getElementById("wizard-back");
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
    logCollapseBtn = document.getElementById("log-collapse-btn");
    logCmdInput = document.getElementById("log-cmd-input");
    logCmdRunBtn = document.getElementById("log-cmd-run");

    wizardBackBtn.addEventListener("click", () => show(prevVisibleIndex(currentIndex)));
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
    logOverlayEl.addEventListener("click", (e) => { if (e.target === logOverlayEl) setLogOpen(false); });
    logCmdRunBtn.addEventListener("click", onLogCmdRun);
    logCmdInput.addEventListener("keydown", (e) => { if (e.key === "Enter") onLogCmdRun(); });
    // Фокус в поле ввода команды сам открывает лог, если он ещё свёрнут —
    // та же идея, что и в desktop-версии (см. app/web/frontend/js/app.js),
    // но проще: лог здесь и так модальный оверлей с закрытием по клику на
    // затемнённый фон (см. logOverlayEl выше) — эта же кнопка "закрыть
    // куда-то мимо" уже работает одинаково независимо от того, открыли лог
    // вручную или так, отдельного "автозакрытия" не нужно.
    logCmdInput.addEventListener("focus", () => { setLogOpen(true); updateLogCmdSuggestions(); });
    logCmdInput.addEventListener("blur", () => {
      // Небольшая задержка — подстраховка для выбора подсказки тачем/
      // клавиатурной навигацией без mousedown (см. initLogCmdSuggestions
      // выше — там основной путь для мыши/тача).
      setTimeout(hideLogCmdSuggestions, 150);
    });
    initLogCmdSuggestions();
    window.events.on("network_scan_result", onNetworkScanResult);
    window.events.on("sync_finished", onSyncFinished);
    window.events.on("model_sync_finished", onModelSyncFinished);
    window.events.on("adb_connect_result", onAdbConnectResult);
    window.events.on("adb_log", onAdbLog);
    window.events.on("adb_ask_input", onAdbAskInput);
    window.events.on("adb_stage_result", onAdbStageResult);
    window.events.on("usb_connect_result", onUsbConnectResult);
    window.events.on("usb_format_result", onUsbFormatResult);
    window.events.on("apk_library_result", onApkLibraryResult);
    window.events.on("update_check_result", onUpdateCheckResult);

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
