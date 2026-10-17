// Итоговая строка последнего этапа — в лог один раз (владелец 17.10.2026: «сделай, чтобы строка писалась один раз»;
// лог №7015 — 49 повторов «Все этапы установки выполнены.»: каждое «Далее» на последнем этапе писало её заново).
// ПК — app/web/frontend/js/screens/stage_wizard.js, Android — android/app/src/main/assets/js/app.js: advanceAfter/logFinish/show.
"use strict";
const { read, slice, assert, run } = require("./_util");

const OK = "Все этапы установки выполнены.";
const FAIL = "Установка завершена с ошибками — не выполнено: Выбор приложений.";

function check(file, busyName) {
  const src = read(file);
  const code = slice(src, "  function advanceAfter(index, optionIndex) {", "  function show(index) {", file)
    + slice(src, "  function show(index) {", "\n  }\n", file) + "\n  }\n";
  const lines = [];
  let flushes = 0;
  const ctx = {
    stages: [{ index: 0, id: 1, type: "instruction", next: null }],
    failedStages: new Map(), done: new Set(), historyStack: [],
    installCompletedShown: false, lastFinishLine: "", currentIndex: 0, nextAction: null,
    log: (line) => lines.push(line), flushSessionLog: () => flushes++,
    renderNav() {}, render() {}, aiNotify() {}, aiStageLine: () => "", indexById: () => -1,
    showCompletionModal() {}, window: { boostyDialogs: { showCompletionDialog() {} } },
  };
  ctx[busyName] = false;
  const api = run(code + "\nthis.__api={advanceAfter,show};", ctx).__api;
  const count = (line) => lines.filter((l) => l === line).length;

  api.advanceAfter(0); api.advanceAfter(0); api.advanceAfter(0);
  assert(count(OK) === 1, `${file}: «${OK}» один раз на три «Далее» (было ${count(OK)})`);
  assert(flushes === 3, `${file}: отправка лога по-прежнему на каждое «Далее» (${flushes})`);

  ctx.failedStages.set(0, "Выбор приложений");
  api.advanceAfter(0); api.advanceAfter(0);
  assert(count(FAIL) === 1, `${file}: итог сменился — новая строка один раз`);

  api.show(0);  // ушли на этап — закончили заново: строка снова в логе
  api.advanceAfter(0);
  assert(count(FAIL) === 2, `${file}: после перехода на этап итог пишется снова`);
  ctx.failedStages.clear();
  api.advanceAfter(0);
  assert(count(OK) === 2 && lines.length === 4, `${file}: итог снова «выполнены» — одна строка (${lines.join(" | ")})`);
}

module.exports = async function () {
  check("app/web/frontend/js/screens/stage_wizard.js", "runnerBusy");
  check("android/app/src/main/assets/js/app.js", "labInstallBusy");
};
