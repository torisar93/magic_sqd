// ПК: журнал сессии подробнее и без потерь после отправки — как Android (session_log_continuity.test.js).
// (app/web/frontend/js/screens/stage_wizard.js: log/flushSessionLog/continueSession/sessionHeaderLines)
"use strict";
const { read, slice, assert, run, sleep } = require("./_util");

module.exports = async function () {
  const file = "app/web/frontend/js/screens/stage_wizard.js";
  const src = read(file);
  const core = slice(src, "  let sessionLog = [];", "  // -- инициализация экрана", file);

  const clock = { t: 5_000_000 };
  class FakeDate { static now() { return clock.t; } toLocaleString() { return "28.09.2026, 17:11:29"; } }
  const calls = [], shown = [];
  const ctx = {
    Date: FakeDate,
    model: { brand: "Belgee", display_label: "Belgee / S50", name: "S50", modification: "" },
    logFn: (line) => shown.push(line),
    window: {
      appInfo: { app_version: "1.0.49", client_id: "abc", os: "Windows 11 (сборка 26100, AMD64)" },
      pywebview: { api: {
        install_log_append: (...a) => { calls.push(["append", ...a]); return Promise.resolve(); },
        install_log_send: (...a) => { calls.push(["send", ...a]); },
        install_log_continue: () => { calls.push(["continue"]); return Promise.resolve("tok2"); },
      } },
    },
  };
  const api = run(core +
    "\nthis.__api={log,flushSessionLog,sessionHeaderLines,state:()=>({sessionLog,sessionToken,sessionHasActivity}),"
    + "setActive:()=>{sessionHasActivity=true;},setToken:(t)=>{sessionToken=t;}};", ctx).__api;
  api.setToken("tok1");

  // шапка: версия и компьютер
  assert(JSON.stringify(api.sessionHeaderLines()) === JSON.stringify([
    "Magic SQD v1.0.49 (x64) · client=abc", "Компьютер: Windows 11 (сборка 26100, AMD64) · 28.09.2026, 17:11:29"]),
    JSON.stringify(api.sessionHeaderLines()));

  // пауза от 15 с — в журнал, но не в панель лога
  api.setActive(); api.log("Флешка: записано — файлы этапа и выбранные приложения.");
  clock.t += 40_000; api.log("Нажато «Записать на флешку»: USB (E:), без форматирования.");
  assert(api.state().sessionLog.join("|") === "Флешка: записано — файлы этапа и выбранные приложения.|… прошло 40 с …|"
    + "Нажато «Записать на флешку»: USB (E:), без форматирования.", api.state().sessionLog.join("|"));
  assert(!shown.some((line) => line.includes("прошло")), "пауза не в панели лога");

  // лог ушёл, а работа идёт дальше: продолжение отдельной сессией, флаг действия, взведённый перед строкой, сохраняется
  api.flushSessionLog(true);
  assert(calls.filter((c) => c[0] === "send").length === 1, "первый лог ушёл");
  api.setActive(); api.log("QR ADB: пароль получен.");
  assert(calls.some((c) => c[0] === "continue"), "запрошен новый журнал на диске");
  const cont = api.state().sessionLog;
  assert(cont[0].startsWith("Продолжение работы с моделью: Belgee / S50") && cont[1].startsWith("Magic SQD v1.0.49")
    && cont.at(-1) === "QR ADB: пароль получен.", cont.join("|"));
  await sleep(0);
  const caught = calls.find((c) => c[0] === "append" && c[1] === "tok2");
  assert(caught && caught[2].split("\n").at(-1) === "QR ADB: пароль получен." && caught[3] === true,
    "строки до токена ушли в журнал на диске одним куском: " + JSON.stringify(caught));
  api.flushSessionLog(false);
  const sends = calls.filter((c) => c[0] === "send");
  assert(sends.length === 2 && sends[1][5].includes("QR ADB: пароль получен.") && sends[1][6] === "tok2", "продолжение ушло");

  // продолжение без действий — не шлём
  api.log("Техник вышел из модели.");
  api.flushSessionLog(false);
  assert(calls.filter((c) => c[0] === "send").length === 2, "без действий — не отправляется");
};
