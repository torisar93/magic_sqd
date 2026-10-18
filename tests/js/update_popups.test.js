// Android: при старте приветствие (Boosty) ждёт проверки обновления; есть обновление — только его окно
// (android/app/src/main/assets/js/app.js: startupPopups / onUpdateCheckResult).
"use strict";
const { read, slice, assert, run } = require("./_util");

function make(code, bridge = {}) {
  const st = { welcome: 0, updateModals: 0, checks: 0, removedWelcome: 0, timers: [], welcomeOpen: false, modals: [],
    calls: [] };
  const overlayStub = { querySelector: () => ({ classList: { add() {} } }), remove: () => { st.overlayRemoved = (st.overlayRemoved || 0) + 1; } };
  const ctx = {
    el: (...a) => ({ a }), showModal: (children, options) => { st.updateModals++; st.modals.push({ children, options }); return overlayStub; },
    maybeShowWelcomeModal: () => { st.welcome++; },
    checkForUpdate: () => { st.checks++; },
    document: { querySelector: (sel) => (sel === ".welcome-overlay" && st.welcomeOpen ? { remove: () => { st.removedWelcome++; } } : null) },
    setTimeout: (fn, ms) => { st.timers.push({ fn, ms }); return st.timers.length; },
    clearTimeout: (id) => { if (st.timers[id - 1]) st.timers[id - 1].cleared = true; },
    Bridge: { call: (method, args) => { st.calls.push([method, args]); return bridge[method] ? bridge[method](args) : {}; } },
  };
  run(code + "\nthis.__api={startupPopups,onUpdateCheckResult,startAppUpdate,resumeAppUpdate,onAppUpdateEvent};", ctx);
  return { st, api: ctx.__api };
}

