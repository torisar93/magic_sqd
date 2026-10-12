// Android: «Назад» посреди операции Kotlin, о которой JS не знает (кнопки «Доп. действий» через выбор приложения —
// «Работа в движении», удаление, запуск). Раньше выход из модели проходил, а новое открытие модели отключало ADB:
// «Работа в движении» сняла приложение, помеченная версия так и не встала (лог №5242, Haval F7 New, 12.10.2026).
// (android/app/src/main/assets/js/app.js: operationBusy, __handleBackPress; WebBridge.kt: "operation_busy")
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "android/app/src/main/assets/js/app.js";
  const src = read(file);
  const busy = slice(src, "  function showLabBusyNotice() {", "  // Android WebView без кастомного WebChromeClient", file);
  const back = slice(src, "  window.__handleBackPress = function () {", "  function onAdbAskInput(", file);
  const logs = [], notices = [], screens = [];
  let kotlinBusy = true;
  const ctx = {
    window: {}, document: { querySelector: () => null },
    closePhotoLightbox: () => false, closeDismissibleModal: () => false,
    logOverlayEl: { classList: { contains: () => false } },
    screenWizard: { classList: { contains: () => true } },
    historyStack: [], labInstallBusy: false, usbOperation: null, stageOperation: null,
    Bridge: { call: (method) => { if (method !== "operation_busy") throw new Error(method); return { busy: kotlinBusy }; } },
    log: (line) => logs.push(line), showLabNotice: (title) => notices.push(title),
    flushSessionLog() {}, showScreen: (name) => screens.push(name),
  };
  run(busy + back, ctx);

  assert(ctx.window.__handleBackPress() === "handled", "назад обработан");
  assert(screens.length === 0, "из модели не вышли, пока идёт операция Kotlin");
  assert(logs.at(-1) === "Нажата «Назад» во время операции — программа попросила дождаться её окончания.", logs.at(-1));
  assert(notices.at(-1) === "Операция выполняется", notices.at(-1));

  kotlinBusy = false;
  ctx.window.__handleBackPress();
  assert(screens.at(-1) === "picker", "операции нет — выходим из модели");

  // Старая программа без "operation_busy" (бандл интерфейса новее APK) — как раньше, по одному labInstallBusy.
  ctx.Bridge.call = () => { throw new Error("неизвестное действие"); };
  ctx.window.__handleBackPress();
  assert(screens.at(-1) === "picker" && screens.length === 2, "без ответа Kotlin выход не блокируется");
};
