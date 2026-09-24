// Android: при старте приветствие (Boosty) ждёт проверки обновления; есть обновление — только его окно
// (android/app/src/main/assets/js/app.js: startupPopups / onUpdateCheckResult).
"use strict";
const { read, slice, assert, run } = require("./_util");

function make(code) {
  const st = { welcome: 0, updateModals: 0, checks: 0, removedWelcome: 0, timers: [], welcomeOpen: false, modals: [] };
  const overlayStub = { querySelector: () => ({ classList: { add() {} } }), remove: () => { st.overlayRemoved = (st.overlayRemoved || 0) + 1; } };
  const ctx = {
    el: (...a) => ({ a }), showModal: (children, options) => { st.updateModals++; st.modals.push({ children, options }); return overlayStub; },
    maybeShowWelcomeModal: () => { st.welcome++; },
    checkForUpdate: () => { st.checks++; },
    document: { querySelector: (sel) => (sel === ".welcome-overlay" && st.welcomeOpen ? { remove: () => { st.removedWelcome++; } } : null) },
    setTimeout: (fn, ms) => { st.timers.push({ fn, ms }); return st.timers.length; },
    clearTimeout: (id) => { if (st.timers[id - 1]) st.timers[id - 1].cleared = true; },
  };
  run(code + "\nthis.__api={startupPopups,onUpdateCheckResult};", ctx);
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
  const download = (modal) => modal.children.find((node) => node.a[1] && node.a[1].text === "Скачать APK").a[1];
  download(mandatory).onclick();
  assert(!st.overlayRemoved, "обязательное: окно остаётся после «Скачать APK»");
  ({ st, api } = make(code)); api.onUpdateCheckResult({ result: update });
  download(st.modals[0]).onclick();
  assert(st.overlayRemoved === 1, "обычное: «Скачать APK» закрывает окно, как раньше");
};
