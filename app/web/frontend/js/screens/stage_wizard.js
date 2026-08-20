// Портировано с app/stage_wizard.py — постраничный мастер "инструкция +
// этапы установки" в одном виджете. Разница с прежним интерфейсом: условная
// видимость этапов и HTML-инструкция остались как были, а сам рендеринг —
// обычные DOM-узлы/CSS вместо CTk-виджетов и tkinterweb HtmlFrame (см. план
// миграции). Один экземпляр состояния на выбранную модель — open() полностью
// сбрасывает его, как раньше пересоздавался StageWizard при смене модели.
(function () {
  const { el, clear } = window.dom;

  const TYPE_LABELS = {
    usb: "USB-флешка", adb: "ADB", manual: "Вручную на магнитоле",
    apps: "Выбор приложений", exe: "Готовый установщик (.exe)",
    check: "Проверка/выбор", instruction: "Инструкция", uart: "UART", telnet: "Telnet",
    actions: "Доп. команды",
  };

  let containerEl, contentEl, navBackBtn, navNextBtn, navLabelEl;
  let mounted = false;
  let logFn = () => {};

  let model = null;
  let stages = [];
  let loadError = null;
  let hasIntro = false;
  let currentIndex = 0;
  const done = new Set();
  let vars = {};
  let chosenVariants = {};
  let appSelection = {};
  const sectionCollapsed = {};
  let nextAction = () => advanceAfter(currentIndex);
  let sharedApksPromise = null;
  let runnerBusy = false;

  function log(message) {
    logFn(`[${model.display_label}] ${message}`);
  }

  // -- инициализация экрана (один раз, до выбора модели) ------------------
  function init(container, logCallback) {
    containerEl = container;
    logFn = logCallback;

    // Глобальные события фонового InstallRunner — на всё приложение, а не
    // при каждом open()/render(): воркер-поток на стороне Python один на
    // всю программу (см. app/web/api/install_api.py), слушатель тоже нужен
    // только один, иначе он задваивался бы при каждом выборе модели.
    window.events.on("install_log", (event) => log(event.text));
    window.events.on("install_finished", onInstallFinished);
    window.events.on("ask_input", (event) => showAskInputDialog(event));
  }

  // -- построение разметки (один раз, лениво — при первом open(), чтобы
  // плейсхолдер "выберите марку и модель" не пропадал раньше времени) -----
  function ensureMounted() {
    if (mounted) return;
    mounted = true;
    const container = containerEl;
    container.innerHTML = `
      <div class="wizard">
        <div class="wizard-content" id="wizard-content"></div>
        <div class="wizard-nav">
          <button id="wizard-back">← Назад</button>
          <span class="page-label" id="wizard-page-label"></span>
          <button id="wizard-next" class="accent">Далее →</button>
        </div>
      </div>
      <dialog id="ask-input-dialog">
        <form method="dialog" id="ask-input-form">
          <p id="ask-input-prompt"></p>
          <select id="ask-input-choices" style="width: 100%; margin-bottom: 10px; display: none"></select>
          <input type="text" id="ask-input-value" style="width: 100%; margin-bottom: 10px" />
          <div style="display: flex; justify-content: flex-end; gap: 6px">
            <button type="button" id="ask-input-cancel">Отмена</button>
            <button type="submit" class="accent">OK</button>
          </div>
        </form>
      </dialog>
    `;
    contentEl = container.querySelector("#wizard-content");
    navBackBtn = container.querySelector("#wizard-back");
    navNextBtn = container.querySelector("#wizard-next");
    navLabelEl = container.querySelector("#wizard-page-label");
    navBackBtn.addEventListener("click", goBack);
    navNextBtn.addEventListener("click", () => nextAction());
    setupAskInputDialog(container);
  }

  // Значение <option>, ведущее к ручному вводу — рядом со списком найденных
  // сканом вариантов (см. ctx.ask_choice в install_context.py) всегда есть
  // возможность вписать своё, на случай если нужного нет в списке.
  const MANUAL_CHOICE_VALUE = "__manual__";

  function setupAskInputDialog(container) {
    const dialog = container.querySelector("#ask-input-dialog");
    const form = container.querySelector("#ask-input-form");
    const cancelBtn = container.querySelector("#ask-input-cancel");
    const select = container.querySelector("#ask-input-choices");
    const valueInput = container.querySelector("#ask-input-value");
    select.addEventListener("change", () => {
      const manual = select.value === MANUAL_CHOICE_VALUE;
      valueInput.style.display = manual ? "" : "none";
      if (manual) valueInput.focus();
    });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const usingChoices = select.style.display !== "none";
      const value = (usingChoices && select.value !== MANUAL_CHOICE_VALUE)
        ? select.value
        : valueInput.value;
      const reqId = dialog.dataset.reqId;
      dialog.close();
      window.pywebview.api.install_answer_input(reqId, value || null);
    });
    cancelBtn.addEventListener("click", () => {
      const reqId = dialog.dataset.reqId;
      dialog.close();
      window.pywebview.api.install_answer_input(reqId, null);
    });
  }

  function showAskInputDialog(event) {
    const dialog = document.getElementById("ask-input-dialog");
    dialog.dataset.reqId = event.id;
    document.getElementById("ask-input-prompt").textContent = event.prompt;
    const select = document.getElementById("ask-input-choices");
    const valueInput = document.getElementById("ask-input-value");
    valueInput.value = "";
    const choices = event.choices || [];
    if (choices.length) {
      select.innerHTML = "";
      for (const choice of choices) {
        const opt = document.createElement("option");
        opt.value = choice;
        opt.textContent = choice;
        select.appendChild(opt);
      }
      if (event.allow_manual !== false) {
        const manualOpt = document.createElement("option");
        manualOpt.value = MANUAL_CHOICE_VALUE;
        manualOpt.textContent = "Ввести вручную...";
        select.appendChild(manualOpt);
      }
      select.style.display = "";
      select.value = choices[0];
      valueInput.style.display = "none";
    } else {
      select.style.display = "none";
      select.innerHTML = "";
      valueInput.style.display = "";
    }
    dialog.showModal();
  }

  function onInstallFinished(event) {
    runnerBusy = false;
    log(event.message);
    if (event.stage_index !== currentIndex) return; // ушли с этой страницы, пока этап работал в фоне
    // "actions" — кнопки необязательны и нажимаются в любом порядке/сколько
    // угодно раз, поэтому в отличие от остальных типов этапов ни успех, ни
    // ошибка одного действия не переводят на следующий этап сами по себе —
    // технику решает об этом сам, нажав "Далее".
    if (stages[currentIndex] && stages[currentIndex].type === "actions") {
      render();
      return;
    }
    if (event.success) {
      advanceAfter(currentIndex);
    } else {
      render(); // перерисовать текущий этап заново — Start/Stop вернутся в состояние "не выполняется"
    }
  }

  // -- открытие модели --------------------------------------------------
  async function open(selectedModel) {
    ensureMounted();
    model = selectedModel;
    done.clear();
    vars = {};
    chosenVariants = {};
    appSelection = {};
    hasIntro = model.no_instruction;
    loadError = null;
    stages = [];

    const result = await window.pywebview.api.install_load_stages(model.key);
    if (result.error) {
      loadError = result.error;
    } else {
      stages = result.stages;
      await initAppSelectionDefaults();
    }

    currentIndex = hasIntro ? -1 : 0;
    render();
  }

  async function initAppSelectionDefaults() {
    for (const stage of stages) {
      if (stage.type !== "apps") continue;
      const standard = await window.pywebview.api.install_standard_apks(model.key, stage.index, null);
      // required — не чекбокс вовсе (см. buildAppRow), но appSelection всё
      // равно держит их как true — этим же словарём собирается финальный
      // список APK на установку (см. selectedApkPaths), не отдельным путём.
      for (const apk of standard.required) appSelection[apk.path] = true;
      for (const apk of standard.optional) {
        if (!(apk.path in appSelection)) appSelection[apk.path] = true;
      }
    }
    for (const apk of await sharedApks()) {
      if (!(apk.path in appSelection)) appSelection[apk.path] = false;
    }
  }

  function sharedApks() {
    if (!sharedApksPromise) sharedApksPromise = window.pywebview.api.scanner_list_apks();
    return sharedApksPromise;
  }

  // -- навигация ----------------------------------------------------------
  function isStageVisible(stage) {
    if (!stage.condition_var) return true;
    return (stage.condition_values || []).includes(vars[stage.condition_var]);
  }

  function firstPageIndex() {
    return hasIntro ? -1 : 0;
  }

  function nextVisibleIndex(index) {
    let candidate = index + 1;
    while (candidate < stages.length && !isStageVisible(stages[candidate])) candidate += 1;
    return candidate;
  }

  function prevVisibleIndex(index) {
    const floor = firstPageIndex();
    let candidate = index - 1;
    while (candidate > floor && !isStageVisible(stages[candidate])) candidate -= 1;
    return candidate;
  }

  function goBack() {
    show(prevVisibleIndex(currentIndex));
  }

  function advanceAfter(index) {
    if (index >= 0) done.add(index);
    const nxt = nextVisibleIndex(index);
    if (nxt >= stages.length) {
      renderNav();
      if (stages.length) log("Все этапы установки выполнены.");
      return;
    }
    show(nxt);
  }

  function show(index) {
    currentIndex = index;
    nextAction = () => advanceAfter(currentIndex);
    render();
  }

  // -- рендеринг ------------------------------------------------------
  function render() {
    clear(contentEl);
    if (loadError) {
      contentEl.appendChild(el("div", { class: "callout danger", text: loadError }));
      renderNav();
      return;
    }
    if (currentIndex === -1) {
      renderIntroPage();
    } else if (stages.length === 0) {
      contentEl.appendChild(el("p", { class: "placeholder-text", text: "Для этой модели нет заданных этапов установки." }));
    } else {
      renderStagePage(stages[currentIndex]);
    }
    renderNav();
  }

  function renderNav() {
    navBackBtn.disabled = currentIndex <= firstPageIndex();
    if (stages.length === 0) {
      navNextBtn.style.display = "none";
    } else {
      navNextBtn.style.display = "";
      const isLast = nextVisibleIndex(currentIndex) >= stages.length;
      navNextBtn.textContent = isLast ? "Готово" : "Далее →";
    }
    if (!stages.length) navLabelEl.textContent = "";
    else if (currentIndex === -1) navLabelEl.textContent = "Инструкция";
    else navLabelEl.textContent = `Этап ${currentIndex + 1} из ${stages.length}`;
  }

  function renderIntroPage() {
    contentEl.appendChild(el("div", { class: "instruction-block" }, [
      el("div", { class: "plain-text" }, [
        el("p", { text: "Для этой машины пока нет известных способов установки." }),
        el("p", {
          style: "color: var(--text-dim)",
          text: "Если вы знаете рабочий способ получить доступ к ADB или поставить приложения — нажмите «Сообщить о проблеме» в углу и опишите его, мы добавим инструкцию.",
        }),
      ]),
    ]));
  }

  function renderStagePage(stage) {
    if (stage.type === "instruction") {
      contentEl.appendChild(buildInstructionBlock(stage, true));
      return;
    }

    if (stage.instruction_html || stage.description) {
      contentEl.appendChild(buildInstructionBlock(stage, false));
    }

    const panel = buildActionPanel(stage.type);
    if (!isStageVisible(stage)) {
      panel.appendChild(el("div", {
        class: "callout",
        text: "Этот этап не требуется при текущем выборе на этапе проверки — можно пропустить («Далее») или всё равно выполнить вручную.",
      }));
    }

    const builders = {
      check: renderCheckStage, apps: renderAppsStage, manual: renderManualStage,
      usb: renderUsbStage, exe: renderExeStage, adb: renderAdbStage, uart: renderUartStage,
      telnet: renderTelnetStage, actions: renderActionsStage,
    };
    (builders[stage.type] || (() => {}))(panel, stage);
    contentEl.appendChild(panel);
  }

  function buildInstructionBlock(stage, fullPage) {
    const html = stage.instruction_html;
    const block = el("div", { class: "instruction-block", style: fullPage ? "flex: 1; display: flex; flex-direction: column" : "" });
    if (html) {
      // allow-popups(-to-escape-sandbox) — чтобы ссылки на источники
      // ("Источники: drive2.ru/...", см. app/instruction_html.py:_linkify)
      // открывались в системном браузере по клику, а не заменяли собой
      // саму инструкцию в этом iframe. allow-scripts — чтобы работали
      // "html"-блоки со своим JS (см. instruction_editor.js: калькулятор
      // кода инженерного меню по текущей дате во freetuga-моделях и т.п.)
      // — БЕЗ allow-same-origin, поэтому у srcdoc-документа всегда opaque
      // origin: скрипт может делать что угодно ВНУТРИ себя, но не видит
      // window.parent/pywebview.api, куки или что-либо ещё хоста. Формы и
      // top-navigation по прежнему запрещены.
      const iframe = el("iframe", { sandbox: "allow-scripts allow-popups allow-popups-to-escape-sandbox" });
      if (fullPage) iframe.style.height = "100%";
      block.appendChild(iframe);
      // srcdoc не всегда успевает попасть в атрибут при быстрой пересборке — пишем через contentWindow.
      iframe.addEventListener("load", () => {}, { once: true });
      iframe.srcdoc = html;
    } else if (stage.description) {
      block.appendChild(el("div", { class: "plain-text", text: stage.description }));
    } else {
      block.appendChild(el("div", { class: "plain-text", text: "Для этого этапа нет отдельной инструкции." }));
    }
    return block;
  }

  function buildActionPanel(stageType) {
    const panel = el("div", { class: "action-panel" });
    panel.appendChild(el("div", { class: "action-chip", text: (TYPE_LABELS[stageType] || "").toUpperCase() }));
    return panel;
  }

  function buildVariantPicker(panel, stage, index) {
    const variantNames = stage.variant_names || [];
    if (!variantNames.length) return;
    const current = chosenVariants[index] || variantNames[0];
    chosenVariants[index] = current;

    const wrap = el("div", { style: "margin-bottom: 10px" });
    wrap.appendChild(el("span", { class: "field-label", text: "Вариант" }));
    const select = el("select", { style: "width: 100%" },
      variantNames.map((name) => el("option", { value: name, text: name, selected: name === current ? "" : null })));
    select.addEventListener("change", async () => {
      chosenVariants[index] = select.value;
      if (stage.type === "apps") {
        const standard = await window.pywebview.api.install_standard_apks(model.key, index, select.value);
        for (const apk of standard.required) appSelection[apk.path] = true;
        for (const apk of standard.optional) {
          if (!(apk.path in appSelection)) appSelection[apk.path] = true;
        }
      }
      render();
    });
    wrap.appendChild(select);
    panel.appendChild(wrap);
  }

  // -- manual ----------------------------------------------------------
  function renderManualStage(panel) {
    panel.appendChild(el("p", { text: "Выполните шаги из инструкции на самой магнитоле, затем нажмите «Далее»." }));
  }

  // -- check -------------------------------------------------------------
  function renderCheckStage(panel, stage) {
    const checkVar = stage.check_var || "";
    const options = stage.check_options || [];
    const current = vars[checkVar] || options[0] || "";
    panel.appendChild(el("span", { class: "field-label", text: "Выберите значение" }));
    const select = el("select", { style: "width: 100%" },
      options.map((opt) => el("option", { value: opt, text: opt, selected: opt === current ? "" : null })));
    panel.appendChild(select);
    nextAction = () => {
      if (checkVar) vars[checkVar] = select.value;
      advanceAfter(stage.index);
    };
  }

  // -- apps ----------------------------------------------------------------
  async function renderAppsStage(panel, stage) {
    buildVariantPicker(panel, stage, stage.index);
    panel.appendChild(await buildAppsTree(stage));
  }

  // Общее дерево "Стандартные приложения этого этапа" + "Дополнительные
  // приложения" (вся общая библиотека apk/, по категориям) — используется и
  // "apps"-этапом (единственный способ установить что-либо), и "usb"-этапом
  // с usb_copy_selected_apks (технику нужно видеть и отмечать те же самые
  // галочки, чтобы выбрать, что скопировать на флешку вместе со скриптом).
  async function buildAppsTree(stage) {
    const tree = el("div", { class: "apps-tree" });

    // Сверху вниз: обязательные (всегда ставятся, без чекбокса) →
    // необязательные этой машины (чекбоксом, техник решает сам) →
    // дополнительные из общей библиотеки apk/ (см. buildAppRow/
    // buildCollapsibleSection ниже — required=true рисует уже отмеченный и
    // задизейбленный чекбокс, чтобы визуально было видно, что это тоже
    // приложение, просто без права его снять).
    const standard = await window.pywebview.api.install_standard_apks(model.key, stage.index, chosenVariants[stage.index]);
    for (const apk of standard.required) appSelection[apk.path] = true;
    for (const apk of standard.optional) {
      if (!(apk.path in appSelection)) appSelection[apk.path] = true;
    }
    if (standard.required.length) {
      tree.appendChild(buildCollapsibleSection("standard-required", "Обязательные приложения", standard.required, null, true));
    }
    if (standard.optional.length) {
      tree.appendChild(buildCollapsibleSection("standard-optional", "Необязательные приложения", standard.optional));
    }

    const shared = await sharedApks();
    const byCategory = {};
    for (const apk of shared) {
      // Не ||= — см. events.js за тем же обоснованием (старый Chromium в
      // Qt5/PySide2-сборке не умеет логические операторы присваивания).
      if (!byCategory[apk.category]) byCategory[apk.category] = [];
      byCategory[apk.category].push(apk);
    }
    const extraBody = el("div");
    if (!Object.keys(byCategory).length) {
      extraBody.appendChild(el("p", { class: "app-desc", text: "Нет APK в папке apk/" }));
    }
    const categories = Object.keys(byCategory).sort((a, b) => (a === "") - (b === "") || a.localeCompare(b));
    for (const category of categories) {
      extraBody.appendChild(buildCollapsibleSection(`extra:${category}`, category || "Без категории", byCategory[category]));
    }
    tree.appendChild(buildCollapsibleSection("extra", "Дополнительные приложения", null, extraBody));
    return tree;
  }

  function buildCollapsibleSection(key, title, apks, presetBody, required) {
    const collapsed = sectionCollapsed[key] || false;
    const wrap = el("div");
    const header = el("div", { class: "apps-section-header" }, [
      el("span", { text: collapsed ? "▸" : "▾" }),
      el("span", { text: title }),
    ]);
    const body = presetBody || el("div");
    body.classList.add("apps-section-body");
    if (collapsed) body.classList.add("collapsed");
    header.addEventListener("click", () => {
      sectionCollapsed[key] = !sectionCollapsed[key];
      body.classList.toggle("collapsed");
      header.firstChild.textContent = sectionCollapsed[key] ? "▸" : "▾";
    });
    wrap.appendChild(header);
    wrap.appendChild(body);
    if (apks) {
      for (const apk of apks) body.appendChild(buildAppRow(apk, required));
    }
    return wrap;
  }

  // required — обязательное приложение этой машины (StepSpec.standard_apks,
  // см. car_generator.py): чекбокс показывается уже отмеченным и
  // задизейбленным — техник видит, что оно будет установлено, но не может
  // его снять (appSelection для него и так всегда true — выставляется в
  // buildAppsTree/initAppSelectionDefaults, не через этот чекбокс).
  function buildAppRow(apk, required) {
    const row = el("div", { class: "app-row" });
    const checkbox = el("input", { type: "checkbox" });
    if (required) {
      checkbox.checked = true;
      checkbox.disabled = true;
    } else {
      checkbox.checked = !!appSelection[apk.path];
      checkbox.addEventListener("change", () => { appSelection[apk.path] = checkbox.checked; });
    }
    const label = apk.name + (apk.remote_only ? "  ⬇ (будет скачан)" : "");
    const wrap = el("div", {}, [
      el("label", { class: "row" }, [checkbox, el("span", { text: label, style: apk.remote_only ? "color: var(--text-dim)" : "" })]),
      apk.description ? el("div", { class: "app-desc", text: apk.description }) : null,
    ]);
    row.appendChild(wrap);
    return row;
  }

  function selectedApkPaths() {
    return Object.entries(appSelection).filter(([, checked]) => checked).map(([path]) => path);
  }

  // -- usb ------------------------------------------------------------------
  async function renderUsbStage(panel, stage) {
    buildVariantPicker(panel, stage, stage.index);
    if (stage.usb_copy_selected_apks) {
      panel.appendChild(await buildAppsTree(stage));
    }
    panel.appendChild(el("button", {
      class: "accent",
      text: "Подготовить флешку для этого этапа...",
      onclick: () => window.usbDialog.open({
        modelKey: model.key,
        stageIndex: stage.index,
        variant: chosenVariants[stage.index],
        selectedApkPaths: selectedApkPaths(),
        titleSuffix: `${model.display_label} — ${stage.title}`,
        onFinished: (success) => {
          if (success) advanceAfter(stage.index);
        },
      }),
    }));
  }

  // -- exe --------------------------------------------------------------
  function renderExeStage(panel, stage) {
    panel.appendChild(el("p", {
      text: `Для этой модели готовый установщик — ${stage.exe_name}. Запустите его и завершите установку в нём самостоятельно, затем нажмите «Далее».`,
    }));
    if (!stage.exe_exists) {
      panel.appendChild(el("div", { class: "callout danger", text: `Файл не найден: ${stage.exe_path}` }));
    }
    panel.appendChild(el("button", {
      class: "accent",
      text: `Запустить ${stage.exe_name}`,
      disabled: stage.exe_exists ? null : "",
      onclick: async () => {
        const result = await window.pywebview.api.install_run_exe(stage.exe_path);
        if (result.ok) log(`Запущен ${stage.exe_name} — завершите установку в открывшемся окне.`);
        else log(result.error);
      },
    }));
  }

  // -- adb ------------------------------------------------------------------
  async function renderAdbStage(panel, stage) {
    panel.appendChild(el("span", { class: "field-label", text: "Устройство" }));
    const row = el("div", { class: "row" });
    const select = el("select", { style: "flex: 1" });
    row.appendChild(select);
    const refreshBtn = el("button", { text: "Обновить" });
    row.appendChild(refreshBtn);
    panel.appendChild(row);

    let deviceByLabel = {};
    async function refreshDevices() {
      const devices = await window.pywebview.api.install_list_devices();
      deviceByLabel = {};
      clear(select);
      for (const d of devices) {
        let label = d.serial;
        if (d.model) label += `  (${d.model})`;
        if (d.state !== "device") label += `  [${d.state}]`;
        deviceByLabel[label] = d.state === "device" ? d.serial : null;
        select.appendChild(el("option", { value: label, text: label }));
      }
    }
    refreshBtn.addEventListener("click", refreshDevices);
    await refreshDevices();

    const btnRow = el("div", { class: "row", style: "margin-top: 12px" });
    const startBtn = el("button", { class: "accent", text: "Начать этот этап" });
    const stopBtn = el("button", { class: "danger", text: "Стоп", disabled: runnerBusy ? "" : null });
    if (runnerBusy) startBtn.disabled = true;
    btnRow.appendChild(startBtn);
    btnRow.appendChild(stopBtn);
    panel.appendChild(btnRow);

    startBtn.addEventListener("click", async () => {
      const device = deviceByLabel[select.value];
      if (!device && !(await window.confirmDialog("Не выбрано подключённое устройство ADB. Продолжить всё равно?"))) return;
      startBtn.disabled = true;
      stopBtn.disabled = false;
      runnerBusy = true;
      const result = await window.pywebview.api.install_start_stage(model.key, stage.index, device, selectedApkPaths());
      if (!result.ok) {
        runnerBusy = false;
        startBtn.disabled = false;
        stopBtn.disabled = true;
        log(result.error);
      }
    });
    stopBtn.addEventListener("click", () => window.pywebview.api.install_cancel_stage());
  }

  // -- uart -------------------------------------------------------------
  // В отличие от adb-этапа, тут не нужен выбор ADB-устройства — подключение
  // идёт по последовательному порту (COM), который сам этап (см.
  // cars/_shared/uart_adb.py:open_uart, ctx.ask_choice) находит
  // автоматически или предлагает выбрать во время выполнения.
  function renderUartStage(panel, stage) {
    panel.appendChild(el("p", {
      text: "COM-порт для UART определяется автоматически (или предлагается выбрать, если их несколько) во время выполнения этого этапа — устройство ADB для него не требуется.",
    }));
    const btnRow = el("div", { class: "row", style: "margin-top: 12px" });
    const startBtn = el("button", { class: "accent", text: "Начать этот этап" });
    const stopBtn = el("button", { class: "danger", text: "Стоп", disabled: runnerBusy ? "" : null });
    if (runnerBusy) startBtn.disabled = true;
    btnRow.appendChild(startBtn);
    btnRow.appendChild(stopBtn);
    panel.appendChild(btnRow);

    startBtn.addEventListener("click", async () => {
      startBtn.disabled = true;
      stopBtn.disabled = false;
      runnerBusy = true;
      const result = await window.pywebview.api.install_start_stage(model.key, stage.index, null, selectedApkPaths());
      if (!result.ok) {
        runnerBusy = false;
        startBtn.disabled = false;
        stopBtn.disabled = true;
        log(result.error);
      }
    });
    stopBtn.addEventListener("click", () => window.pywebview.api.install_cancel_stage());
  }

  // -- telnet -------------------------------------------------------------
  // Как и uart-этап — без выбора ADB-устройства: IPv6-адрес находится сам
  // (см. cars/_shared/telnet_adb.py:scan_ipv6_neighbors, ctx.ask_choice)
  // или предлагается выбрать/ввести во время выполнения этапа.
  function renderTelnetStage(panel, stage) {
    panel.appendChild(el("p", {
      text: "IPv6-адрес магнитолы определяется автоматически (или предлагается выбрать/ввести) во время выполнения этого этапа — устройство ADB для него не требуется.",
    }));
    const btnRow = el("div", { class: "row", style: "margin-top: 12px" });
    const startBtn = el("button", { class: "accent", text: "Начать этот этап" });
    const stopBtn = el("button", { class: "danger", text: "Стоп", disabled: runnerBusy ? "" : null });
    if (runnerBusy) startBtn.disabled = true;
    btnRow.appendChild(startBtn);
    btnRow.appendChild(stopBtn);
    panel.appendChild(btnRow);

    startBtn.addEventListener("click", async () => {
      startBtn.disabled = true;
      stopBtn.disabled = false;
      runnerBusy = true;
      const result = await window.pywebview.api.install_start_stage(model.key, stage.index, null, selectedApkPaths());
      if (!result.ok) {
        runnerBusy = false;
        startBtn.disabled = false;
        stopBtn.disabled = true;
        log(result.error);
      }
    });
    stopBtn.addEventListener("click", () => window.pywebview.api.install_cancel_stage());
  }

  // -- actions --------------------------------------------------------------
  // В отличие от остальных этапов с run — тут не одна кнопка "Начать этап", а
  // по кнопке на каждое действие (StepSpec.actions в car_generator.py),
  // технику можно нажимать их в любом порядке и по несколько раз (см.
  // onInstallFinished — успех/ошибка действия не переводит на следующий этап
  // сами по себе). Нужно ADB-устройство, как и у "adb"-этапа — команды/выдача
  // разрешений/фиктивные местоположения все идут через ctx.shell.
  async function renderActionsStage(panel, stage) {
    panel.appendChild(el("span", { class: "field-label", text: "Устройство" }));
    const row = el("div", { class: "row" });
    const select = el("select", { style: "flex: 1" });
    row.appendChild(select);
    const refreshBtn = el("button", { text: "Обновить" });
    row.appendChild(refreshBtn);
    panel.appendChild(row);

    let deviceByLabel = {};
    async function refreshDevices() {
      const devices = await window.pywebview.api.install_list_devices();
      deviceByLabel = {};
      clear(select);
      for (const d of devices) {
        let label = d.serial;
        if (d.model) label += `  (${d.model})`;
        if (d.state !== "device") label += `  [${d.state}]`;
        deviceByLabel[label] = d.state === "device" ? d.serial : null;
        select.appendChild(el("option", { value: label, text: label }));
      }
    }
    refreshBtn.addEventListener("click", refreshDevices);
    await refreshDevices();

    const actions = stage.actions || [];
    const list = el("div", { style: "display: flex; flex-direction: column; gap: 6px; margin-top: 12px" });
    if (!actions.length) {
      list.appendChild(el("p", { class: "placeholder-text", text: "Для этого этапа не задано ни одного действия." }));
    }
    const buttons = [];
    const setButtonsDisabled = (disabled) => { for (const b of buttons) b.disabled = disabled; };
    actions.forEach((action, i) => {
      const btn = el("button", { class: "accent", text: action.label || `Действие ${i + 1}` });
      btn.disabled = runnerBusy;
      buttons.push(btn);
      btn.addEventListener("click", async () => {
        const device = deviceByLabel[select.value];
        if (!device && !(await window.confirmDialog("Не выбрано подключённое устройство ADB. Продолжить всё равно?"))) return;
        runnerBusy = true;
        setButtonsDisabled(true);
        const result = await window.pywebview.api.install_run_action(model.key, stage.index, i, device, selectedApkPaths());
        if (!result.ok) {
          runnerBusy = false;
          setButtonsDisabled(false);
          log(result.error);
        }
      });
      list.appendChild(btn);
    });
    panel.appendChild(list);
    panel.appendChild(el("p", {
      class: "app-desc", style: "margin-top: 10px",
      text: "Эти кнопки необязательны — можно нажимать в любом порядке и по несколько раз. Когда закончите, нажмите «Далее».",
    }));
  }

  window.stageWizard = { init, open };
})();