module.exports = async function () {
  const file = "android/app/src/main/assets/js/app.js";
  const src = read(file);
  const start = src.indexOf("  const WELCOME_WAIT_MS");
  const fn = src.indexOf("  function onUpdateCheckResult", start);
  const end = src.indexOf("\n  }\n", fn) + 5;
  assert(start > 0 && fn > 0 && end > fn, "маркеры startupPopups/onUpdateCheckResult найдены");
  const code = src.slice(start, end);
  const update = { available: true, version: "1.0.17", changelog: "x", download_url: "u" };

  let { st, api } = make(code);
  api.startupPopups();
  assert(st.checks === 1 && st.welcome === 0 && st.timers[0].ms === 4000, "приветствие ждёт ответа проверки");
  api.onUpdateCheckResult({ result: update });
  assert(st.updateModals === 1 && st.welcome === 0 && st.timers[0].cleared, "есть обновление -> только окно обновления");

  ({ st, api } = make(code)); api.startupPopups(); api.onUpdateCheckResult({ result: { available: false } });
  assert(st.updateModals === 0 && st.welcome === 1, "нет обновления -> приветствие");

  ({ st, api } = make(code)); api.startupPopups(); api.onUpdateCheckResult({ result: null });
  assert(st.welcome === 1 && st.updateModals === 0, "сбой проверки -> приветствие");

  ({ st, api } = make(code)); api.startupPopups(); st.timers[0].fn(); st.welcomeOpen = true;
  assert(st.welcome === 1, "по таймеру приветствие показано");
  api.onUpdateCheckResult({ result: update });
  assert(st.removedWelcome === 1 && st.updateModals === 1, "поздний ответ заменяет приветствие окном обновления");

  ({ st, api } = make(code)); api.startupPopups(); api.onUpdateCheckResult({ result: update });
  if (!st.timers[0].cleared) st.timers[0].fn();
  assert(st.welcome === 0, "таймер после ответа не показывает приветствие");

  // Обязательное обновление (владелец, 2026-09-25): без «Позже», тап мимо и «Назад» не закрывают окно.
  const texts = (modal) => modal.children.map((node) => (node.a[1] && node.a[1].text) || "");
  ({ st, api } = make(code)); api.onUpdateCheckResult({ result: update });
  assert(st.modals[0].options.dismissible === true && texts(st.modals[0]).includes("Позже"), "обычное: можно отложить");
  ({ st, api } = make(code)); api.onUpdateCheckResult({ result: { ...update, mandatory: true } });
  const mandatory = st.modals[0];
  assert(mandatory.options.dismissible === false, "обязательное: закрыть тапом мимо/«Назад» нельзя");
  assert(!texts(mandatory).includes("Позже"), "обязательное: без «Позже» — " + texts(mandatory).join("|"));
  assert(texts(mandatory).some((t) => t.startsWith("Обязательное обновление")), "обязательное: так и названо");
  // С 1.1.12 программа ставит обновление сама (AppSelfUpdate.kt; владелец 18.10.2026): «Обновить» вместо «Скачать APK»,
  // ссылка на APK — запасной путь, видна после сбоя.
  const node = (modal, text) => modal.children.find((n) => n.a && n.a[1] && n.a[1].text === text);
  const sha = "a".repeat(64);
  let can = false;
  ({ st, api } = make(code, { app_update_status: () => ({ can, silent: true }) }));
  api.onUpdateCheckResult({ result: { ...update, sha256: sha } });
  const modal = st.modals[0];
  const button = node(modal, "Обновить"), manual = node(modal, "Скачать APK вручную");
  const status = modal.children.find((n) => n.a && n.a[1] && n.a[1].class === "stage-text update-status");
  assert(button && manual && status && manual.a[1].hidden === "" && !node(modal, "Скачать APK"), "окно: «Обновить», ссылка скрыта");
  // Нет разрешения на установку — открываем настройки, ждём возврата; вернулись с разрешением — ставим сами.
  button.a[1].onclick();
  assert(st.calls.map((c) => c[0]).join() === "app_update_status,app_update_allow", "без разрешения — настройки: " + st.calls.map((c) => c[0]));
  assert(/Разрешить установку из этого источника/.test(status.textContent), "подсказка про разрешение");
  api.resumeAppUpdate();
  assert(!st.calls.some((c) => c[0] === "app_update_install"), "вернулись без разрешения — снова ждём");
  can = true;
  api.resumeAppUpdate();
  const install = st.calls.find((c) => c[0] === "app_update_install");
  assert(install && install[1].url === "u" && install[1].sha256 === sha && button.disabled === true, "с разрешением — ставим");
  api.onAppUpdateEvent({ stage: "download", done: 3 * 1048576, total: 64 * 1048576 });
  assert(/^Скачиваю обновление: 3 МБ из 64 МБ$/.test(status.textContent.replace(/\u00a0/g, " ")), "ход: " + status.textContent);
  api.onAppUpdateEvent({ stage: "install", silent: true });
  assert(/закроется на несколько секунд/.test(status.textContent), "Android 12+: без окна, программа закроется");
  // Android 8–11: окно подтверждения Android — «Установить»; «Открыть» в нём нет (сеанс установки), программа закроется.
  api.onAppUpdateEvent({ stage: "confirm" });
  assert(/«Установить»/.test(status.textContent) && /закроется/.test(status.textContent) && !/«Открыть»/.test(status.textContent),
    "подтверждение: " + status.textContent);
  api.onAppUpdateEvent({ stage: "error", message: "файл подписан другим ключом" });
  assert(button.disabled === false && button.textContent === "Повторить" && manual.hidden === false
    && /подписан другим ключом/.test(status.textContent), "сбой: «Повторить» и ссылка на APK");
  // Окно пришло бандлом интерфейса к программе старше 1.1.12: моста обновления нет («Неизвестный метод») — «Обновить»
  // открывает ссылку на APK, как прежняя «Скачать APK», а не застревает на разрешении.
  ({ st, api } = make(code, { app_update_status: () => ({ error: "Неизвестный метод: app_update_status" }) }));
  api.onUpdateCheckResult({ result: update });
  const oldManual = node(st.modals[0], "Скачать APK вручную");
  let opened = 0;
  oldManual.click = () => { opened++; oldManual.a[1].onclick(); };
  node(st.modals[0], "Обновить").a[1].onclick();
  assert(opened === 1 && oldManual.hidden === false && st.calls.map((c) => c[0]).join() === "app_update_status",
    "старая программа: ссылка вместо установки — " + st.calls.map((c) => c[0]));
  assert(st.overlayRemoved === 1, "старая программа, обычное обновление: окно закрылось, как после «Скачать APK»");
  // Обязательное: ссылка на APK окно не закрывает; обычное — закрывает, как раньше «Скачать APK».
  ({ st, api } = make(code)); api.onUpdateCheckResult({ result: { ...update, mandatory: true } });
  node(st.modals[0], "Скачать APK вручную").a[1].onclick();
  assert(!st.overlayRemoved, "обязательное: окно остаётся");
  ({ st, api } = make(code)); api.onUpdateCheckResult({ result: update });
  node(st.modals[0], "Скачать APK вручную").a[1].onclick();
  assert(st.overlayRemoved === 1, "обычное: ссылка закрывает окно");
};
