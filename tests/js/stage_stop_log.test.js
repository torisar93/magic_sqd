// Android: техник остановил очередь установки — в логе «Этап остановлен пользователем.», а не
// «Этап завершился с ошибкой: java.lang.IllegalStateException: Очередь остановлена…» (лог #584).
// (android/app/src/main/assets/js/app.js: onAdbStageResult)
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "android/app/src/main/assets/js/app.js";
  const src = read(file);
  const code = slice(src, "  let labInstallBusy=false;", "  function showLabNotice(", file);
  const logs = [], runs = [];
  const ctx = {
    usbOperation: null, stageOperation: null, appInstallOperation: null,
    appInstallResults: {}, stageExecutionResults: {}, flashBlockResults: {}, usbStageResults: {},
    screenWizard: { classList: { remove() {} } },
    stages: [{ index: 2, type: "apps", title: "Приложения" }], currentIndex: 0,
    failedStages: new Map(),
    log: (line) => logs.push(line),
    finishRun: (outcome) => runs.push(outcome),
  };
  const api = run(code + "\nthis.__api={onAdbStageResult};", ctx).__api;

  // 1) остановка техником: причина от Kotlin — текст отмены
  ctx.appInstallOperation = { index: 2, cancelRequested: true };
  api.onAdbStageResult({ index: 2, result: { success: false, reason: "Очередь остановлена пользователем" } });
  assert(logs.at(-1) === "Этап остановлен пользователем.", "остановка — не «ошибка»: " + logs.at(-1));
  assert(ctx.appInstallResults[2].cancelled === true, "итог этапа помечен как остановленный");
  assert(ctx.appInstallOperation === null, "операция завершена");

  // 2) настоящая ошибка без остановки — причина в логе как раньше
  ctx.appInstallOperation = { index: 2, cancelRequested: false };
  api.onAdbStageResult({ index: 2, result: { success: false, reason: "Не удалось установить a.apk ни одним из способов" } });
  assert(logs.at(-1) === "Этап завершился с ошибкой: Не удалось установить a.apk ни одним из способов", logs.at(-1));
  assert(ctx.appInstallResults[2].cancelled === false, "ошибка не выдаётся за остановку");

  // 3) успех
  ctx.appInstallOperation = { index: 2, cancelRequested: false };
  api.onAdbStageResult({ index: 2, result: { success: true } });
  assert(logs.at(-1) === "Этап выполнен успешно.", "успех");
  assert(runs.length === 3, "окно этапа получает итог каждый раз");
};
