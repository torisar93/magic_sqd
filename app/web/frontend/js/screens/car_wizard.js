// Мастер "Добавить/Изменить машину" — портировано из app/add_car_dialog.py.
// Разница с tkinter-версией: поля формы читаются/пишутся напрямую по
// input-событиям в объект шага (без отдельного "commit" при переключении
// этапа, как в оригинале) — простая прямая привязка, которой не нужен
// весь класс проблем из add_car_dialog.py про "виджет уничтожен раньше,
// чем прочитали его значение" (см. _rebuild_step_form_deferred в старом
// коде — здесь такой класс багов структурно не существует).
(function () {
  const { el, clear } = window.dom;

  const STEP_TYPE_LABELS = {
    adb: "ADB-команды", usb: "USB-флешка", manual: "Ручной шаг", apps: "Выбор приложений",
    exe: "Готовый установщик (.exe)", check: "Проверка/выбор", instruction: "Инструкция",
  };

  const ADB_HELP_TEXT =
    'Можно вставить кусок .bat/.sh как есть: строки "adb ..."/"TIMEOUT /T N" распознаются ' +
    "автоматически, декоративный batch-мусор (@echo/cls/pause/rem/метки) пропускается сам.\n\n" +
    "Спецкоманды (тоже по одной на строку, среди обычных команд):\n" +
    "#sleep 5 — пауза 5 секунд\n" +
    "#reboot — перезагрузить магнитолу и дождаться загрузки\n" +
    '#reboot_nowait — перезагрузить, не дожидаясь (= "adb reboot" как в .bat)\n' +
    "#wait_device — дождаться устройства (можно с таймаутом: #wait_device 60)\n" +
    "#root — adb root\n#disable_verity — adb disable-verity\n#remount — adb remount\n" +
    "#push ИмяФайла /remote/path — закачать прикреплённый файл (см. ниже)\n" +
    "#install ИмяФайла — установить прикреплённый APK\n" +
    "#ask Введите IP-адрес — спросить у пользователя во время установки; ответ можно " +
    "подставить в следующую команду через {ask}, например:\nconnect {ask}:5555\n\n" +
    "Из .bat/.sh распознаются как есть (без переписывания в #-спецкоманды): " +
    "adb root/remount/disable-verity/reboot/wait-for-device, adb shell <команда>, " +
    "adb push <файл> <путь>, adb install <apk>, TIMEOUT /T N.";

  let dialog, headerEl, stepListEl, stepFormEl, newStepTypeSelect;
  let steps = [];
  let currentStepIndex = 0;
  let editingVariantIndex = 0;
  let brand = "", model = "", modification = "", wifi = false, wifiPort = 5555, changelog = "";
  let isEditing = false;
  let editModelKey = null;
  let onCreatedCb = null;
  let logFn = () => {};

  function newStep(type) {
    return {
      type, title: "", description: "", instruction_blocks: [],
      usb_files: [], usb_copy_selected_apks: false,
      commands: [], adb_install_selected_apks: false, adb_files: [],
      standard_apks: [], exe_file: null,
      check_var: "", check_options: [],
      condition_var: "", condition_values: [],
      variants: [],
    };
  }

  // -- инициализация (один раз) ----------------------------------------
  function init(logCallback) {
    logFn = logCallback;
    dialog = document.getElementById("car-wizard-dialog");
    headerEl = document.getElementById("car-wizard-header");
    stepListEl = document.getElementById("car-step-list");
    stepFormEl = document.getElementById("car-step-form");
    newStepTypeSelect = document.getElementById("car-new-step-type");

    clear(newStepTypeSelect);
    for (const [type, label] of Object.entries(STEP_TYPE_LABELS)) {
      newStepTypeSelect.appendChild(el("option", { value: type, text: label }));
    }

    document.getElementById("car-add-step").addEventListener("click", addStep);
    document.getElementById("car-move-up").addEventListener("click", () => moveStep(-1));
    document.getElementById("car-move-down").addEventListener("click", () => moveStep(1));
    document.getElementById("car-remove-step").addEventListener("click", removeStep);
    document.getElementById("car-wizard-cancel").addEventListener("click", () => dialog.close());
    document.getElementById("car-wizard-save").addEventListener("click", onSave);

    window.instructionEditor.init();
    initAdminLoginDialog();

    // Упаковка+отправка на сервер после локального сохранения идёт в фоне
    // (воркер-поток на стороне Python) уже ПОСЛЕ того, как окно мастера
    // закрылось — прогресс виден в логе главного окна, а не в отдельном
    // блокирующем диалоге (по просьбе пользователя: мастер не должен
    // держать техника перед закрытым окном на время долгой публикации).
    window.events.on("car_save_log", (event) => logFn(event.text));
    window.events.on("car_saved", (event) => {
      if (onCreatedCb) onCreatedCb(event.brand, event.model, event.modification);
    });
    window.events.on("car_save_finished", (event) => {
      logFn(event.message);
      setMainProgressVisible(false);
    });
  }

  // Индикатор "идёт сохранение" в карточке "Лог" главного окна — то же
  // .progress-bar, что и в usb/admin-диалогах, только не в отдельном
  // диалоге (см. onSave: окно мастера закрывается сразу, а не ждёт
  // архивирование+отправку на сервер).
  function setMainProgressVisible(visible) {
    const progressEl = document.getElementById("main-progress");
    progressEl.style.display = visible ? "" : "none";
    progressEl.classList.toggle("indeterminate", visible);
  }

  // -- открытие: editModel=null -> "Добавить", editModel={key,...} -> "Изменить" --
  async function open(editModel, existingBrands, onCreated) {
    onCreatedCb = onCreated;
    isEditing = !!editModel;
    editModelKey = editModel ? editModel.key : null;

    if (isEditing) {
      const spec = await window.pywebview.api.car_load_spec(editModel.key);
      if (spec.error) {
        await window.notice(spec.error, { title: "Изменить машину", danger: true });
        return;
      }
      brand = spec.brand; model = spec.model; modification = spec.modification || "";
      wifi = spec.wifi; wifiPort = spec.wifi_port;
      steps = spec.steps;
    } else {
      brand = ""; model = ""; modification = ""; wifi = false; wifiPort = 5555;
      steps = [{ ...newStep("instruction"), title: "Этап 1" }];
    }
    // Заметка "что изменилось" — разовая подпись к КОНКРЕТНОМУ сохранению
    // (см. app/car_generator.py: version.json), не часть spec — поэтому
    // всегда пустая при открытии, даже в режиме редактирования.
    changelog = "";

    document.getElementById("car-wizard-title").textContent = isEditing ? "Изменить машину" : "Добавить машину";
    document.getElementById("car-wizard-save").textContent = isEditing ? "Сохранить" : "Создать";

    renderHeader(existingBrands);
    currentStepIndex = 0;
    editingVariantIndex = 0;
    refreshStepList();
    renderStepForm();
    dialog.showModal();
  }

  // ------------------------------------------------------------------
  // Заголовок: марка/модель/модификация, Wi-Fi
  // ------------------------------------------------------------------
  function renderHeader(existingBrands) {
    clear(headerEl);
    const grid = el("div", { class: "car-header-grid" });

    const brandInput = el("input", { type: "text", list: "car-existing-brands", disabled: isEditing ? "" : null });
    brandInput.value = brand;
    brandInput.addEventListener("input", () => { brand = brandInput.value; });
    const brandList = el("datalist", { id: "car-existing-brands" }, existingBrands.map((b) => el("option", { value: b })));

    const modelInput = el("input", { type: "text", disabled: isEditing ? "" : null });
    modelInput.value = model;
    modelInput.addEventListener("input", () => { model = modelInput.value; });

    const modificationInput = el("input", { type: "text", placeholder: "необязательно", disabled: isEditing ? "" : null });
    modificationInput.value = modification;
    modificationInput.addEventListener("input", () => { modification = modificationInput.value; });

    grid.appendChild(el("span", { class: "field-label", text: "Марка" }));
    grid.appendChild(brandInput);
    grid.appendChild(el("span", { class: "field-label", text: "Модель" }));
    grid.appendChild(modelInput);
    grid.appendChild(el("span", { class: "field-label", text: "Модификация" }));
    grid.appendChild(modificationInput);
    headerEl.appendChild(grid);
    headerEl.appendChild(brandList);

    if (!isEditing) {
      headerEl.appendChild(el("p", {
        class: "app-desc", style: "margin-top: 4px",
        text: "Заполните модификацию, только если это рестайлинг/версия для другого рынка уже существующей (или новой) модели — она станет модификацией внутри общей карточки модели, а не отдельным пунктом.",
      }));
    }

    const wifiRow = el("div", { class: "row", style: "margin-top: 8px" });
    const wifiCheckbox = el("input", { type: "checkbox" });
    wifiCheckbox.checked = wifi;
    const wifiPortInput = el("input", { type: "text", style: "width: 70px; display: " + (wifi ? "inline-block" : "none") });
    wifiPortInput.value = String(wifiPort);
    wifiCheckbox.addEventListener("change", () => {
      wifi = wifiCheckbox.checked;
      wifiPortInput.style.display = wifi ? "inline-block" : "none";
    });
    wifiPortInput.addEventListener("input", () => { wifiPort = wifiPortInput.value; });
    wifiRow.appendChild(el("label", { class: "row" }, [wifiCheckbox, "ADB-этапы подключаются по Wi-Fi (иначе — по USB/уже подключено)"]));
    wifiRow.appendChild(el("span", { text: "Порт:" }));
    wifiRow.appendChild(wifiPortInput);
    headerEl.appendChild(wifiRow);

    // Короткая заметка "что изменилось в этом сохранении" — необязательная,
    // пишется в version.json (см. app/car_generator.py) и попадает в сводку
    // "Что нового" у техников при следующем запуске (см. app/web/api/
    // sync_api.py, app/update_tracker.py). Не привязана к прошлым
    // сохранениям — каждый раз пустая, см. open().
    const changelogField = el("div", { class: "field", style: "margin-top: 8px" });
    changelogField.appendChild(el("span", {
      class: "field-label",
      text: "Что нового в этом сохранении (необязательно, увидят техники)",
    }));
    const changelogInput = el("textarea", {
      rows: "2", placeholder: "Например: поправили баг с автоподключением Wi-Fi",
    });
    changelogInput.value = changelog;
    changelogInput.addEventListener("input", () => { changelog = changelogInput.value; });
    changelogField.appendChild(changelogInput);
    headerEl.appendChild(changelogField);
  }

  // ------------------------------------------------------------------
  // Список этапов слева
  // ------------------------------------------------------------------
  function refreshStepList() {
    clear(stepListEl);
    steps.forEach((step, i) => {
      const li = el("li", {
        class: i === currentStepIndex ? "selected" : "",
        text: `${i + 1}. ${step.title || `Этап ${i + 1}`} (${STEP_TYPE_LABELS[step.type]})`,
      });
      li.addEventListener("click", () => selectStep(i));
      stepListEl.appendChild(li);
    });
  }

  function selectStep(index) {
    currentStepIndex = index;
    editingVariantIndex = 0;
    refreshStepList();
    renderStepForm();
  }

  function addStep() {
    steps.push({ ...newStep(newStepTypeSelect.value), title: `Этап ${steps.length + 1}` });
    refreshStepList();
    selectStep(steps.length - 1);
  }

  async function removeStep() {
    if (steps.length <= 1) {
      await window.notice("Должен остаться хотя бы один этап.");
      return;
    }
    steps.splice(currentStepIndex, 1);
    currentStepIndex = Math.min(currentStepIndex, steps.length - 1);
    refreshStepList();
    renderStepForm();
  }

  function moveStep(direction) {
    const i = currentStepIndex;
    const j = i + direction;
    if (j < 0 || j >= steps.length) return;
    [steps[i], steps[j]] = [steps[j], steps[i]];
    currentStepIndex = j;
    refreshStepList();
    renderStepForm();
  }

  // ------------------------------------------------------------------
  // Форма текущего этапа
  // ------------------------------------------------------------------
  function renderStepForm() {
    clear(stepFormEl);
    const step = steps[currentStepIndex];

    stepFormEl.appendChild(el("span", { class: "field-label", text: "Название этапа" }));
    const titleInput = el("input", { type: "text", style: "margin-bottom: 10px" });
    titleInput.value = step.title;
    titleInput.addEventListener("input", () => {
      step.title = titleInput.value;
      refreshStepList();
    });
    stepFormEl.appendChild(titleInput);

    if (step.type !== "instruction") {
      stepFormEl.appendChild(el("span", { class: "field-label", text: "Описание (инструкция для этого этапа, необязательно)" }));
      const descArea = el("textarea", { style: "min-height: 60px; margin-bottom: 10px" });
      descArea.value = step.description;
      descArea.addEventListener("input", () => { step.description = descArea.value; });
      stepFormEl.appendChild(descArea);
    }

    const builders = {
      adb: renderAdbFields, usb: renderUsbFields, apps: renderAppsFields,
      exe: renderExeFields, check: renderCheckFields, instruction: renderInstructionFields,
    };
    if (builders[step.type]) builders[step.type](step);
    else if (step.type === "manual") {
      stepFormEl.appendChild(el("p", {
        class: "app-desc",
        text: "Для «Ручного шага» дополнительных полей нет — пользователь просто прочитает описание выше и отметит этап выполненным.",
      }));
    }

    renderConditionFields(step);
  }

  function buildSpoiler(title, bodyText) {
    let collapsed = true;
    const body = el("div", { class: "spoiler-body collapsed", text: bodyText });
    const toggle = el("span", { text: "▸ " + title });
    const header = el("div", { class: "spoiler-header" }, [toggle]);
    header.addEventListener("click", () => {
      collapsed = !collapsed;
      body.classList.toggle("collapsed", collapsed);
      toggle.textContent = (collapsed ? "▸ " : "▾ ") + title;
    });
    return el("div", {}, [header, body]);
  }

  // -- adb --------------------------------------------------------------
  function renderAdbFields(step) {
    stepFormEl.appendChild(el("span", { class: "field-label", text: "Команды (по одной на строку, по порядку)" }));
    const commandsArea = el("textarea", { style: "min-height: 140px; font-family: var(--font-mono)" });
    commandsArea.value = step.commands.join("\n");
    commandsArea.addEventListener("input", () => {
      step.commands = commandsArea.value.split("\n").map((l) => l.trim()).filter(Boolean);
    });
    stepFormEl.appendChild(commandsArea);
    stepFormEl.appendChild(buildSpoiler("Справка по командам", ADB_HELP_TEXT));

    stepFormEl.appendChild(el("span", { class: "field-label", text: "Прикреплённые файлы (для #push/#install и adb push/adb install)" }));
    stepFormEl.appendChild(buildFileList(step.adb_files, "any", true, () => renderStepForm()));

    const installRow = el("label", { class: "row", style: "margin-top: 8px" });
    const installCheckbox = el("input", { type: "checkbox" });
    installCheckbox.checked = step.adb_install_selected_apks;
    installCheckbox.addEventListener("change", () => { step.adb_install_selected_apks = installCheckbox.checked; });
    installRow.appendChild(installCheckbox);
    installRow.appendChild(document.createTextNode("Установить отмеченные галочками приложения после команд"));
    stepFormEl.appendChild(installRow);
  }

  // -- общий список файлов (используется adb_files и одиночными usb/apps) --
  function buildFileList(fileArray, pickKind, multiple, onChange) {
    const wrap = el("div");
    const addBtn = el("button", {
      text: pickKind === "apk" ? "Добавить APK..." : "Добавить файлы...",
      onclick: async () => {
        const picked = await window.pywebview.api.car_pick_files(pickKind, multiple);
        if (!picked.length) return;
        fileArray.push(...picked);
        onChange();
      },
    });
    const list = el("ul", { class: "picker-list", style: "margin: 6px 0; max-height: 140px" });
    const selected = new Set();
    fileArray.forEach((f, i) => {
      const li = el("li", { text: f.name });
      li.addEventListener("click", () => {
        li.classList.toggle("selected");
        if (selected.has(i)) selected.delete(i); else selected.add(i);
      });
      list.appendChild(li);
    });
    const removeBtn = el("button", {
      class: "danger",
      text: "Убрать выбранное",
      onclick: () => {
        for (const i of Array.from(selected).sort((a, b) => b - a)) fileArray.splice(i, 1);
        onChange();
      },
    });
    wrap.appendChild(addBtn);
    wrap.appendChild(list);
    wrap.appendChild(removeBtn);
    return wrap;
  }

  // -- usb/apps: одиночный набор ИЛИ несколько именованных вариантов -------
  function renderUsbFields(step) {
    renderVariantToggle(step, "usb_files", "Файлы всех вариантов будут потеряны.");
    if (step.variants.length) {
      renderVariantManager(step, "usb_files", "any", 'Файлы варианта «{name}» в корень флешки');
    } else {
      stepFormEl.appendChild(el("span", { class: "field-label", text: "Файлы в корень флешки" }));
      stepFormEl.appendChild(buildFileList(step.usb_files, "any", true, () => renderStepForm()));
    }
    const copyRow = el("label", { class: "row", style: "margin-top: 8px" });
    const copyCheckbox = el("input", { type: "checkbox" });
    copyCheckbox.checked = step.usb_copy_selected_apks;
    copyCheckbox.addEventListener("change", () => { step.usb_copy_selected_apks = copyCheckbox.checked; });
    copyRow.appendChild(copyCheckbox);
    copyRow.appendChild(document.createTextNode("Скопировать отмеченные галочками приложения на эту флешку"));
    stepFormEl.appendChild(copyRow);
  }

  function renderAppsFields(step) {
    renderVariantToggle(step, "standard_apks", "APK всех вариантов будут потеряны.");
    if (step.variants.length) {
      renderVariantManager(step, "standard_apks", "apk", "APK варианта «{name}»");
    } else {
      stepFormEl.appendChild(el("span", { class: "field-label", text: "APK стандартного набора для этого этапа" }));
      stepFormEl.appendChild(buildFileList(step.standard_apks, "apk", true, () => renderStepForm()));
    }
  }

  function renderVariantToggle(step, singleField, warnText) {
    const row = el("label", { class: "row", style: "margin-bottom: 8px" });
    const checkbox = el("input", { type: "checkbox" });
    checkbox.checked = step.variants.length > 0;
    checkbox.addEventListener("change", async () => {
      if (checkbox.checked && !step.variants.length) {
        step.variants = [{ name: "Вариант 1", usb_files: singleField === "usb_files" ? step[singleField] : [], standard_apks: singleField === "standard_apks" ? step[singleField] : [] }];
        step[singleField] = [];
      } else if (!checkbox.checked && step.variants.length) {
        if (!(await window.confirmDialog(`Убрать варианты и вернуться к одному набору файлов? ${warnText}`))) {
          checkbox.checked = true;
          return;
        }
        step.variants = [];
      }
      editingVariantIndex = 0;
      renderStepForm();
    });
    row.appendChild(checkbox);
    row.appendChild(document.createTextNode("Несколько вариантов (например Full/Lite) — техник выбирает при установке"));
    stepFormEl.appendChild(row);
  }

  function renderVariantManager(step, field, pickKind, headingTpl) {
    if (editingVariantIndex >= step.variants.length) editingVariantIndex = 0;
    const row = el("div", { class: "row", style: "margin-bottom: 4px" });
    const select = el("select", {}, step.variants.map((v, i) => el("option", { value: i, text: v.name, selected: i === editingVariantIndex ? "" : null })));
    select.addEventListener("change", () => { editingVariantIndex = Number(select.value); renderStepForm(); });
    row.appendChild(select);
    row.appendChild(el("button", { text: "Добавить вариант", onclick: () => addVariant(step) }));
    row.appendChild(el("button", { text: "Переименовать", onclick: () => renameVariant(step) }));
    row.appendChild(el("button", { class: "danger", text: "Удалить вариант", onclick: () => removeVariant(step) }));
    stepFormEl.appendChild(row);

    const variant = step.variants[editingVariantIndex];
    stepFormEl.appendChild(el("span", { class: "field-label", text: headingTpl.replace("{name}", variant.name) }));
    stepFormEl.appendChild(buildFileList(variant[field], pickKind, true, () => renderStepForm()));
  }

  async function addVariant(step) {
    const name = (await window.promptDialog("Название варианта (например Full):"))?.trim();
    if (!name) return;
    if (step.variants.some((v) => v.name === name)) {
      await window.notice("Название должно быть непустым и уникальным.");
      return;
    }
    step.variants.push({ name, usb_files: [], standard_apks: [] });
    editingVariantIndex = step.variants.length - 1;
    renderStepForm();
  }

  async function renameVariant(step) {
    const variant = step.variants[editingVariantIndex];
    const name = (await window.promptDialog("Новое название варианта:", { initialValue: variant.name }))?.trim();
    if (!name) return;
    if (step.variants.some((v) => v !== variant && v.name === name)) {
      await window.notice("Название должно быть непустым и уникальным.");
      return;
    }
    variant.name = name;
    renderStepForm();
  }

  async function removeVariant(step) {
    if (step.variants.length <= 1) {
      await window.notice('Должен остаться хотя бы один вариант (или уберите галочку «Несколько вариантов»).');
      return;
    }
    step.variants.splice(editingVariantIndex, 1);
    editingVariantIndex = Math.max(0, editingVariantIndex - 1);
    renderStepForm();
  }

  // -- exe --------------------------------------------------------------
  function renderExeFields(step) {
    stepFormEl.appendChild(el("p", {
      class: "app-desc",
      text: "Готовый установщик от производителя — пользователь просто запустит его и завершит установку сам (для машин, для которых нет доступа к исходным скриптам/инструкциям).",
    }));
    const row = el("div", { class: "row" });
    row.appendChild(el("button", {
      text: "Выбрать .exe файл...",
      onclick: async () => {
        const picked = await window.pywebview.api.car_pick_files("exe", false);
        if (!picked.length) return;
        step.exe_file = picked[0];
        renderStepForm();
      },
    }));
    row.appendChild(el("button", { class: "danger", text: "Убрать", onclick: () => { step.exe_file = null; renderStepForm(); } }));
    stepFormEl.appendChild(row);
    stepFormEl.appendChild(el("p", { class: "app-desc", text: step.exe_file ? step.exe_file.name : "(не выбран)" }));
  }

  // -- check --------------------------------------------------------------
  function renderCheckFields(step) {
    stepFormEl.appendChild(el("p", {
      class: "app-desc",
      text: "Техник сам сверяется с магнитолой (версия аппаратного обеспечения, прошивки и т.п.) и выбирает подходящий вариант из списка ниже во время установки — опишите, как её проверить, в поле «Описание» выше.",
    }));
    stepFormEl.appendChild(el("span", { class: "field-label", text: "Имя переменной (короткое, латиницей, например hw_version)" }));
    const varInput = el("input", { type: "text", style: "margin-bottom: 10px" });
    varInput.value = step.check_var;
    varInput.addEventListener("input", () => { step.check_var = varInput.value.trim(); });
    stepFormEl.appendChild(varInput);

    stepFormEl.appendChild(el("span", { class: "field-label", text: "Варианты выбора" }));
    const optionsWrap = el("div");
    stepFormEl.appendChild(optionsWrap);
    renderCheckOptions(step, optionsWrap);

    // CSS Grid (1fr/auto), а не flex:1 в .row — на это поле не подействовал
    // ни flex:1, ни min-width:0 (см. правку .row > input в tokens.css),
    // сообщено пользователем как "поле узкое, а уже добавленный вариант
    // widе" — grid-колонка 1fr однозначно отдаёт полю всё свободное место,
    // без каких-либо допущений про flex-basis/min-width.
    const addRow = el("div", { style: "display: grid; grid-template-columns: 1fr auto; gap: 6px; margin-top: 4px" });
    const newOptionInput = el("input", { type: "text", placeholder: "Новый вариант" });
    const addOption = () => {
      const value = newOptionInput.value.trim();
      if (!value) return;
      if (step.check_options.includes(value)) {
        window.notice("Такой вариант уже есть.");
        return;
      }
      step.check_options.push(value);
      newOptionInput.value = "";
      renderCheckOptions(step, optionsWrap);
    };
    newOptionInput.addEventListener("keydown", (e) => { if (e.key === "Enter") addOption(); });
    addRow.appendChild(newOptionInput);
    addRow.appendChild(el("button", { text: "Добавить вариант", onclick: addOption }));
    stepFormEl.appendChild(addRow);
  }

  function renderCheckOptions(step, container) {
    clear(container);
    if (!step.check_options.length) {
      container.appendChild(el("p", { class: "app-desc", text: "Вариантов пока нет — добавьте хотя бы один ниже." }));
      return;
    }
    step.check_options.forEach((option, i) => {
      container.appendChild(el("div", { class: "option-card" }, [
        el("span", { text: option }),
        el("button", {
          class: "danger icon-btn", text: "✕",
          onclick: () => { step.check_options.splice(i, 1); renderCheckOptions(step, container); },
        }),
      ]));
    });
  }

  // -- instruction ------------------------------------------------------
  function renderInstructionFields(step) {
    stepFormEl.appendChild(el("p", {
      class: "app-desc",
      text: "Отдельная часть инструкции — заголовки, шаги, важные плашки, фото. Покажется технику отдельной страницей на этом месте в последовательности этапов (а не только один раз в начале).",
    }));
    stepFormEl.appendChild(el("button", {
      class: "accent",
      text: "Написать инструкцию...",
      onclick: () => window.instructionEditor.open(
        step.instruction_blocks.length ? step.instruction_blocks : null,
        (blocks) => { step.instruction_blocks = blocks; renderStepForm(); }
      ),
    }));
    if (!step.instruction_blocks.length) {
      // Пустой шаблон с маркой/моделью в заголовке — как instruction_html.default_blocks в старом коде.
      window.pywebview.api.car_instruction_default_blocks(brand, model).then((blocks) => {
        step.instruction_blocks = blocks;
      });
    }
    stepFormEl.appendChild(el("p", {
      class: "app-desc",
      text: step.instruction_blocks.length ? `Готово (${step.instruction_blocks.length} блок(ов))` : "(пока не написана)",
    }));
  }

  // -- условная видимость (для любого типа этапа) ------------------------
  function availableCheckVars(excludeStep) {
    const names = [];
    for (const step of steps) {
      if (step === excludeStep) continue;
      if (step.type === "check" && step.check_var && !names.includes(step.check_var)) names.push(step.check_var);
    }
    return names;
  }

  function renderConditionFields(step) {
    const ALWAYS = "(всегда)";
    stepFormEl.appendChild(el("span", { class: "field-label", text: "Показывать этап только если (необязательно)", style: "margin-top: 12px" }));
    const values = [ALWAYS, ...availableCheckVars(step)];
    const current = step.condition_var || ALWAYS;
    if (!values.includes(current)) values.push(current);
    const select = el("select", { style: "margin-bottom: 4px" }, values.map((v) => el("option", { value: v, text: v, selected: v === current ? "" : null })));
    const valuesWrap = el("div");

    select.addEventListener("change", () => {
      step.condition_var = select.value === ALWAYS ? "" : select.value;
      step.condition_values = [];
      renderConditionValues(step, valuesWrap);
    });
    stepFormEl.appendChild(select);
    stepFormEl.appendChild(el("span", { class: "field-label", text: "Значения, при которых этап нужен" }));
    stepFormEl.appendChild(valuesWrap);
    renderConditionValues(step, valuesWrap);
  }

  function renderConditionValues(step, container) {
    clear(container);
    if (!step.condition_var) {
      container.appendChild(el("p", { class: "app-desc", text: "Выберите переменную выше" }));
      return;
    }
    const owner = steps.find((s) => s.type === "check" && s.check_var === step.condition_var);
    const options = owner ? owner.check_options : [];
    if (!options.length) {
      container.appendChild(el("p", { class: "app-desc", text: "У этого этапа-переменной пока нет вариантов выбора." }));
      return;
    }
    for (const option of options) {
      const checkbox = el("input", { type: "checkbox" });
      checkbox.checked = step.condition_values.includes(option);
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) step.condition_values.push(option);
        else step.condition_values = step.condition_values.filter((v) => v !== option);
      });
      container.appendChild(el("label", { class: "row" }, [checkbox, option]));
    }
  }

  // ------------------------------------------------------------------
  // Сохранение — по кнопке окно мастера закрывается сразу (не ждёт
  // архивирование+отправку на сервер), прогресс идёт в лог главного окна
  // через car_save_log/car_save_finished (см. init()).
  // ------------------------------------------------------------------
  function specToJson() {
    return { brand, model, modification, wifi, wifi_port: Number(wifiPort) || 5555, steps, changelog };
  }

  async function onSave() {
    if (!brand.trim() || !model.trim()) {
      await window.notice("Укажите марку и модель.");
      return;
    }
    if (wifi && !Number.isFinite(Number(wifiPort))) {
      await window.notice("Порт должен быть числом.");
      return;
    }

    const target = await window.pywebview.api.car_get_publish_target();
    if (target.mode === "admin" && !target.session_cached) {
      const ok = await ensureAdminSession(target.base_url);
      if (!ok) return; // пользователь отменил вход — не начинаем сохранение вовсе
    }

    const label = modification ? `${brand} / ${model} — ${modification}` : `${brand} / ${model}`;
    const result = await window.pywebview.api.car_save(specToJson(), editModelKey);
    if (!result.ok) {
      // Не начиналось вовсе (например, предыдущее сохранение ещё не
      // закончилось) — остаёмся в форме, ничего не закрываем.
      await window.notice(result.error, { title: "Сохранение", danger: true });
      return;
    }
    logFn(`Сохраняю «${label}»...`);
    setMainProgressVisible(true);
    dialog.close();
  }

  // -- вход в админку перед первым сохранением за запуск -------------------
  function initAdminLoginDialog() {
    const dlg = document.getElementById("admin-login-dialog");
    const errorEl = document.getElementById("admin-login-error");
    let resolveFn = null;

    document.getElementById("admin-login-submit").addEventListener("click", async () => {
      const username = document.getElementById("admin-login-username").value.trim();
      const password = document.getElementById("admin-login-password").value;
      if (!username || !password) return;
      const baseUrl = dlg.dataset.baseUrl;
      const result = await window.pywebview.api.car_admin_login(baseUrl, username, password);
      if (result.ok) {
        dlg.close();
        resolveFn(true);
      } else {
        errorEl.textContent = result.error;
        errorEl.style.display = "";
      }
    });
    document.getElementById("admin-login-cancel").addEventListener("click", () => {
      dlg.close();
      resolveFn(false);
    });

    window._openAdminLoginDialog = (baseUrl) => {
      dlg.dataset.baseUrl = baseUrl;
      document.getElementById("admin-login-username").value = "";
      document.getElementById("admin-login-password").value = "";
      errorEl.style.display = "none";
      dlg.showModal();
      return new Promise((resolve) => { resolveFn = resolve; });
    };
  }

  function ensureAdminSession(baseUrl) {
    return window._openAdminLoginDialog(baseUrl);
  }

  window.carWizard = { init, open };
})();
