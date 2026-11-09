// Android: пока программа ждёт магнитолу по USB (до 20 с), «Начать установку» и «Выполнить» не начинают операцию —
// раньше Kotlin отвечал «Другая операция ещё выполняется» и этап падал (1.1.21 r1, №14010; разбор 09.11.2026).
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "android/app/src/main/assets/js/app.js";
  const src = read(file);
  const notices = [];
  const ctx = run("let adbUsbConnecting = false;\n" + slice(src, "  function waitingForUsb() {", "  function onAdbConnect() {", file)
    + "\nthis.waitingForUsb = waitingForUsb; this.setWaiting = (v) => { adbUsbConnecting = v; };",
    { log: () => {}, showLabNotice: (title, text) => notices.push(title) });
  assert(ctx.waitingForUsb() === false && notices.length === 0, "не ждём — операция идёт как обычно");
  ctx.setWaiting(true);
  assert(ctx.waitingForUsb() === true && notices[0] === "Ждём магнитолу по USB", "ждём — окно «дождитесь»");
  assert(src.includes("if (labInstallBusy || waitingForUsb()) return;\n    stageOperation ="), "«Выполнить» этапов (runStageOperation)");
  assert(src.includes('if (labInstallBusy || waitingForUsb()) return;\n      // Wi-Fi ADB: подключение НЕ нужно заранее'), "«Начать установку»");
};
