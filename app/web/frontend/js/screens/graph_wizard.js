// Визуальный редактор-граф этапов установки — единственный редактор "Добавить/
// Изменить машину" (старый текстовый мастер car_wizard.js/список+форма
// удалён после сравнения, см. историю). Использует ТОТ ЖЕ бридж
// (car_load_spec/car_save/car_get_publish_target/car_admin_login) и ТУ ЖЕ
// панель тип-специфичных полей (car_step_fields.js) — ничего в install.py/
// stages.py/установке у техника не меняется, это только способ
// редактировать NewCarSpec.
//
// Холст с pan/zoom, узлы как перетаскиваемые карточки, панель свойств
// справа, авто-раскладка для моделей без сохранённых координат, Save/Cancel.
// Провод порядка выполнения — от псевдо-узла "Начало" к первому шагу, затем
// от каждого шага к следующему, СТРОГО по порядку массива steps (нет
// отдельного состояния — провода каждый раз рисуются заново из текущего
// порядка). Перетаскивание провода от нижнего сокета узла (или "Начала") на
// верхний сокет другого узла переставляет этот узел в массиве steps сразу
// после узла-источника.
// Узлы "Проверка/выбор" получают отдельный выход-сокет на
// каждый check_option; у любого узла есть вход "условие" (слева). "Имя
// переменной" (check_var) — рудимент текстового мастера, тут не
// показывается, генерируется автоматически (см. generateCheckVar) — условие
// выражается исключительно проводом (можно сбросить и на верхний "поток"
// сокет цели, и на левый "условие" — оба работают одинаково).
//
// Позиция узла в последовательности (steps) и условие видимости — разные
// вещи, но связаны так: провод условия ПЕРЕСТАВЛЯЕТ узел сразу после узла
// проверки, ТОЛЬКО если узел ещё "не привязан" (_detached — только что
// создан, см. addNode, ни разу ещё не подключён НИКАКИМ проводом). Это
// покрывает частый случай "N вариантов ведут в один и тот же следующий
// этап": первый же провод условия и ставит новый узел на место, и задаёт
// первое условие; второй и третий провод от других вариантов на ТОТ ЖЕ
// узел (target уже не _detached) только добавляют значения в
// condition_values, положение больше не трогая — поэтому узел слияния,
// который уже стоит на своём месте (в том числе поставленный туда обычным
// потоковым проводом отдельно), от повторных условий не дёргается — см.
// connectConditionWire/applyCondition. Обычный провод потока (зелёный,
// снизу узла/из "Начала" — см. connectFlow) всегда просто переставляет,
// независимо от _detached — это единственный способ подвинуть уже
// размещённый узел. Провода кликабельны для разрыва связи (см. detachFlow/
// removeCondition/clearCondition), у сокетов входа — то же самое хватанием
// напрямую; во время перетаскивания подходящие сокеты-цели увеличиваются
// (см. highlightDropTargets) — маленькую точку иначе легко промахнуть.
(function () {
  const { el, clear } = window.dom;
  const { svgEl } = window.svgDom;

  // "adb" сюда сознательно не входит — как новый узел он больше не
  // предлагается (слит с "actions", см. renderActionsFields в
  // car_step_fields.js: теперь умеет и прикреплённые файлы). STAGE_LABELS
  // ниже (не этот словарь) знает про "adb" отдельно — там нужно уметь
  // отображать уже существующие старые узлы этого типа, если они
  // встретятся при открытии старой модели.
  const STEP_TYPE_LABELS = {
    usb: "USB-флешка", manual: "Ручной шаг", apps: "Установка приложений",
    exe: "Готовый установщик (.exe)", check: "Проверка/выбор", instruction: "Инструкция",
    uart: "UART-команды", telnet: "Telnet (IPv6)", actions: "ADB-команды",
  };
  // Показ уже существующего узла типа "adb" (если такой встретится при
  // открытии старой, ещё не мигрированной модели) — не в STEP_TYPE_LABELS
  // выше, чтобы не предлагать его как ВЫБОР для нового узла.
  const LEGACY_STEP_TYPE_LABELS = { ...STEP_TYPE_LABELS, adb: "ADB-команды (устар.)" };

  const MIN_ZOOM = 0.4;
  const MAX_ZOOM = 2;
  const LAYOUT_STEP_Y = 160;
  const LAYOUT_START = 40;

  let dialog, headerEl, viewportEl, worldEl, propsEl, addTypeSelect;
  let stepFieldsController = null;
  let steps = [];
  let selectedIndex = null;
  let brand = "", model = "", modification = "", wifi = false, wifiPort = 5555, changelog = "";
  let status = "ok"; // см. app/scanner.py: MODEL_STATUSES/model_status_color
  let isEditing = false;
  let editModelKey = null;
  let onCreatedCb = null;
  let logFn = () => {};
  // Заявка клиента, застейдженная в app/web/api/submissions_api.py:stage
  // (см. index.html: кнопки "Опубликовать"/"Отклонить заявку" в футере,
  // видны только для неё) — тот же редактор, что и для обычных моделей,
  // только "Сохранить" не публикует (см. car_editor_api.py:_worker), а
  // публикация/отклонение — отдельные явные действия.
  let isPendingModel = false;
  let pendingSubmissionName = null;

  let panX = 40, panY = 40, zoom = 1;
  let panState = null;
  let dragState = null;
  let wireDragState = null;
  let startNodeEl = null;
  let wiresEl = null;
  const START_POS = { x: 40, y: -80 };

  // "Имя переменной" — рудимент текстового мастера, тут не показывается
  // (см. car_step_fields.js: hideCheckVarField) — condition_var/check_var
  // связаны исключительно проводами, поэтому генерируется автоматически и
  // никогда не редактируется руками.
  function generateCheckVar() {
    return "check_" + Math.random().toString(36).slice(2, 8);
  }

  function newStep(type) {
    return {
      type, title: "", description: "", instruction_blocks: [],
      usb_files: [], usb_copy_selected_apks: false, usb_apks_dest: "", usb_shared_folder: "",
      commands: [], adb_install_selected_apks: false, adb_files: [],
      standard_apks: [], standard_apks_optional: [], apps_connection: "wired", apps_install_method: "",
      actions_connection: "wired",
      exe_file: null, uart_baudrate: 115200, actions: [],
      check_var: type === "check" ? generateCheckVar() : "", check_options: [],
      condition_var: "", condition_values: [],
      variants: [],
      pos_x: 0, pos_y: 0,
    };
  }

  // -- инициализация (один раз) ----------------------------------------
  function init(logCallback) {
    logFn = logCallback;
    dialog = document.getElementById("graph-wizard-dialog");
    headerEl = document.getElementById("graph-wizard-header");
    viewportEl = document.getElementById("graph-viewport");
    worldEl = document.getElementById("graph-world");
    wiresEl = document.getElementById("graph-wires");
    propsEl = document.getElementById("graph-props");
    addTypeSelect = document.getElementById("graph-new-node-type");

    // rerender() общего контроллера полей (car_step_fields.js) должен не
    // только перестроить панель свойств, но и перерисовать холст — узел
    // "Проверка/выбор" может получить/потерять сокеты вариантов (см.
    // renderCanvas), а условная видимость другого узла — провод (см.
    // renderWires), и оба меняются из панели свойств, а не только с холста.
    stepFieldsController = window.carStepFields.createStepFieldsController(
      propsEl, () => steps, () => ({ brand, model }), () => { renderCanvas(); renderProperties(); },
      { hideCheckVarField: true });

    clear(addTypeSelect);
    for (const [type, label] of Object.entries(STEP_TYPE_LABELS)) {
      addTypeSelect.appendChild(el("option", { value: type, text: label }));
    }

    document.getElementById("graph-add-node").addEventListener("click", addNode);
    document.getElementById("graph-remove-node").addEventListener("click", removeNode);
    document.getElementById("graph-zoom-reset").addEventListener("click", () => {
      zoom = 1; panX = 40; panY = 40; applyTransform();
    });
    document.getElementById("graph-wizard-cancel").addEventListener("click", () => dialog.close());
    document.getElementById("graph-wizard-save").addEventListener("click", onSave);
    document.getElementById("graph-wizard-publish").addEventListener("click", onPublish);
    document.getElementById("graph-wizard-reject").addEventListener("click", onReject);

    viewportEl.addEventListener("mousedown", onViewportMouseDown);
    viewportEl.addEventListener("wheel", onWheelZoom, { passive: false });

    initAdminLoginDialog();

    window.events.on("car_save_log", (event) => logFn(event.text));
    window.events.on("car_saved", (event) => {
      if (onCreatedCb) onCreatedCb(event.brand, event.model, event.modification, event.dir);
    });
    window.events.on("car_save_finished", (event) => {
      logFn(event.message);
      setMainProgressVisible(false);
      // Диалог мастера к этому моменту уже закрыт (onSave закрывает его
      // оптимистично, не дожидаясь результата фонового потока — само
      // сохранение идёт в фоне, см. app/web/api/car_editor_api.py:_worker) —
      // без явного уведомления ошибка (например "такая модель уже
      // существует") видна только строчкой в логе главного окна и легко
      // остаётся незамеченной.
      if (!event.success) {
        window.notice(event.message, { title: "Не удалось сохранить", danger: true });
      }
    });
  }

  // -- вход в админку перед первым сохранением за запуск -------------------
  // (перенесено из старого текстового мастера car_wizard.js — с переходом
  // на визуальный редактор как единственный он тут больше не нужен)
  function initAdminLoginDialog() {
    const dlg = document.getElementById("graph-admin-login-dialog");
    const errorEl = document.getElementById("graph-admin-login-error");
    let resolveFn = null;

    document.getElementById("graph-admin-login-submit").addEventListener("click", async () => {
      const username = document.getElementById("graph-admin-login-username").value.trim();
      const password = document.getElementById("graph-admin-login-password").value;
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
    document.getElementById("graph-admin-login-cancel").addEventListener("click", () => {
      dlg.close();
      resolveFn(false);
    });

    window._openAdminLoginDialog = (baseUrl) => {
      dlg.dataset.baseUrl = baseUrl;
      document.getElementById("graph-admin-login-username").value = "";
      document.getElementById("graph-admin-login-password").value = "";
      errorEl.style.display = "none";
      dlg.showModal();
      return new Promise((resolve) => { resolveFn = resolve; });
    };
  }

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
    isPendingModel = !!(editModel && editModel.is_pending);
    pendingSubmissionName = editModel ? editModel.submission_name : null;

    if (isEditing) {
      const spec = await window.pywebview.api.car_load_spec(editModel.key);
      if (spec.error) {
        await window.notice(spec.error, { title: "Визуальный редактор", danger: true });
        return;
      }
      brand = spec.brand; model = spec.model; modification = spec.modification || "";
      wifi = spec.wifi; wifiPort = spec.wifi_port;
      status = spec.status || "ok";
      steps = spec.steps;
    } else {
      brand = ""; model = ""; modification = ""; wifi = false; wifiPort = 5555; status = "ok";
      steps = [{ ...newStep("instruction"), title: "Этап 1" }];
    }
    changelog = "";
    autoLayout();
    // Модели, созданные/правленные классическим мастером (или графом до
    // этого фикса), могут иметь check-этап без check_var (поле там
    // необязательное) — без него нечего вешать на выходы вариантов на
    // холсте, поэтому подставляем автоматически, тихо.
    steps.forEach((step) => {
      if (step.type === "check" && !step.check_var) step.check_var = generateCheckVar();
    });

    document.getElementById("graph-wizard-title").textContent = isPendingModel
      ? "Заявка клиента" : (isEditing ? "Изменить машину" : "Добавить машину");
    document.getElementById("graph-wizard-save").textContent = isEditing ? "Сохранить" : "Создать";
    document.getElementById("graph-wizard-publish").style.display = isPendingModel ? "" : "none";
    document.getElementById("graph-wizard-reject").style.display = isPendingModel ? "" : "none";

    renderHeader(existingBrands);
    selectedIndex = null;
    stepFieldsController.resetVariantIndex();
    zoom = 1; panX = 40; panY = 40;
    applyTransform();
    // showModal() ДО renderCanvas()/renderWires() — <dialog> закрыт
    // (display: none) до этого момента, а offsetWidth/offsetHeight,
    // которыми считаются координаты проводов (см. socketPos/
    // checkOptionSocketPos), для элементов внутри display:none-поддерева
    // всегда равны 0 — отсюда "кривой" провод к первому шагу при открытии.
    dialog.showModal();
    renderCanvas();
    renderProperties();
  }

  // Шаги без сохранённой позиции (0/0 — и вновь созданные, и шаги,
  // сохранённые классическим мастером, который её не проставляет) — вертикальный
  // стек по порядку массива, чтобы граф открывался осмысленно для ЛЮБОЙ
  // модели, а не только для сохранённых из самого графа.
  function autoLayout() {
    steps.forEach((step, i) => {
      if (!step.pos_x && !step.pos_y) {
        step.pos_x = LAYOUT_START;
        step.pos_y = LAYOUT_START + i * LAYOUT_STEP_Y;
      }
    });
  }

  // ------------------------------------------------------------------
  // Заголовок: марка/модель/модификация, Wi-Fi, чейнджлог — те же поля,
  // что и в car_wizard.js (не общий код, т.к. модуль отдельный, но
  // поведение и разметка идентичны для визуальной согласованности).
  // ------------------------------------------------------------------
  function renderHeader(existingBrands) {
    clear(headerEl);
    const grid = el("div", { class: "car-header-grid" });

    // Марка/модель/модификация редактируются и в режиме правки — при
    // сохранении car_generator.py:update_car физически переносит папку
    // модели на новый путь (полноценное переименование, см. car_save/
    // update_car). Заблокированы только для "Заявки клиента"
    // (isPendingModel) — там переименование пока не переносит физически
    // застейдженную папку (см. update_car(..., allow_rename=False) в
    // car_editor_api.py), так что смысла редактировать эти поля нет.
    const nameLocked = isPendingModel;
    const brandInput = el("input", { type: "text", list: "graph-existing-brands", disabled: nameLocked ? "" : null });
    brandInput.value = brand;
    brandInput.addEventListener("input", () => { brand = brandInput.value; });
    const brandList = el("datalist", { id: "graph-existing-brands" }, existingBrands.map((b) => el("option", { value: b })));

    const modelInput = el("input", { type: "text", disabled: nameLocked ? "" : null });
    modelInput.value = model;
    modelInput.addEventListener("input", () => { model = modelInput.value; });

    const modificationInput = el("input", { type: "text", placeholder: "необязательно", disabled: nameLocked ? "" : null });
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

    // Общий чекбокс "весь ADB модели по Wi-Fi" убран из формы (был нужен
    // только легаси-типу "adb" — см. car_generator.py: _with_connect — и
    // не давал доступа к Wi-Fi типу "actions" вовсе). Теперь у "Установки
    // приложений" и "ADB-команд" свой независимый выбор способа/порта прямо
    // на самом этапе (см. car_step_fields.js: renderAppsFields/
    // renderActionsFields), а uart/telnet — там же, справочным полем.
    // Переменные wifi/wifiPort ниже (см. открытие модели выше) по-прежнему
    // отправляются в spec_data при сохранении без изменений — старые модели
    // с легаси "adb" не теряют уже сохранённые spec.wifi/wifi_port, их
    // просто больше нельзя редактировать через эту форму.

    const changelogField = el("div", { class: "field", style: "margin-top: 8px" });
    changelogField.appendChild(el("span", {
      class: "field-label", text: "Что нового в этом сохранении (необязательно, увидят техники)",
    }));
    const changelogInput = el("textarea", {
      rows: "2", placeholder: "Например: поправили баг с автоподключением Wi-Fi",
    });
    changelogInput.value = changelog;
    changelogInput.addEventListener("input", () => { changelog = changelogInput.value; });
    changelogField.appendChild(changelogInput);
    headerEl.appendChild(changelogField);

    // Статус — цветная метка у марки/модели в списке техника (см.
    // app/scanner.py: MODEL_STATUSES/model_status_color). В отличие от
    // changelog выше — держащееся значение, не разовая заметка: остаётся,
    // пока сюда же не поменяют. "Недавно обновлено" тут не выбор — она
    // сама на несколько часов перекрашивает ЛЮБОЙ статус сразу после
    // сохранения (см. renderStatusHint ниже), поэтому в списке только те
    // статусы, которые техник должен осознанно выбрать.
    const statusField = el("div", { class: "field", style: "margin-top: 8px" });
    statusField.appendChild(el("span", { class: "field-label", text: "Статус (метка в списке машин)" }));
    const statusSelect = el("select", {}, [
      el("option", { value: "ok", text: "🟢 Актуально", selected: status === "ok" ? "" : null }),
      el("option", { value: "needs_review", text: "🟡 Требует обновления (черновик/не проверено)", selected: status === "needs_review" ? "" : null }),
      el("option", { value: "broken", text: "🔴 Способ не работает", selected: status === "broken" ? "" : null }),
    ]);
    statusSelect.addEventListener("change", () => { status = statusSelect.value; });
    statusField.appendChild(statusSelect);
    statusField.appendChild(el("p", {
      class: "app-desc", style: "margin-top: 4px",
      text: "Сразу после сохранения метка на несколько часов станет синей (\"недавно обновлено\") "
        + "независимо от выбора выше, потом сама вернётся к нему.",
    }));
    headerEl.appendChild(statusField);
  }

  // ------------------------------------------------------------------
  // Холст: pan/zoom
  // ------------------------------------------------------------------
  function applyTransform() {
    worldEl.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
  }

  function onViewportMouseDown(e) {
    if (e.target !== viewportEl || e.button !== 0) return;
    // Без preventDefault браузер параллельно с нашим pan-перетаскиванием
    // может начать свой родной жест (выделение текста на странице) —
    // видно как "выделился весь текст, всё двигается вместе" (см. отчёт
    // пользователя). user-select:none в CSS одно это не предотвращает —
    // жест уже может успеть начаться до применения стиля.
    e.preventDefault();
    panState = { startClientX: e.clientX, startClientY: e.clientY, startPanX: panX, startPanY: panY };
    viewportEl.classList.add("panning");
    document.addEventListener("mousemove", onPanMove);
    document.addEventListener("mouseup", onPanEnd);
  }

  function onPanMove(e) {
    if (!panState) return;
    panX = panState.startPanX + (e.clientX - panState.startClientX);
    panY = panState.startPanY + (e.clientY - panState.startClientY);
    applyTransform();
  }

  function onPanEnd() {
    panState = null;
    viewportEl.classList.remove("panning");
    document.removeEventListener("mousemove", onPanMove);
    document.removeEventListener("mouseup", onPanEnd);
  }

  function onWheelZoom(e) {
    e.preventDefault();
    const rect = viewportEl.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;
    const factor = e.deltaY < 0 ? 1.1 : 1 / 1.1;
    const newZoom = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom * factor));
    // Держим точку холста под курсором на месте при изменении масштаба —
    // иначе масштабирование "уводит" куда смотрел, а не куда наведена мышь.
    panX = mouseX - (mouseX - panX) * (newZoom / zoom);
    panY = mouseY - (mouseY - panY) * (newZoom / zoom);
    zoom = newZoom;
    applyTransform();
  }

  // ------------------------------------------------------------------
  // Узлы
  // ------------------------------------------------------------------
  function renderCanvas() {
    Array.from(worldEl.querySelectorAll(".graph-node, .graph-start-node")).forEach((n) => n.remove());

    const startOut = el("div", { class: "graph-socket graph-socket-out" });
    startOut.addEventListener("mousedown", (e) => startWireDrag(e, -1, "flow"));
    startNodeEl = el("div", {
      class: "graph-start-node",
      style: `left: ${START_POS.x}px; top: ${START_POS.y}px`,
    }, ["Начало", startOut]);
    worldEl.appendChild(startNodeEl);

    steps.forEach((step, i) => {
      const titleEl = el("div", { text: step.title || `Этап ${i + 1}` });
      const typeLabelEl = el("div", { class: "graph-node-type-label", text: LEGACY_STEP_TYPE_LABELS[step.type] || step.type });
      const header = el("div", { class: "graph-node-header" }, [titleEl, typeLabelEl]);
      const body = el("div", { class: "graph-node-body", text: step.description || "" });
      const inSocket = el("div", { class: "graph-socket graph-socket-in" });
      const outSocket = el("div", { class: "graph-socket graph-socket-out" });
      // Вход "условие" — есть у ЛЮБОГО узла (не только у "check"), см.
      // condition_var/condition_values — этап любого типа может быть скрыт
      // условно за ответом какого-то более раннего "Проверка/выбор".
      const conditionInSocket = el("div", { class: "graph-socket graph-socket-condition-in" });
      outSocket.addEventListener("mousedown", (e) => startWireDrag(e, i, "flow"));
      // Схватить сам вход (а не только кликнуть по проводу) — тоже
      // отвязывает текущую связь; второй, более "нащупываемый" способ
      // разорвать провод, раз тонкую линию кликнуть трудно.
      inSocket.addEventListener("mousedown", (e) => { e.stopPropagation(); detachFlow(i); });
      conditionInSocket.addEventListener("mousedown", (e) => {
        e.stopPropagation();
        if (step.condition_var) clearCondition(i);
      });

      const children = [inSocket, conditionInSocket, header, body];

      // Выход по одному на каждый вариант выбора — только у "Проверка/
      // выбор". Перетаскивание от такого выхода на вход "условие" другого
      // узла — см. connectConditionWire.
      if (step.type === "check" && step.check_options.length) {
        const outputsWrap = el("div", { class: "graph-node-check-outputs" });
        step.check_options.forEach((option, optIndex) => {
          const dot = el("div", { class: "graph-socket-condition-out" });
          dot.addEventListener("mousedown", (e) => startWireDrag(e, i, "condition", optIndex));
          outputsWrap.appendChild(el("div", { class: "graph-check-option-row" }, [el("span", { text: option }), dot]));
        });
        children.push(outputsWrap);
      }

      children.push(outSocket);
      const node = el("div", {
        class: `graph-node graph-node-type-${step.type}` + (i === selectedIndex ? " selected" : ""),
        style: `left: ${step.pos_x}px; top: ${step.pos_y}px`,
      }, children);
      node.addEventListener("mousedown", (e) => startNodeDrag(e, i, node));
      worldEl.appendChild(node);
    });

    renderWires();
  }

  // -- сокеты/провода -----------------------------------------------------
  function socketPos(index, role) {
    if (index === -1) {
      const w = startNodeEl.offsetWidth, h = startNodeEl.offsetHeight;
      return { x: START_POS.x + w / 2, y: START_POS.y + h };
    }
    const nodes = worldEl.querySelectorAll(".graph-node");
    const node = nodes[index];
    const step = steps[index];
    if (!node || !step) return null;
    const w = node.offsetWidth, h = node.offsetHeight;
    if (role === "in") return { x: step.pos_x + w / 2, y: step.pos_y };
    if (role === "out") return { x: step.pos_x + w / 2, y: step.pos_y + h };
    if (role === "condition-in") return { x: step.pos_x, y: step.pos_y + h / 2 };
    return null;
  }

  // Позиция конкретного выхода-варианта узла "Проверка/выбор" — измеряется
  // по реальному DOM-элементу точки (см. renderCanvas), а не вычисляется
  // геометрией вручную, чтобы не расходиться с фактической раскладкой строк.
  function checkOptionSocketPos(index, optionIndex) {
    const nodes = worldEl.querySelectorAll(".graph-node");
    const node = nodes[index];
    const step = steps[index];
    if (!node || !step) return null;
    const dots = node.querySelectorAll(".graph-socket-condition-out");
    const dot = dots[optionIndex];
    if (!dot) return null;
    return {
      x: step.pos_x + dot.offsetLeft + dot.offsetWidth / 2,
      y: step.pos_y + dot.offsetTop + dot.offsetHeight / 2,
    };
  }

  function wirePath(p1, p2) {
    const midY = (p1.y + p2.y) / 2;
    return `M ${p1.x} ${p1.y} C ${p1.x} ${midY}, ${p2.x} ${midY}, ${p2.x} ${p2.y}`;
  }

  // Укорачивает отрезок p1->p2 на marginPx с каждого конца (линейно, не по
  // самой кривой — для подрезки кликабельной зоны этого достаточно точно).
  function shrinkEndpoints(p1, p2, marginPx) {
    const dx = p2.x - p1.x, dy = p2.y - p1.y;
    const len = Math.hypot(dx, dy) || 1;
    const t = Math.min(marginPx, len / 2 - 1) / len;
    return [{ x: p1.x + dx * t, y: p1.y + dy * t }, { x: p2.x - dx * t, y: p2.y - dy * t }];
  }

  // Рисует провод как ДВЕ SVG-линии: тонкую видимую (className, полной
  // длины — от сокета до сокета) и широкую прозрачную поверх неё
  // (.graph-wire-hit, ~14px, только у неё pointer-events) — но кликабельная
  // линия НАМЕРЕННО короче видимой (не доходит до самих сокетов, см.
  // shrinkEndpoints): иначе её зона перекрывается с сокетами на концах, и
  // клик по проводу рядом с узлом чаще попадает в сокет, а не в провод —
  // запускает перетаскивание нового соединения вместо удаления старого
  // (см. отчёт пользователя: "провода перепрыгивают на другие окна").
  function appendWire(className, p1, p2, onClick) {
    const fullD = wirePath(p1, p2);
    if (onClick) {
      const [hp1, hp2] = shrinkEndpoints(p1, p2, 18);
      wiresEl.appendChild(svgEl("path", { class: "graph-wire-hit", d: wirePath(hp1, hp2), onclick: onClick }));
    }
    wiresEl.appendChild(svgEl("path", { class: `graph-wire ${className}`, d: fullD }));
  }

  function renderWires() {
    clear(wiresEl);
    if (!steps.length) return;

    // Поток выполнения — строго по порядку массива steps, "Начало" -> 0-й
    // шаг -> 1-й -> ... Никакого отдельного состояния, провода — просто
    // текущий порядок массива, нарисованный заново. НЕ рисуем основную
    // стрелку во вход шага, у которого уже есть условие (condition_var) —
    // его входящая связь полностью представлена проводом условия (ниже) —
    // ИЛИ который помечен "не привязан" (_detached, см. addNode) — только
    // что созданный шаг ничего не показывает, пока пользователь сам не
    // протянет провод. Сами провода потока кликабельны — клик отвязывает
    // целевой шаг от текущего места (см. detachFlow), после чего его можно
    // перетащить проводом куда угодно заново.
    if (!steps[0].condition_var && !steps[0]._detached) {
      appendWire("graph-wire-flow", socketPos(-1, "out"), socketPos(0, "in"), () => detachFlow(0));
    }
    for (let i = 0; i < steps.length - 1; i++) {
      if (steps[i + 1].condition_var || steps[i + 1]._detached) continue;
      appendWire("graph-wire-flow", socketPos(i, "out"), socketPos(i + 1, "in"), () => detachFlow(i + 1));
    }

    // "Слияние веток" — дополнительные провода потока в уже РАЗМЕЩЁННЫЙ
    // узел от ДРУГИХ узлов (см. connectFlow: mergeSources) — единственная
    // РЕАЛЬНАЯ позиция узла в последовательности по-прежнему одна (см. цикл
    // выше, определяется порядком массива steps), эти провода её не
    // меняют — только показывают "сюда же ведёт и эта ветка", раз после
    // разных вариантов проверки реально исполняется только одна ветка, а
    // остальные пропускаются (см. пояснение пользователю). Только для
    // текущего сеанса редактирования, не сохраняются между открытиями —
    // это чисто наглядность, а не отдельные данные модели.
    steps.forEach((step, targetIndex) => {
      if (!step._mergeSources || !step._mergeSources.length) return;
      for (const sourceStep of step._mergeSources) {
        const sourceIndex = steps.indexOf(sourceStep);
        if (sourceIndex === -1) continue;
        const from = socketPos(sourceIndex, "out");
        const to = socketPos(targetIndex, "in");
        if (!from || !to) continue;
        appendWire("graph-wire-flow", from, to, () => removeMergeSource(targetIndex, sourceStep));
      }
    });

    // Условная видимость — провод от каждого значения check_options
    // узла-владельца (condition_var) к входу "условие" зависимого узла.
    // Кликабельны — клик убирает именно эту связь (см. removeCondition), не
    // трогая остальные условия этого же узла.
    steps.forEach((step, targetIndex) => {
      if (!step.condition_var) return;
      const ownerIndex = steps.findIndex((s) => s.type === "check" && s.check_var === step.condition_var);
      if (ownerIndex === -1) return;
      const owner = steps[ownerIndex];
      for (const value of step.condition_values) {
        const optionIndex = owner.check_options.indexOf(value);
        if (optionIndex === -1) continue;
        const from = checkOptionSocketPos(ownerIndex, optionIndex);
        const to = socketPos(targetIndex, "condition-in");
        if (!from || !to) continue;
        appendWire("graph-wire-condition", from, to, () => removeCondition(targetIndex, value));
      }
    });
  }

  // Перетаскивание нового провода от сокета "выход" узла-источника —
  // kind="flow": обычный поток (sourceIndex === -1 — псевдо-узел "Начало"),
  // на вход "поток" другого узла — переставляет целевой узел в массиве
  // steps сразу после источника (или в начало, если источник — "Начало").
  // kind="condition": выход конкретного варианта узла "Проверка/выбор"
  // (optionIndex) на вход "условие" другого узла — устанавливает
  // condition_var/condition_values. Никакого отдельного состояния "проводов"
  // нет — оба случая просто меняют steps, провода перерисовываются заново.
  function startWireDrag(e, sourceIndex, kind, optionIndex) {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault(); // см. onViewportMouseDown — иначе может начаться выделение текста
    const from = kind === "condition" ? checkOptionSocketPos(sourceIndex, optionIndex) : socketPos(sourceIndex, "out");
    if (!from) return;
    const wireClass = kind === "condition" ? "graph-wire-condition" : "graph-wire-flow";
    const tempPath = svgEl("path", { class: `graph-wire ${wireClass} graph-wire-dragging` });
    wiresEl.appendChild(tempPath);
    wireDragState = { sourceIndex, kind, optionIndex, from, tempPath };
    document.addEventListener("mousemove", onWireDragMove);
    document.addEventListener("mouseup", onWireDragEnd);
    highlightDropTargets(kind);
  }

  // Пока идёт перетаскивание провода — увеличиваем все подходящие
  // сокеты-цели (крупнее и заметнее, см. .graph-drop-target в graph.css):
  // условие можно бросить и на вход "поток", и на вход "условие" (см.
  // onWireDragEnd), оба увеличиваются разом. Маленькая (12px) точка на
  // конце длинного перетаскивания — сама по себе источник промахов, из-за
  // которых казалось, что часть проводов "не получается провести" (см.
  // отчёт пользователя) — крупная цель решает это прямее, чем гадать,
  // почему именно промахивается конкретное перетаскивание.
  function highlightDropTargets(kind) {
    const selector = kind === "condition"
      ? ".graph-socket-in, .graph-socket-condition-in"
      : ".graph-socket-in";
    worldEl.querySelectorAll(selector).forEach((s) => s.classList.add("graph-drop-target"));
  }

  function clearDropTargetHighlights() {
    worldEl.querySelectorAll(".graph-drop-target").forEach((s) => s.classList.remove("graph-drop-target"));
  }

  function onWireDragMove(e) {
    if (!wireDragState) return;
    const rect = viewportEl.getBoundingClientRect();
    const worldX = (e.clientX - rect.left - panX) / zoom;
    const worldY = (e.clientY - rect.top - panY) / zoom;
    wireDragState.tempPath.setAttribute("d", wirePath(wireDragState.from, { x: worldX, y: worldY }));
  }

  function onWireDragEnd(e) {
    document.removeEventListener("mousemove", onWireDragMove);
    document.removeEventListener("mouseup", onWireDragEnd);
    if (!wireDragState) return;
    const { sourceIndex, kind, optionIndex, tempPath } = wireDragState;
    tempPath.remove();
    wireDragState = null;

    // Хит-тест ДО снятия подсветки — пока цели ещё увеличены (см.
    // highlightDropTargets), чтобы реальная кликабельная область на
    // момент проверки совпадала с тем, что видел пользователь.
    const hit = document.elementFromPoint(e.clientX, e.clientY);
    clearDropTargetHighlights();

    if (kind === "flow") {
      const socket = hit ? hit.closest(".graph-socket-in") : null;
      const nodeEl = socket ? socket.closest(".graph-node") : null;
      const targetIndex = nodeEl ? Array.from(worldEl.querySelectorAll(".graph-node")).indexOf(nodeEl) : -1;
      if (targetIndex === -1) {
        console.warn("graph_wizard: flow-wire drop missed a socket", { hit });
        renderWires();
        return;
      }
      try {
        connectFlow(sourceIndex, targetIndex);
      } catch (err) {
        console.error("graph_wizard: connectFlow failed", err);
        window.notice(`Не удалось переставить этап: ${err && err.message ? err.message : err}`,
          { title: "Ошибка (сообщите об этом)", danger: true });
      }
      return;
    }

    // kind === "condition" — можно сбросить и на верхний (поток), и на левый
    // (условие) сокет цели, оба принимаются одинаково — connectConditionWire
    // только добавляет условие, положение узла никогда не трогает (см. его
    // комментарий).
    const flowSocket = hit ? hit.closest(".graph-socket-in") : null;
    const conditionSocket = hit ? hit.closest(".graph-socket-condition-in") : null;
    const targetSocket = flowSocket || conditionSocket;
    const nodeEl = targetSocket ? targetSocket.closest(".graph-node") : null;
    const targetIndex = nodeEl ? Array.from(worldEl.querySelectorAll(".graph-node")).indexOf(nodeEl) : -1;
    if (targetIndex === -1) {
      console.warn("graph_wizard: condition-wire drop missed a socket", { hit });
      renderWires();
      return;
    }
    // Промах молча "ничего не делал" бы при любой брошенной сюда ошибке
    // (async-функция без await на месте вызова = отклонённый promise без
    // видимой обратной связи) — раз пользователь сообщает "не работает"
    // без явной причины, ловим и показываем явно, а не гадаем вслепую ещё раз.
    connectConditionWire(sourceIndex, optionIndex, targetIndex).catch((err) => {
      console.error("graph_wizard: connectConditionWire failed", err);
      window.notice(`Не удалось задать условие: ${err && err.message ? err.message : err}`,
        { title: "Ошибка (сообщите об этом)", danger: true });
    });
  }

  // Переставляет movedStep сразу после sourceStep в массиве steps (null —
  // в самое начало, см. "Начало"). Общая часть connectFlow/
  // connectConditionWire — работает по ссылкам на объекты, а не по
  // индексам, чтобы не зависеть от того, что индексы могли сместиться
  // из-за предыдущих операций в той же перестановке.
  function moveStepAfter(sourceStep, movedStep) {
    if (movedStep === sourceStep) return;
    const idx = steps.indexOf(movedStep);
    if (idx === -1) return;
    steps.splice(idx, 1);
    if (sourceStep === null) {
      steps.unshift(movedStep);
    } else {
      const insertIndex = steps.indexOf(sourceStep) + 1;
      steps.splice(insertIndex, 0, movedStep);
    }
  }

  // Реальная позиция шага в последовательности — только одна (steps —
  // плоский массив, на нём и построена настоящая установка у техника, см.
  // stage_runner.py — это НЕ трогаем). Первый провод потока на узел, пока
  // он ещё "не привязан" (_detached — только что создан), ставит его на
  // это единственное место. Если узел УЖЕ размещён и провод потока тянут
  // на него ЕЩЁ РАЗ от ДРУГОГО узла — это не переезд, а "сюда же ведёт и
  // эта ветка": добавляем источник в mergeSources (см. renderWires) —
  // чисто наглядный доп.провод для случая "после разных вариантов проверки
  // — общий следующий этап" (реально исполнится только одна из веток, но
  // на холсте видно, что от каждой есть путь сюда).
  function connectFlow(sourceIndex, targetIndex) {
    if (sourceIndex === targetIndex) return;
    if (targetIndex < 0 || targetIndex >= steps.length) return;
    const targetStep = steps[targetIndex];
    const sourceStepRef = sourceIndex === -1 ? null : steps[sourceIndex];

    if (!targetStep._detached) {
      if (sourceStepRef && sourceStepRef !== targetStep) {
        targetStep._mergeSources = targetStep._mergeSources || [];
        if (!targetStep._mergeSources.includes(sourceStepRef)) targetStep._mergeSources.push(sourceStepRef);
      }
      renderWires();
      return;
    }

    const selectedStepRef = selectedIndex !== null ? steps[selectedIndex] : null;
    moveStepAfter(sourceStepRef, targetStep);
    targetStep._detached = false;

    selectedIndex = selectedStepRef ? steps.indexOf(selectedStepRef) : null;
    renderCanvas();
  }

  function removeMergeSource(targetIndex, sourceStep) {
    const step = steps[targetIndex];
    if (!step || !step._mergeSources) return;
    step._mergeSources = step._mergeSources.filter((s) => s !== sourceStep);
    renderWires();
  }

  // Устанавливает condition_var/condition_values у targetStep по варианту
  // checkStep.check_options[optionIndex] — общая часть connectConditionWire.
  // Если у targetStep уже есть условие от ДРУГОЙ переменной — спрашивает
  // подтверждение перед заменой (тот же принцип, что раньше был у
  // выпадающего списка в панели свойств, см. car_step_fields.js).
  // Возвращает false, если пользователь отменил замену.
  async function applyCondition(checkStep, optionIndex, targetStep) {
    const optionValue = checkStep.check_options[optionIndex];
    if (optionValue === undefined) return false;
    // check_var обычно уже проставлен автоматически (см. newStep/open) —
    // это просто защитный запасной случай, если он всё же пуст.
    if (!checkStep.check_var) checkStep.check_var = generateCheckVar();
    if (targetStep.condition_var && targetStep.condition_var !== checkStep.check_var) {
      const ok = await window.confirmDialog(
        `Этап уже показывается по условию «${targetStep.condition_var}». Заменить на «${checkStep.check_var}»?`);
      if (!ok) return false;
      targetStep.condition_values = [];
    }
    targetStep.condition_var = checkStep.check_var;
    if (!targetStep.condition_values.includes(optionValue)) targetStep.condition_values.push(optionValue);
    return true;
  }

  // Провод условия НИКОГДА не переставляет узел в последовательности — это
  // всегда исключительно работа потокового провода (см. connectFlow),
  // независимо от того, новый узел или уже размещённый. Раньше условие
  // заодно переставляло узел при первом перетаскивании (пока он "не
  // привязан", см. _detached) — из-за этого узел слияния, к которому по
  // очереди тянут условия от НЕСКОЛЬКИХ разных вариантов одного и того же
  // check-этапа, срабатывал только на первое перетаскивание: оно
  // переставляло узел к check-этапу, и только оно визуально "цепляло" (см.
  // отчёт пользователя) — остальные условия тихо добавлялись в данные, но
  // без видимого перемещения это выглядело как "не работает". Теперь
  // положение и условие — полностью независимые действия: чтобы поместить
  // новый узел в ветку, сначала тяните ОБЫЧНЫЙ (зелёный) провод потока для
  // позиции, потом — сколько угодно оранжевых проводов условия с любых
  // вариантов, в любом порядке, положение узла они не тронут.
  async function connectConditionWire(checkIndex, optionIndex, targetIndex) {
    const checkStep = steps[checkIndex];
    const targetStep = steps[targetIndex];
    if (!checkStep || !targetStep || checkStep === targetStep) {
      renderWires();
      return;
    }
    // Если цель ещё "не привязана" (_detached — только что созданный узел,
    // ни разу ещё никуда не подключённый) — первый же провод условия
    // заодно ставит её на место в последовательности сразу после узла
    // проверки. Именно так собирается частый случай "3 варианта — 1
    // общий следующий этап": первый провод и позиционирует, и задаёт
    // условие; второй и третий (target уже НЕ detached) только добавляют
    // ещё значения в condition_values, положение не трогая — поэтому узел
    // слияния, уже размещённый явным потоковым проводом, от повторных
    // условий не дёргается.
    const selectedStepRef = selectedIndex !== null ? steps[selectedIndex] : null;
    if (targetStep._detached) {
      moveStepAfter(checkStep, targetStep);
      selectedIndex = selectedStepRef ? steps.indexOf(selectedStepRef) : null;
    }
    await applyCondition(checkStep, optionIndex, targetStep);
    targetStep._detached = false;
    renderCanvas();
    renderProperties();
  }

  // Полностью отвязывает targetIndex от последовательности — переносит шаг
  // в конец массива (нейтральное, всегда валидное место — steps не может
  // остаться без реальной позиции, см. пояснение пользователю) И помечает
  // его "не привязан" (_detached, как у только что созданного узла, см.
  // addNode), чтобы renderWires() не рисовал ему НИКАКОГО входящего
  // провода — ни от старого соседа, ни от нового (без этого шаг молча
  // "переподключался" к тому, что теперь оказалось перед ним — это и была
  // жалоба "не могу полностью отцепить"). Условие (condition_var/values),
  // если было, не трогаем — это отдельная связь, рвётся через
  // removeCondition/clearCondition.
  function detachFlow(targetIndex) {
    const step = steps[targetIndex];
    if (!step) return;
    const selectedStepRef = selectedIndex !== null ? steps[selectedIndex] : null;
    steps.splice(targetIndex, 1);
    steps.push(step);
    step._detached = true;
    selectedIndex = selectedStepRef ? steps.indexOf(selectedStepRef) : null;
    renderCanvas();
  }

  function removeCondition(targetIndex, value) {
    const step = steps[targetIndex];
    if (!step) return;
    step.condition_values = step.condition_values.filter((v) => v !== value);
    if (!step.condition_values.length) step.condition_var = "";
    renderWires();
    if (selectedIndex === targetIndex) renderProperties();
  }

  // Убирает условие целиком (а не одно значение) — по хватанию сокета
  // "условие" узла напрямую (см. renderCanvas), а не клику по конкретному
  // проводу конкретного варианта.
  function clearCondition(targetIndex) {
    const step = steps[targetIndex];
    if (!step) return;
    step.condition_var = "";
    step.condition_values = [];
    renderWires();
    if (selectedIndex === targetIndex) renderProperties();
  }

  function selectNode(index) {
    const prevSelected = worldEl.querySelector(".graph-node.selected");
    if (prevSelected) prevSelected.classList.remove("selected");
    selectedIndex = index;
    const nodes = worldEl.querySelectorAll(".graph-node");
    if (nodes[index]) nodes[index].classList.add("selected");
    stepFieldsController.resetVariantIndex();
    renderProperties();
  }

  function updateNodeHeader(index) {
    const nodes = worldEl.querySelectorAll(".graph-node");
    const node = nodes[index];
    if (!node) return;
    const titleDiv = node.querySelector(".graph-node-header > div:first-child");
    if (titleDiv) titleDiv.textContent = steps[index].title || `Этап ${index + 1}`;
  }

  function updateNodeBody(index) {
    const nodes = worldEl.querySelectorAll(".graph-node");
    const node = nodes[index];
    if (!node) return;
    const bodyDiv = node.querySelector(".graph-node-body");
    if (bodyDiv) bodyDiv.textContent = steps[index].description || "";
  }

  function startNodeDrag(e, index, node) {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault(); // см. onViewportMouseDown — иначе может начаться выделение текста
    selectNode(index);
    const step = steps[index];
    dragState = {
      index, node,
      startClientX: e.clientX, startClientY: e.clientY,
      startX: step.pos_x, startY: step.pos_y,
    };
    node.classList.add("dragging");
    document.addEventListener("mousemove", onNodeDragMove);
    document.addEventListener("mouseup", onNodeDragEnd);
  }

  function onNodeDragMove(e) {
    if (!dragState) return;
    const dx = (e.clientX - dragState.startClientX) / zoom;
    const dy = (e.clientY - dragState.startClientY) / zoom;
    const step = steps[dragState.index];
    step.pos_x = dragState.startX + dx;
    step.pos_y = dragState.startY + dy;
    dragState.node.style.left = step.pos_x + "px";
    dragState.node.style.top = step.pos_y + "px";
    renderWires();
  }

  function onNodeDragEnd() {
    if (dragState) dragState.node.classList.remove("dragging");
    dragState = null;
    document.removeEventListener("mousemove", onNodeDragMove);
    document.removeEventListener("mouseup", onNodeDragEnd);
  }

  // Новый узел появляется рядом с тем, где сейчас реально работает
  // пользователь — рядом с выбранным узлом (если есть), иначе в центре
  // видимой области холста — а не по формуле от числа шагов, которая
  // игнорирует, как узлы уже разложены (см. отчёт пользователя: раньше
  // каждый новый шаг просто съезжал всё ниже, независимо от раскладки).
  function viewportCenterWorld() {
    const rect = viewportEl.getBoundingClientRect();
    return { x: (rect.width / 2 - panX) / zoom, y: (rect.height / 2 - panY) / zoom };
  }

  function addNode() {
    const type = addTypeSelect.value;
    const index = steps.length;
    let pos;
    if (selectedIndex !== null && steps[selectedIndex]) {
      const s = steps[selectedIndex];
      pos = { x: s.pos_x + 240, y: s.pos_y };
    } else {
      pos = viewportCenterWorld();
    }
    // _detached — новый узел появляется НЕ привязанным ни к одному якорю
    // (см. отчёт пользователя): renderWires() не рисует ему входящий провод,
    // пока пользователь сам не перетащит на него провод потока/условия (см.
    // connectFlow/connectConditionWire — они снимают флаг).
    steps.push({ ...newStep(type), title: `Этап ${index + 1}`, pos_x: pos.x, pos_y: pos.y, _detached: true });
    renderCanvas();
    selectNode(index);
  }

  async function removeNode() {
    if (selectedIndex === null) return;
    if (steps.length <= 1) {
      await window.notice("Должен остаться хотя бы один этап.");
      return;
    }
    const [removedStep] = steps.splice(selectedIndex, 1);
    // Убираем удалённый узел из чужих mergeSources (см. connectFlow) —
    // иначе останется висячая ссылка на объект, которого больше нет в steps.
    steps.forEach((step) => {
      if (step._mergeSources) step._mergeSources = step._mergeSources.filter((s) => s !== removedStep);
    });
    selectedIndex = null;
    renderCanvas();
    renderProperties();
  }

  // ------------------------------------------------------------------
  // Панель свойств выбранного узла — тип-специфичные поля и условная
  // видимость из car_step_fields.js (общее с car_wizard.js).
  // ------------------------------------------------------------------
  function renderProperties() {
    clear(propsEl);
    if (selectedIndex === null || !steps[selectedIndex]) {
      propsEl.appendChild(el("p", { class: "app-desc", text: "Выберите узел, чтобы отредактировать его." }));
      return;
    }
    const step = steps[selectedIndex];
    const index = selectedIndex;

    propsEl.appendChild(el("span", { class: "field-label", text: "Название этапа" }));
    const titleInput = el("input", { type: "text", style: "margin-bottom: 10px" });
    titleInput.value = step.title;
    titleInput.addEventListener("input", () => {
      step.title = titleInput.value;
      updateNodeHeader(index);
    });
    propsEl.appendChild(titleInput);

    if (step.type !== "instruction") {
      propsEl.appendChild(el("span", { class: "field-label", text: "Описание (инструкция для этого этапа, необязательно)" }));
      const descArea = el("textarea", { style: "min-height: 60px; margin-bottom: 10px" });
      descArea.value = step.description;
      descArea.addEventListener("input", () => {
        step.description = descArea.value;
        updateNodeBody(index);
      });
      propsEl.appendChild(descArea);
    }

    stepFieldsController.renderTypeFields(step);
    // renderConditionFields (выпадающий список + чекбоксы, см.
    // car_step_fields.js) сюда сознательно не подключаем — условная
    // видимость здесь полностью выражается проводами на холсте (см.
    // renderWires/connectConditionWire/removeCondition), дублировать её текстовым
    // виджетом с автосгенерированным именем переменной незачем.
  }

  // ------------------------------------------------------------------
  // Сохранение — идентично car_wizard.js (тот же bridge-метод car_save).
  // ------------------------------------------------------------------
  function specToJson() {
    return { brand, model, modification, wifi, wifi_port: Number(wifiPort) || 5555, steps, changelog, status };
  }

  async function onSave() {
    if (!brand.trim() || !model.trim()) {
      await window.notice("Укажите марку и модель.");
      return;
    }
    const portNeeded = wifi || steps.some((s) => s.type === "apps" && (s.apps_connection === "wifi" || s.apps_connection === "ask"));
    if (portNeeded && !Number.isFinite(Number(wifiPort))) {
      await window.notice("Порт должен быть числом.");
      return;
    }

    const target = await window.pywebview.api.car_get_publish_target();
    if (target.mode === "admin" && !target.session_cached) {
      const ok = await window._openAdminLoginDialog(target.base_url);
      if (!ok) return; // пользователь отменил вход — не начинаем сохранение вовсе
    }

    const label = modification ? `${brand} / ${model} — ${modification}` : `${brand} / ${model}`;
    const result = await window.pywebview.api.car_save(specToJson(), editModelKey);
    if (!result.ok) {
      await window.notice(result.error, { title: "Сохранение", danger: true });
      return;
    }
    logFn(`Сохраняю «${label}»...`);
    setMainProgressVisible(true);
    dialog.close();
  }

  // ------------------------------------------------------------------
  // Публикация/отклонение заявки клиента (см. index.html: кнопки видны
  // только когда isPendingModel, submissions_api.py:publish/reject).
  // ------------------------------------------------------------------
  function waitForEvent(kind) {
    return new Promise((resolve) => {
      function handler(event) {
        window.events.off(kind, handler);
        resolve(event);
      }
      window.events.on(kind, handler);
    });
  }

  function setPendingButtonsDisabled(disabled) {
    document.getElementById("graph-wizard-save").disabled = disabled;
    document.getElementById("graph-wizard-publish").disabled = disabled;
    document.getElementById("graph-wizard-reject").disabled = disabled;
  }

  async function onPublish() {
    if (!brand.trim() || !model.trim()) {
      await window.notice("Укажите марку и модель.");
      return;
    }
    setPendingButtonsDisabled(true);
    // Публикация заливает то, что сейчас лежит в застейдженной папке — если
    // правки в редакторе ещё не сохранены, публикация уйдёт со старым
    // содержимым, поэтому сначала обычное "Сохранить" и ждём его результата.
    const saveResult = await window.pywebview.api.car_save(specToJson(), editModelKey);
    if (!saveResult.ok) {
      setPendingButtonsDisabled(false);
      await window.notice(saveResult.error, { title: "Сохранение", danger: true });
      return;
    }
    const savedEvent = await waitForEvent("car_save_finished");
    if (!savedEvent.success) {
      setPendingButtonsDisabled(false);
      return; // общий обработчик car_save_finished уже показал причину
    }
    logFn("Публикую заявку...");
    const publishResult = await window.pywebview.api.submissions_publish(editModelKey);
    if (!publishResult.ok) {
      setPendingButtonsDisabled(false);
      await window.notice(publishResult.error, { title: "Публикация", danger: true });
      return;
    }
    const finishedEvent = await waitForEvent("submissions_finished");
    setPendingButtonsDisabled(false);
    if (!finishedEvent.success) {
      await window.notice(finishedEvent.message, { title: "Публикация", danger: true });
      return;
    }
    logFn(finishedEvent.message);
    dialog.close();
    if (window.pendingList) window.pendingList.reload();
    window.mainPicker.reload();
  }

  async function onReject() {
    const label = modification ? `${brand} / ${model} — ${modification}` : `${brand} / ${model}`;
    const ok = await window.confirmDialog(`Отклонить заявку «${label}»? Это необратимо.`,
      { title: "Отклонить заявку" });
    if (!ok) return;
    setPendingButtonsDisabled(true);
    const result = await window.pywebview.api.submissions_reject(pendingSubmissionName);
    if (!result.ok) {
      setPendingButtonsDisabled(false);
      await window.notice(result.error, { title: "Отклонение", danger: true });
      return;
    }
    const finishedEvent = await waitForEvent("submissions_finished");
    setPendingButtonsDisabled(false);
    if (!finishedEvent.success) {
      await window.notice(finishedEvent.message, { title: "Отклонение", danger: true });
      return;
    }
    logFn(finishedEvent.message);
    dialog.close();
    if (window.pendingList) window.pendingList.reload();
  }

  window.graphWizard = { init, open };
})();
