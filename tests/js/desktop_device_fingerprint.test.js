// ПК: отпечаток магнитолы в журнал сессии — тем же текстом, что пишет Android («ADB подключён: device::…»), раз на
// магнитолу за сессию (app/web/frontend/js/screens/stage_wizard.js: noteDeviceFingerprint). По нему сервер учится
// узнавать модель по магнитоле.
"use strict";
const { read, slice, assert, run, sleep } = require("./_util");

module.exports = async function () {
  const file = "app/web/frontend/js/screens/stage_wizard.js";
  const src = read(file);
  const core = slice(src, "  let sessionLog = [];", "  // -- инициализация экрана", file);
  const asked = [];
  let answer = { name: "geely_fx11_j1", model: "FX11_J1", device: "msmnile_gvmq" };
  const ctx = {
    Date, model: { brand: "Geely", display_label: "Geely / Preface — Monji", name: "Preface", modification: "Monji" },
    logFn: () => {},
    window: { appInfo: {}, pywebview: { api: {
      install_device_fingerprint: (serial) => { asked.push(serial); return Promise.resolve(answer); },
      install_log_append: () => Promise.resolve(),
    } } },
  };
  const api = run(core + "\nthis.__api={noteDeviceFingerprint,fingerprinted,state:()=>({sessionLog,sessionHasActivity})};", ctx).__api;

  await api.noteDeviceFingerprint("ABC123");
  await api.noteDeviceFingerprint("ABC123");  // повторное «Обновить» — второй строки нет
  await sleep(0);
  const lines = api.state().sessionLog;
  assert(asked.join(",") === "ABC123", "спросили один раз: " + asked.join(","));
  assert(lines.at(-1) === "ADB подключён: device::ro.product.name=geely_fx11_j1;ro.product.model=FX11_J1;ro.product.device=msmnile_gvmq",
    lines.at(-1));
  assert(api.state().sessionHasActivity === false, "подключение само по себе — не действие");

  answer = {};  // не удалось прочитать — строки нет
  await api.noteDeviceFingerprint("192.168.1.5:5555");
  await sleep(0);
  assert(api.state().sessionLog.length === lines.length, "без отпечатка строки нет");
  api.fingerprinted.clear();  // новая модель — снова спросим (open() чистит набор)
  answer = { name: "x9h_a01g", model: "x9 for arm64", device: "x9h_a01g" };
  await api.noteDeviceFingerprint("ABC123");
  await sleep(0);
  assert(api.state().sessionLog.at(-1).endsWith("ro.product.model=x9 for arm64;ro.product.device=x9h_a01g"), "после смены модели");
};
