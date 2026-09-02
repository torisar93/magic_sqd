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
//
// Граф исполнения — полностью явный, никакого "порядка по списку +
// отдельные условия" (см. историю: раньше был именно так, путало —
// одновременно два разных вида проводов, узел мог "зависеть от условия
// где-то в другом месте списка", а последовательное продолжение после
// обычного шага не показывалось вовсе, если у цели уже было условие).
// Теперь у КАЖДОГО узла, кроме "Проверка/выбор", РОВНО один вход (слева,
// на него может прийти сколько угодно проводов от разных источников — это
// нормально, значит несколько разных путей ведут к одному и тому же этапу)
// и РОВНО один выход (справа) — во что он превращается, храним прямо в
// самом узле (StepSpec.next — id следующего этапа или null, конец
// установки). "Проверка/выбор" — тот же один вход слева, но отдельный
// выход справа НА КАЖДЫЙ вариант (StepSpec.next_options, тот же индекс,
// что и check_options). Порядок элементов в массиве steps ни на что не
// влияет, КРОМЕ steps[0] — это всегда точка входа (провод от псевдо-узла
// "Начало"); перетащить провод "Начала" на другой узел — сделать ЕГО
// точкой входа (переставляет его на позицию 0, больше ничего не меняя).
// Обычное перетаскивание провода между двумя узлами НИКОГДА не переставляет
// их в массиве — просто записывает id цели в .next/.next_options[i]
// источника. Провода кликабельны для разрыва связи, у входного сокета —
// то же самое хватанием напрямую (убирает ВСЕ провода, ведущие в этот
// узел, откуда бы они ни шли); во время перетаскивания подходящие
// сокеты-цели увеличиваются (см. highlightDropTargets).
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
    qr_adb: "Пароль ADB по QR-коду",
  };
  // Показ уже существующего узла типа "adb" (если такой встретится при
  // открытии старой, ещё не мигрированной модели) — не в STEP_TYPE_LABELS
  // выше, чтобы не предлагать его как ВЫБОР для нового узла.
  const LEGACY_STEP_TYPE_LABELS = { ...STEP_TYPE_LABELS, adb: "ADB-команды (устар.)" };

  const MIN_ZOOM = 0.4;
  const MAX_ZOOM = 2;
  // Слева направо, а не сверху вниз — коннекторы теперь только слева/
  // справа узла (см. renderCanvas), и сам холст шире, чем выше.
  const LAYOUT_STEP_X = 260;
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
  // Слева от первого узла (тот стоит на LAYOUT_START по x), а не сверху —
  // см. LAYOUT_STEP_X.
  const START_POS = { x: -120, y: 40 };

  // Следующий свободный id для нового узла — максимальный уже занятый + 1
  // (пересчитывается из текущего steps каждый раз, отдельного счётчика не
  // держим — не нужно синхронизировать с undo/отменой и т.п.).
  function nextFreeId() {
    return 1 + steps.reduce((max, s) => Math.max(max, s.id ?? -1), -1);
  }

  function indexById(id) {
    return steps.findIndex((s) => s.id === id);
  }

  function newStep(type) {
    return {
      type, title: "", description: "", instruction_blocks: [],
      usb_files: [], usb_copy_selected_apks: false, usb_apks_dest: "", usb_shared_folder: "",
      commands: [], adb_install_selected_apks: false, adb_files: [],
      standard_apks: [], standard_apks_optional: [], apps_connection: "wired", apps_install_method: "",
      actions_connection: "wired",
      exe_file: null, uart_baudrate: 115200, actions: [],
      check_options: [],
      id: null, next: null, next_options: [],
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
      propsEl, () => steps, () => ({ brand, model }), () => { renderCanvas(); renderProperties(); });

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
    // id уже гарантированно проставлен car_generator.py:load_car_spec (даже
    // для моделей, сохранённых до перехода на граф — там id считается по
    // позиции в списке). Тут только подгоняем длину next_options под
    // check_options — на случай, если варианты добавляли/убирали в
    // старом текстовом формате, где next_options не было вовсе.
    steps.forEach((step) => {
      if (step.type !== "check") return;
      if (!Array.isArray(step.next_options)) step.next_options = [];
      while (step.next_options.length < step.check_options.length) step.next_options.push(null);
      step.next_options.length = step.check_options.length;
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
        step.pos_x = LAYOUT_START + i * LAYOUT_STEP_X;
        step.pos_y = LAYOUT_START;
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
    startOut.addEventListener("mousedown", (e) => startWireDrag(e, -1));
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
      // Один вход слева — сюда может прийти сколько угодно проводов от
      // разных узлов (несколько разных путей ведут к одному и тому же
      // этапу — это нормально). Хватание сокета напрямую убирает ВСЕ
      // входящие провода разом (см. clearIncoming) — более "нащупываемый"
      // способ разорвать связь, раз тонкую линию кликнуть трудно.
      const inSocket = el("div", { class: "graph-socket graph-socket-in" });
      inSocket.addEventListener("mousedown", (e) => { e.stopPropagation(); clearIncoming(step.id); });

      const children = [inSocket, header, body];

      if (step.type === "check") {
        // "Проверка/выбор" — один выход НА КАЖДЫЙ вариант (справа), вместо
        // общего выхода узла.
        const outputsWrap = el("div", { class: "graph-node-check-outputs" });
        step.check_options.forEach((option, optIndex) => {
          const dot = el("div", { class: "graph-socket-check-out" });
          dot.addEventListener("mousedown", (e) => startWireDrag(e, i, optIndex));
          outputsWrap.appendChild(el("div", { class: "graph-check-option-row" }, [el("span", { text: option }), dot]));
        });
        children.push(outputsWrap);
      } else {
        // Любой другой тип — ровно один выход справа.
        const outSocket = el("div", { class: "graph-socket graph-socket-out" });
        outSocket.addEventListener("mousedown", (e) => startWireDrag(e, i));
        children.push(outSocket);
      }

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
      return { x: START_POS.x + w, y: START_POS.y + h / 2 };
    }
    const nodes = worldEl.querySelectorAll(".graph-node");
    const node = nodes[index];
    const step = steps[index];
    if (!node || !step) return null;
    const w = node.offsetWidth, h = node.offsetHeight;
    if (role === "in") return { x: step.pos_x, y: step.pos_y + h / 2 };
    if (role === "out") return { x: step.pos_x + w, y: step.pos_y + h / 2 };
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
    const dots = node.querySelectorAll(".graph-socket-check-out");
    const dot = dots[optionIndex];
    if (!dot) return null;
    return {
      x: step.pos_x + dot.offsetLeft + dot.offsetWidth / 2,
      y: step.pos_y + dot.offsetTop + dot.offsetHeight / 2,
    };
  }

  // Горизонтальная кривая (вход/выход теперь слева/справа узла, а не
  // сверху/снизу) — выгибается по X, а не по Y.
  function wirePath(p1, p2) {
    const midX = (p1.x + p2.x) / 2;
    return `M ${p1.x} ${p1.y} C ${midX} ${p1.y}, ${midX} ${p2.y}, ${p2.x} ${p2.y}`;
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

    // "Начало" -> steps[0] — steps[0] всегда точка входа (см. шапку файла).
    // Не кликабелен на разрыв — единственный способ сменить точку входа —
    // перетащить провод "Начала" на другой узел (см. onWireDragEnd), это
    // ставит ЕГО на позицию 0 автоматически.
    appendWire("graph-wire-flow", socketPos(-1, "out"), socketPos(0, "in"), null);

    // Дальше — то, что реально хранится в самих узлах (StepSpec.next/
    // next_options), а не порядок массива. Несколько узлов могут независимо
    // указывать next на ОДИН и тот же id — тогда сюда просто придёт
    // несколько проводов, это штатный случай "разные пути ведут к одному
    // этапу" (см. отчёт пользователя), никакой отдельной обработки не
    // требует: каждый источник рисует и разрывает СВОЙ провод сам по себе.
    steps.forEach((step, i) => {
      if (step.type === "check") {
        step.check_options.forEach((_, optIndex) => {
          const targetId = step.next_options[optIndex];
          if (targetId == null) return;
          const targetIndex = indexById(targetId);
          if (targetIndex === -1) return;
          const from = checkOptionSocketPos(i, optIndex);
          const to = socketPos(targetIndex, "in");
          if (!from || !to) return;
          appendWire("graph-wire-flow", from, to, () => {
            step.next_options[optIndex] = null;
            renderWires();
            if (selectedIndex === i) renderProperties();
          });
        });
        return;
      }
      if (step.next == null) return;
      const targetIndex = indexById(step.next);
      if (targetIndex === -1) return;
      const from = socketPos(i, "out");
      const to = socketPos(targetIndex, "in");
      if (!from || !to) return;
      appendWire("graph-wire-flow", from, to, () => {
        step.next = null;
        renderWires();
        if (selectedIndex === i) renderProperties();
      });
    });
  }

  // Перетаскивание нового провода от сокета "выход" узла-источника —
  // sourceIndex === -1 значит источник — псевдо-узел "Начало".
  // optionIndex задан только для выхода конкретного варианта узла
  // "Проверка/выбор". Отпущено не над сокетом "вход" — просто отменяется
  // (см. onWireDragEnd), никакое состояние не остаётся висеть.
  function startWireDrag(e, sourceIndex, optionIndex) {
    if (e.button !== 0) return;
    e.stopPropagation();
    e.preventDefault(); // см. onViewportMouseDown — иначе может начаться выделение текста
    const from = optionIndex === undefined ? socketPos(sourceIndex, "out") : checkOptionSocketPos(sourceIndex, optionIndex);
    if (!from) return;
    const tempPath = svgEl("path", { class: "graph-wire graph-wire-flow graph-wire-dragging" });
    wiresEl.appendChild(tempPath);
    wireDragState = { sourceIndex, optionIndex, from, tempPath };
    document.addEventListener("mousemove", onWireDragMove);
    document.addEventListener("mouseup", onWireDragEnd);
    highlightDropTargets();
  }

  // Во время перетаскивания провода — увеличиваем сокет "вход" у всех узлов
  // (крупнее и заметнее, см. .graph-drop-target в graph.css). Маленькая
  // (12px) точка на конце длинного перетаскивания — сама по себе источник
  // промахов, из-за которых казалось, что часть проводов "не получается
  // провести" (см. отчёт пользователя) — крупная цель решает это прямее,
  // чем гадать, почему именно промахивается конкретное перетаскивание.
  function highlightDropTargets() {
    worldEl.querySelectorAll(".graph-socket-in").forEach((s) => s.classList.add("graph-drop-target"));
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
    const { sourceIndex, optionIndex, tempPath } = wireDragState;
    tempPath.remove();
    wireDragState = null;

    // Хит-тест ДО снятия подсветки — пока цели ещё увеличены (см.
    // highlightDropTargets), чтобы реальная кликабельная область на
    // момент проверки совпадала с тем, что видел пользователь.
    const hit = document.elementFromPoint(e.clientX, e.clientY);
    clearDropTargetHighlights();

    const socket = hit ? hit.closest(".graph-socket-in") : null;
    const nodeEl = socket ? socket.closest(".graph-node") : null;
    const targetIndex = nodeEl ? Array.from(worldEl.querySelectorAll(".graph-node")).indexOf(nodeEl) : -1;
    if (targetIndex === -1) {
      renderWires();
      return;
    }

    if (sourceIndex === -1) {
      // "Начало" -> узел: делаем ЕГО точкой входа — переставляем на
      // позицию 0, больше ничего не меняя (никакие .next/.next_options не
      // трогаем — это ортогонально тому, кто на кого ссылается).
      const selectedStepRef = selectedIndex !== null ? steps[selectedIndex] : null;
      const [moved] = steps.splice(targetIndex, 1);
      steps.unshift(moved);
      selectedIndex = selectedStepRef ? steps.indexOf(selectedStepRef) : null;
      renderCanvas();
      return;
    }

    const source = steps[sourceIndex];
    const target = steps[targetIndex];
    if (!source || !target || source === target) {
      renderWires();
      return;
    }
    if (source.type === "check") {
      source.next_options[optionIndex] = target.id;
    } else {
      source.next = target.id;
    }
    renderCanvas();
    renderProperties();
  }

  // Убирает ВСЕ провода, ведущие в узел с этим id, откуда бы они ни шли —
  // по хватанию его сокета "вход" напрямую (см. renderCanvas).
  function clearIncoming(targetId) {
    steps.forEach((s) => {
      if (s.type === "check") {
        s.next_options = s.next_options.map((v) => (v === targetId ? null : v));
      } else if (s.next === targetId) {
        s.next = null;
      }
    });
    renderWires();
    renderProperties();
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
    // Новый узел получает свободный id сразу (нужен, чтобы на него можно
    // было провести провод немедленно) и появляется без единой связи — ни
    // входящей, ни исходящей — пока пользователь сам не протянет провод.
    steps.push({ ...newStep(type), id: nextFreeId(), title: `Этап ${index + 1}`, pos_x: pos.x, pos_y: pos.y });
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
    // Убираем удалённый узел из чужих next/next_options — иначе останется
    // висячая ссылка на id, которого больше нет в steps.
    clearIncoming(removedStep.id);
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
    // "Куда дальше" отсюда не редактируется текстом — только проводами на
    // холсте (см. renderWires/onWireDragEnd/clearIncoming).
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
