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
  const runStage = slice(src, "  function runStageOperation(", "    stageOperation = {", file);
  assert(runStage.includes("if (labInstallBusy || waitingForUsb()) return;"), "«Выполнить» этапов (runStageOperation)");
  // №16672 (Wi-Fi): Kotlin занят подключением, ждущим подтверждения ключа — тоже «дождитесь», а не ошибка этапа.
  assert(runStage.includes("if (operationBusy()) { showLabBusyNotice(); return; }"), "«Выполнить»: занято — дождитесь");
  const startBtn = slice(src, "    btn.addEventListener(\"click\", () => {\n      if (labInstallBusy || waitingForUsb()) return;",
    "      // Wi-Fi ADB: подключение НЕ нужно заранее", file);
  assert(startBtn.includes("if (operationBusy()) { showLabBusyNotice(); return; }"), "«Начать установку»: занято — дождитесь");

  // Поведение: Kotlin занят — операция не начинается, показано окно.
  const shown = [];
  const guard = run("let labInstallBusy = false; let stageOperation = null;\n"
    + runStage + "    stageOperation = { index: args.index }; return 'started'; }\nthis.runStageOperation = runStageOperation;",
    { waitingForUsb: () => false, operationBusy: () => true, showLabBusyNotice: () => shown.push("busy") });
  assert(guard.runStageOperation("m", { index: 1 }) === undefined && shown.length === 1, "занято — окно, этап не запущен");
  const free = run("let labInstallBusy = false; let stageOperation = null;\n"
    + runStage + "    stageOperation = { index: args.index }; return 'started'; }\nthis.runStageOperation = runStageOperation;",
    { waitingForUsb: () => false, operationBusy: () => false, showLabBusyNotice: () => shown.push("busy") });
  assert(free.runStageOperation("m", { index: 1 }) === "started", "свободно — этап запускается");
};
