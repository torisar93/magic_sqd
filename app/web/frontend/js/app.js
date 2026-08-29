// Бутстрап главного окна: разделитель, 3-шаговый пикер, панель лога, stage
// wizard, мелкие диалоги (usb/report/admin-upload) и мастер "Добавить/
// Изменить машину" (с редактором инструкции внутри).
let currentModel = null;
let stageUiReady = null;

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
  line.textContent = text;
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

function sendAdbConsoleCommand() {
  const input = document.getElementById("adb-console-input");
  const command = input.value.trim();
  if (!command) return;
  input.value = "";
  const select = document.getElementById("adb-console-device");
  const device = adbConsoleDeviceByLabel[select.value] || null;
  window.pywebview.api.install_console_send(device, command);
  const firstWord = command.split(/\s+/, 1)[0].toLowerCase();
  if (ADB_DEVICE_LIST_COMMANDS.has(firstWord)) {
    setTimeout(refreshAdbConsoleDevices, 1500);
  }
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
  let update;
  try {
    update = await window.pywebview.api.update_check();
  } catch (err) {
    return;
  }
  if (!update.available) return;
  const message = `Доступна новая версия: ${update.version}\n\n`
    + `Что нового:\n${update.changelog || "—"}\n\n`
    + `Скачать и установить сейчас? Программа закроется и перезапустится после установки.`;
  const confirmed = await window.confirmDialog(message, { title: "Обновление Magic SQD" });
  if (!confirmed) return;
  const result = await window.pywebview.api.update_install(update.download_url);
  if (!result.ok) {
    await window.notice(result.error || "Не удалось начать обновление.",
      { title: "Обновление", danger: true });
  }
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
      if (catalogWasEmpty) window.mainPicker.hideStartupLoading();
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

  window.events.on("update_log", (event) => log(event.text));
  window.events.on("update_finished", (event) => {
    if (!event.success) {
      window.notice(event.message || "Не удалось установить обновление.",
        { title: "Обновление", danger: true });
    }
    // при успехе окно скоро закроется само (см. update_api.py:_close_app) —
    // показывать больше нечего
  });
  // Раньше пропускалось и для admin_mode тоже — имело смысл, пока
  // admin-сборка была ОТДЕЛЬНОЙ (публиковалась только вручную), но после
  // объединения сборок (см. app/web/bridge.py: WebApi.is_win7/admin_mode)
  // admin_mode — это просто переключатель на той же самой установленной
  // копии, которой реально пользуются как рабочей; она не должна навсегда
  // переставать проверять обновления после одной разблокировки. debug_mode
  // всё ещё пропускает — не хотим прерывать диагностику неожиданным
  // автообновлением посреди сессии.
  if (!info.debug_mode) {
    checkForUpdate(); // fire-and-forget, не блокирует остальной старт
  }
  if (!info.admin_mode && !info.debug_mode && !catalogWasEmpty) {
    // Админ и без того поддерживает проект своей работой, ему не нужен
    // донат-попап. catalogWasEmpty (см. выше) — признак самого первого
    // запуска (каталог ещё не синхронизирован ни разу): просить донат
    // раньше, чем человек хоть раз воспользовался программой, неуместно.
    window.boostyDialogs.maybeShowWelcomeDialog();
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
    const expanded = card.classList.toggle("is-expanded");
    const button = document.getElementById("log-toggle");
    button.textContent = expanded ? "Свернуть" : "Развернуть";
    button.setAttribute("aria-expanded", String(expanded));
  });

  document.getElementById("adb-console-refresh").addEventListener("click", () => refreshAdbConsoleDevices());
  document.getElementById("adb-console-send").addEventListener("click", () => sendAdbConsoleCommand());
  document.getElementById("adb-console-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") sendAdbConsoleCommand();
  });
  refreshAdbConsoleDevices();

});
