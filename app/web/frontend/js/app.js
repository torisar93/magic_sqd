// Бутстрап главного окна: разделитель, 3-шаговый пикер, панель лога, stage
// wizard, мелкие диалоги (usb/report/admin-upload) и мастер "Добавить/
// Изменить машину" (с редактором инструкции внутри).
let currentModel = null;

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

function onModelSelected(model) {
  currentModel = model;
  document.getElementById("report-btn").disabled = false;
  document.getElementById("edit-car-btn").disabled = !model.has_wizard_spec;
  window.stageWizard.open(model);
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
  window.instructionEditor.init();
  window.graphWizard.init(log);
  window.stageWizard.init(document.getElementById("install-content"), log);
  window.pendingList.init();

  // Раньше остального старта — в admin-сборке техник не должен увидеть ХОТЬ
  // ЧТО-ТО из интерфейса (список машин, панель лога и т.п.) без входа,
  // поэтому гейт стоит ДО sync_startup()/mainPicker.init() ниже, а не после
  // (см. app/web/api/admin_api.py: try_saved_login/login_only,
  // dialogs.js: adminLogin.requireLogin — диалог нельзя закрыть без
  // успешного входа).
  const info = await window.pywebview.api.app_get_info();
  if (info.admin_mode) {
    const saved = await window.pywebview.api.admin_try_saved_login();
    if (!saved.ok) {
      await window.adminLoginDialog.requireLogin();
    }
  }

  // ДО sync_startup() — иначе лог-события, которые синхронизация шлёт по
  // ходу (см. app/content_sync.py: sync_tree/list_files_recursive), летят в
  // window.__onBackendEvent (см. events.js) раньше, чем на "log" вообще
  // появится подписчик, и просто теряются: шина не буферизует события для
  // опоздавших слушателей. Из-за этого лог при первом запуске выглядел
  // пустым, будто программа зависла, хотя синхронизация шла нормально.
  window.events.on("log", (event) => log(event.text));

  const syncResult = await window.pywebview.api.sync_startup();
  await window.mainPicker.init(document.getElementById("picker"), { onModelSelected });

  if (info.admin_mode) {
    document.getElementById("admin-login-btn").style.display = "";
    document.getElementById("admin-upload-btn").style.display = "";
    document.getElementById("admin-add-apk-btn").style.display = "";
    document.getElementById("admin-browse-btn").style.display = "";
    document.getElementById("pending-section").style.display = "";
  }
  // DEBUG-сборка (см. main_web.py:_enable_debug_log_all) — показываем
  // client_id в углу, чтобы можно было сверить с папкой debug_logs/<id>/,
  // если дебаг-сборку поставили нескольким людям одновременно.
  if (info.debug_mode) {
    const badge = document.getElementById("debug-id-badge");
    badge.textContent = `DEBUG · ${info.client_id}`;
    badge.style.display = "";
  }

  // Автообновление — только техническая сборка (не admin, не debug, см.
  // "Область" в плане/checkForUpdate выше).
  window.events.on("update_log", (event) => log(event.text));
  window.events.on("update_finished", (event) => {
    if (!event.success) {
      window.notice(event.message || "Не удалось установить обновление.",
        { title: "Обновление", danger: true });
    }
    // при успехе окно скоро закроется само (см. update_api.py:_close_app) —
    // показывать больше нечего
  });
  if (!info.admin_mode && !info.debug_mode) {
    checkForUpdate(); // fire-and-forget, не блокирует остальной старт
    // Только техническая сборка (не admin/debug) — те же соображения, что
    // и у автообновления выше: админ и без того поддерживает проект своей
    // работой, ему не нужен донат-попап.
    window.boostyDialogs.maybeShowWelcomeDialog();
  }

  document.getElementById("add-car-btn").addEventListener("click", () =>
    window.graphWizard.open(null, window.mainPicker.getBrands(),
      (brand, modelName, modification, dir) => onCarCreated(brand, modelName, modification, dir, null)));
  document.getElementById("edit-car-btn").addEventListener("click", () => {
    const editingKey = currentModel ? currentModel.key : null;
    window.graphWizard.open(currentModel, window.mainPicker.getBrands(),
      (brand, modelName, modification, dir) => onCarCreated(brand, modelName, modification, dir, editingKey));
  });
  document.getElementById("report-btn").addEventListener("click", () => window.reportDialog.open(currentModel));
  document.getElementById("admin-login-btn").addEventListener("click", () => window.adminLoginDialog.open());
  document.getElementById("admin-upload-btn").addEventListener("click", () => window.adminDialog.open());
  document.getElementById("admin-add-apk-btn").addEventListener("click", () => window.adminApkDialog.open());
  document.getElementById("admin-browse-btn").addEventListener("click", () => window.adminBrowseDialog.open());

  document.getElementById("adb-console-refresh").addEventListener("click", () => refreshAdbConsoleDevices());
  document.getElementById("adb-console-send").addEventListener("click", () => sendAdbConsoleCommand());
  document.getElementById("adb-console-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") sendAdbConsoleCommand();
  });
  refreshAdbConsoleDevices();

  if (syncResult.changes && syncResult.changes.length > 0) {
    showUpdatesNotice(syncResult.changes);
  }
});
