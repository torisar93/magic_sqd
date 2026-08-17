// Бутстрап главного окна: разделитель, 3-шаговый пикер, панель лога, stage
// wizard, мелкие диалоги (usb/report/admin-upload) и мастер "Добавить/
// Изменить машину" (с редактором инструкции внутри).
let currentModel = null;

function log(text) {
  const el = document.getElementById("log-panel");
  const line = document.createElement("div");
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

// До подключения устройство ещё НЕ в списке "adb devices" — выбирать пока
// нечего, поэтому "Подключить Wi-Fi" не привязана к выбору из выпадающего
// списка выше (в отличие от обычных команд консоли). Сначала пробуем
// автоопределение IP по шлюзу текущей Wi-Fi-сети (см. get_default_gateway_ip
// в adb_utils.py) — работает, только если ноутбук подключён к собственной
// точке доступа магнитолы; если это не сработало (или само подключение по
// автоопределённому IP не удалось), предлагаем ввести IP вручную —
// отдельного поля ввода в строке консоли ради этого не держим, оно того не
// стоит для случая, который нужен нечасто.
async function connectAdbWifi() {
  const btn = document.getElementById("adb-console-wifi-connect");
  // Порт — не всегда 5555 (например Cityray со значком Wi-Fi — 7777), поэтому
  // берём из поля рядом с кнопкой, а не хардкодим (см. index.html:
  // #adb-console-wifi-port).
  const portInput = document.getElementById("adb-console-wifi-port");
  const port = parseInt(portInput.value, 10) || 5555;
  btn.disabled = true;
  try {
    let result = await window.pywebview.api.install_wifi_connect(port, null);
    if (!result.ok) {
      // Автоопределение по шлюзу не сработало — сканируем локальную сеть на
      // тот же порт (случай, когда магнитола сама подключилась к сети/точке
      // доступа ноутбука, см. install_api.py:scan_wifi) и предлагаем выбрать
      // из найденного; пункт "Ввести вручную..." в этом диалоге есть всегда,
      // даже если скан ничего не нашёл.
      const candidates = await window.pywebview.api.install_scan_wifi(port);
      const ip = (await window.selectDialog(
        candidates.length
          ? `Не удалось подключиться автоматически. Выберите IP магнитолы (порт ${port}):`
          : `Не удалось подключиться автоматически и скан сети ничего не нашёл (порт ${port}). Введите IP магнитолы:`,
        candidates,
        { title: "Wi-Fi ADB" }
      ))?.trim();
      if (!ip) return;
      result = await window.pywebview.api.install_wifi_connect(port, ip);
      if (!result.ok) {
        await window.notice(result.message || result.error || "Не удалось подключиться.",
          { title: "Wi-Fi ADB", danger: true });
        return;
      }
    }
    await refreshAdbConsoleDevices();
  } finally {
    btn.disabled = false;
  }
}

function onModelSelected(model) {
  currentModel = model;
  document.getElementById("report-btn").disabled = false;
  document.getElementById("edit-car-btn").disabled = !model.has_wizard_spec;
  window.stageWizard.open(model);
}

function onCarCreated(brand, modelName, modification) {
  const label = modification ? `${brand} / ${modelName} — ${modification}` : `${brand} / ${modelName}`;
  log(`Сохранена модель: ${label}`);
  window.mainPicker.reload();
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
  window.instructionEditor.init();
  window.graphWizard.init(log);
  window.stageWizard.init(document.getElementById("install-content"), log);

  // ДО sync_startup() — иначе лог-события, которые синхронизация шлёт по
  // ходу (см. app/content_sync.py: sync_tree/list_files_recursive), летят в
  // window.__onBackendEvent (см. events.js) раньше, чем на "log" вообще
  // появится подписчик, и просто теряются: шина не буферизует события для
  // опоздавших слушателей. Из-за этого лог при первом запуске выглядел
  // пустым, будто программа зависла, хотя синхронизация шла нормально.
  window.events.on("log", (event) => log(event.text));

  const syncResult = await window.pywebview.api.sync_startup();
  await window.mainPicker.init(document.getElementById("picker"), { onModelSelected });

  const info = await window.pywebview.api.app_get_info();
  if (info.admin_mode) {
    document.getElementById("admin-login-btn").style.display = "";
    document.getElementById("admin-upload-btn").style.display = "";
    document.getElementById("admin-add-apk-btn").style.display = "";
    document.getElementById("admin-browse-btn").style.display = "";
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
  }

  document.getElementById("add-car-btn").addEventListener("click", () =>
    window.graphWizard.open(null, window.mainPicker.getBrands(), onCarCreated));
  document.getElementById("edit-car-btn").addEventListener("click", () =>
    window.graphWizard.open(currentModel, window.mainPicker.getBrands(), onCarCreated));
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
  document.getElementById("adb-console-wifi-connect").addEventListener("click", () => connectAdbWifi());
  refreshAdbConsoleDevices();

  if (syncResult.changes && syncResult.changes.length > 0) {
    showUpdatesNotice(syncResult.changes);
  }
});
