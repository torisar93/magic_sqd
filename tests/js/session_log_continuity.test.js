// Android: журнал сессии не обрывается и подробнее (Belgee S50, 28.09 — логи рвались на «Скачиваю …»: при
// сворачивании программы лог отправлялся и запечатывался, всё дальнейшее на сервер не попадало).
// (android/app/src/main/assets/js/app.js: log/flushSessionLog/continueSession, __onAppHidden/__onAppShown,
// beginUsbOperation)
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "android/app/src/main/assets/js/app.js";
  const src = read(file);
  const core = slice(src, "  let sessionLog = [];", "  function setLogOpen(", file);
  const lifecycle = slice(src, "  let hiddenAt = 0;", "  // Системный жест/кнопка \"назад\"", file);
  const begin = slice(src, "  // note — подробности для журнала", "  function sendUsbOperation(", file);

  const clock = { t: 1_000_000 };
  class FakeDate { static now() { return clock.t; } toLocaleString() { return "28.09.2026, 20:44:31"; } }
  const calls = [];
  let panelLines = 0;
  const ctx = {
    Date: FakeDate, window: {},
    model: { brand: "Belgee", display_label: "Belgee / S50", name: "S50", modification: "" },
    screenWizard: { classList: { contains: () => ctx.wizardActive } }, wizardActive: true,
    Bridge: {
      call(method, args) {
        calls.push([method, args]);
        if (method === "app_version") return { version: "1.0.49", client_id: "c1", device: "Xiaomi 2201117TG", android: "13", sdk: 33 };
        return {};
      },
    },
    classifyLogLevel: () => "info", highlightKeywords: (t) => t,
    el: () => { panelLines += 1; return {}; },
    logPanelEl: { appendChild() {}, scrollHeight: 0 }, logLastLineEl: {},
    // для beginUsbOperation
    labInstallBusy: false, usbConnected: true,
    usbStatusEl: {}, usbConnectBtn: { focus() {} }, usbBarEl: { scrollIntoView() {} },
    renderNav() {}, document: { querySelectorAll: () => [] },
    openStageRun() {},
  };
  const api = run(core + lifecycle + begin +
    "\nthis.__api={log,flushSessionLog,beginUsbOperation,usbWriteNote,state:()=>({sessionLog,sessionSent,sessionHasActivity})};",
    ctx).__api;
  const appended = () => calls.filter(([m]) => m === "install_log_append").map(([, a]) => a.line);
  const sent = () => calls.filter(([m]) => m === "install_log_send").map(([, a]) => a.log);

  // 1) паузы от 15 с — отдельной строкой; меньше — без пометки
  api.log("Флешка смонтирована.", true);
  clock.t += 5_000; api.log("Скачиваю Data_Belgee_2.3.apk...", true);
  clock.t += 75_000; api.log("Скачано файлов: 5.", true);
  const lines = appended();
  assert(lines.join("|") === "Флешка смонтирована.|Скачиваю Data_Belgee_2.3.apk...|… прошло 1 мин 15 с …|Скачано файлов: 5.",
    "пауза в журнале: " + lines.join("|"));

  // 2) служебная строка — в журнал, но не на экран
  const shown = panelLines;
  api.log("Нажато: «Запись файлов на флешку».", false, true);
  assert(panelLines === shown && appended().at(-1) === "Нажато: «Запись файлов на флешку».", "quiet — только журнал");

  // 3) свернули программу — строка в журнал с тем, что её свернуло (MainActivity.kt: hiddenReason), лог НЕ
  //    отправляется; вернулись — сколько была свёрнута, без «прошло»
  ctx.window.__onAppHidden("погас экран");
  assert(sent().length === 0, "при сворачивании лог больше не отправляется и не запечатывается");
  clock.t += 125_000;
  ctx.window.__onAppShown();
  const tail = appended().slice(-2);
  assert(tail[0] === "Программа свёрнута: погас экран." &&
    tail[1] === "Программа снова на экране (была свёрнута 2 мин 5 с).", "свёрнута/вернулась: " + tail.join("|"));
  ctx.window.__onAppHidden();  // прежний MainActivity (без причины) — общая строка
  assert(appended().at(-1) === "Программа свёрнута: погас экран или открыто другое приложение.", appended().at(-1));
  ctx.window.__onAppShown();
  ctx.wizardActive = false; ctx.window.__onAppHidden("погас экран"); ctx.wizardActive = true;
  assert(appended().at(-1).startsWith("Программа снова на экране"), "вне мастера сворачивание не пишется");

  // 4) лог ушёл (все этапы пройдены), а работа идёт дальше — продолжение отдельной сессией, не мимо сервера
  api.flushSessionLog(true);
  assert(sent().length === 1 && sent()[0].includes("Скачано файлов: 5."), "первый лог ушёл целиком");
  api.log("Запускаю приложение: com.dudu.autoui", true);
  const restart = calls.filter(([m]) => m === "install_log_session_start");
  assert(restart.length === 1 && restart[0][1].model === "Belgee / S50", "новая сессия той же модели");
  const cont = api.state().sessionLog;
  assert(cont[0].startsWith("Продолжение работы с моделью: Belgee / S50") &&
    cont[1] === "Magic SQD v1.0.49 (Android) · client=c1" &&
    cont[2] === "Телефон: Xiaomi 2201117TG · Android 13 (API 33) · 28.09.2026, 20:44:31" &&
    cont.at(-1) === "Запускаю приложение: com.dudu.autoui", "шапка продолжения: " + cont.join("|"));
  api.flushSessionLog(false);
  assert(sent().length === 2 && sent()[1].includes("Запускаю приложение"), "продолжение тоже ушло");

  // 5) продолжение без реальных действий — не шлём (как и раньше пустые сессии)
  api.log("Программа свёрнута: открыто другое приложение или нажата «Домой».", false, true);
  api.flushSessionLog(false);
  assert(sent().length === 2, "без действий — не отправляется");

  // 6) запись на флешку: почему не началась и что пишем
  ctx.labInstallBusy = true;
  assert(api.beginUsbOperation("files", { index: 1 }, {}) === false, "занято — не начинаем");
  assert(appended().at(-1) === "«Запись файлов на флешку» не началась: ещё идёт другая операция.", appended().at(-1));
  ctx.labInstallBusy = false; ctx.usbConnected = false;
  assert(api.beginUsbOperation("files", { index: 1 }, {}) === false, "нет флешки — не начинаем");
  assert(appended().at(-1) === "«Запись файлов на флешку» не началась: флешка не подключена.", appended().at(-1));
  ctx.usbConnected = true;
  const card = { dataset: {}, querySelector: () => ({ querySelector: () => ({}) }) };
  const note = api.usbWriteNote(["a"], ["x.apk", "y.apk", "z.apk"], "freetuga");
  assert(api.beginUsbOperation("files", { index: 1 }, card, [], null, note) === true, "запись началась");
  assert(appended().at(-1) === "Нажато: «Запись файлов на флешку» — файлы этапа: 1, приложения: 3, комплект «freetuga».",
    appended().at(-1));
};
