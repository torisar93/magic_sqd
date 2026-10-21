// Android: «Работа в движении» — пока операция идёт, у карточки кнопка «Остановить» (останавливает только снятие
// APK: WebBridge «adb_cancel_install» с what=motion), по концу операции кнопка уходит. Лог №8317: Spotify снимался по
// Wi-Fi 4 минуты молча, техник решил, что программа зависла. (android/app/src/main/assets/js/app.js: watchMotionOptimize)
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "android/app/src/main/assets/js/app.js";
  const src = read(file);
  assert(src.includes('if (action.kind === "motion_optimize") watchMotionOptimize(card, note);'),
    "кнопка появляется после запуска «Работы в движении»");
  const watch = slice(src, "  function watchMotionOptimize(card, note) {", "  function usbStageButton(", file);
  const calls = [], appended = [];
  let busy = true, tick = null, cleared = false, motionStop = true;
  const card = { isConnected: true, append: (node) => appended.push(node) };
  const note = { textContent: "Запрос отправлен. Результат появится в логе." };
  const ctx = {
    Bridge: { call: (method, args) => {
      if (method === "operation_busy") return { busy, ...(motionStop ? { motion_stop: true } : {}) };
      calls.push([method, args]);
      return {};
    } },
    operationBusy: () => busy,
    log: () => {},
    usbStageButton: (text, symbol, handler) => ({ text, click: handler, removed: false, remove() { this.removed = true; } }),
    setInterval: (fn) => { tick = fn; return 1; },
    clearInterval: () => { cleared = true; },
  };
  run(watch + "\nthis.watchMotionOptimize = watchMotionOptimize;", ctx);
  ctx.watchMotionOptimize(card, note);

  const stop = appended[0];
  assert(stop && stop.text === "Остановить", "кнопка «Остановить» у карточки");
  assert(note.textContent.startsWith("Работа в движении:"), note.textContent);
  stop.click();
  assert(JSON.stringify(calls) === JSON.stringify([["adb_cancel_install", { what: "motion" }]]), JSON.stringify(calls));

  tick();
  assert(!stop.removed && !cleared, "операция идёт — кнопка на месте");
  busy = false;
  tick();
  assert(stop.removed && cleared, "операция кончилась — кнопки нет");
  assert(note.textContent === "Готово — результат в логе.", note.textContent);

  // Программа 1.1.12 (бандл интерфейса новее APK): остановкой оборвала бы и переустановку — кнопки нет.
  appended.length = 0; busy = true; motionStop = false;
  ctx.watchMotionOptimize(card, note);
  assert(appended.length === 0, "старая программа — без кнопки");
  busy = false;
  tick();
  assert(note.textContent === "Готово — результат в логе.", "конец операции виден и без кнопки");
};
