// Десктоп: log()/flushSessionLog() дозаписывают в прочный журнал сессии на
// диске (app/pending_install_logs.py, через install_log_append/install_log_send
// с токеном сессии) — см. app/web/frontend/js/screens/stage_wizard.js.
"use strict";
const { read, slice, assert, run, sleep } = require("./_util");

module.exports = async function () {
  const file = "app/web/frontend/js/screens/stage_wizard.js";
  const src = read(file);
  const code = slice(src, "  let sessionLog = [];", "  // -- инициализация экрана", file);

  const calls = [];
  const ctx = {
    model: { brand: "Geely", display_label: "Atlas New", name: "Atlas New", modification: "Monji" },
    logFn: () => {},
    window: {
      pywebview: {
        api: {
          async install_log_append(token, line, activity) { calls.push(["append", token, line, activity]); },
          async install_log_send(brand, model, modification, success, logText, token) {
            calls.push(["send", brand, model, modification, success, logText, token]);
          },
        },
      },
    },
  };
  const api = run(
    code + "\nthis.__api = { log, flushSessionLog, setToken: (t) => { sessionToken = t; }, "
      + "setActivity: (v) => { sessionHasActivity = v; } };",
    ctx,
  ).__api;

  // Без токена (сессия ещё не открылась через open()) — install_log_append
  // всё равно вызывается (лучше пустой токен, чем полностью потерянная
  // строка), но append_current на стороне Python молча ничего не запишет —
  // это поведение проверено в tests/test_pending_install_logs.py
  // (test_stale_token_append_is_noop).
  api.log("первая строка");
  await sleep(5);
  assert(calls.length === 1 && calls[0][0] === "append" && calls[0][1] === "" && calls[0][2] === "первая строка",
    "log() без токена всё равно дозаписывает (с пустым токеном): " + JSON.stringify(calls));
  assert(calls[0][3] === false, "activity по умолчанию false");

  // Токен появился (как после open()/install_load_stages) — дальнейшие
  // строки уходят с ним.
  api.setToken("abc123");
  api.setActivity(true);
  api.log("строка с активностью");
  await sleep(5);
  assert(calls[1][1] === "abc123" && calls[1][2] === "строка с активностью" && calls[1][3] === true,
    "log() после появления токена передаёт его и текущий флаг активности: " + JSON.stringify(calls));

  // flushSessionLog — тот же токен идёт последним аргументом install_log_send.
  calls.length = 0;
  api.flushSessionLog(true);
  assert(calls.length === 1 && calls[0][0] === "send", "flushSessionLog зовёт install_log_send: " + JSON.stringify(calls));
  const [, brand, modelName, modification, success, logText, token] = calls[0];
  assert(brand === "Geely" && modelName === "Atlas New" && modification === "Monji" && success === true,
    "install_log_send несёт привычные аргументы (регресс формата): " + JSON.stringify(calls[0]));
  assert(logText === "первая строка\nстрока с активностью", "install_log_send несёт полный текст сессии");
  assert(token === "abc123", "install_log_send передаёт токен сессии");
};
