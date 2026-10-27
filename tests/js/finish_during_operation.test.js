// Android: «Готово» на последнем этапе, пока идёт операция («Работа в движении», запуск, разрешения). Haval H9,
// 27.10.2026: каждое нажатие отправляло лог сессии заново — 13 кусков по 4–29 строк. Теперь — окно «дождитесь».
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "android/app/src/main/assets/js/app.js";
  const src = read(file);
  const advance = slice(src, "  function advanceAfter(index, optionIndex) {", "  function logFinish(line) {", file);
  const calls = [];
  let busy = true;
  const ctx = {
    stages: [{ type: "actions", next: null }],
    failedStages: new Map(),
    operationBusy: () => busy,
    showLabBusyNotice: () => calls.push("notice"),
    log: (text) => calls.push("log:" + text),
    logFinish: (line) => calls.push("finish:" + line),
    flushSessionLog: (success) => calls.push("flush:" + success),
    renderNav: () => {}, showCompletionModal: () => {},
    installCompletedShown: true,
  };
  run(advance + "\nthis.advanceAfter = advanceAfter;", ctx);
  ctx.advanceAfter(0);
  assert(JSON.stringify(calls) === JSON.stringify(
    ["log:Нажато «Готово» во время операции — программа попросила дождаться её окончания.", "notice"]),
    "во время операции лог не отправляется: " + JSON.stringify(calls));
  calls.length = 0;
  busy = false;
  ctx.advanceAfter(0);
  assert(calls.includes("flush:true"), "после операции — как раньше: " + JSON.stringify(calls));
};
