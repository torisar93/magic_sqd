// ПК: кнопка «Начать установку» на этапе приложений — при Wi-Fi сначала prefetch, потом окно подключения,
// потом start_stage(prefetched=true); провод — как раньше (app/web/frontend/js/screens/stage_wizard.js:
// buildStartStopButtons).
"use strict";
const { read, slice, assert, run, sleep } = require("./_util");

async function scenario(code, { mode, prefetch, askWifi, device = null, confirm = true }) {
  const calls = [], runs = [], finishes = [];
  let handler = null;
  const btn = { addEventListener: (ev, h) => { handler = h; }, disabled: false, appendChild() {} };
  const ctx = {
    el: () => btn, runnerBusy: false, navBackBtn: {}, navNextBtn: {}, model: { key: "M" },
    selectedApkPaths: () => ["/a.apk"], log() {}, render() {},
    openStageRun: (o) => { runs.push(o); },
    finishRun: (o) => finishes.push(o),
    document: { querySelector: () => ({ close: () => calls.push(["dialog.close"]) }) },
    window: {
      confirmDialog: async () => { calls.push(["confirm"]); return confirm; }, notice() {},
      pywebview: { api: {
        install_prefetch_apks: async (...a) => { calls.push(["prefetch", ...a]); return prefetch; },
        install_start_stage: async (...a) => { calls.push(["start", ...a]); return { ok: true }; },
        install_cancel_stage: () => calls.push(["cancel_stage"]),
      } },
    },
  };
  const panel = { appendChild() {}, querySelector: () => ({ append() {} }), _appChooser: { paths: () => ["/a.apk"], entries: () => [] } };
  run(code + "\nthis.__f = buildStartStopButtons;", ctx);
  ctx.__f(panel, { type: "apps", index: 2 }, () => device, { startLabel: "Начать установку", transport: { mode: () => mode, askWifi } });
  await handler();
  return { calls, runs, finishes };
}

module.exports = async function () {
  const file = "app/web/frontend/js/screens/stage_wizard.js";
  const code = slice(read(file), "  function buildStartStopButtons(", "  function renderAdbStage(", file);

  let r = await scenario(code, { mode: "wifi", prefetch: { ok: true }, askWifi: async () => "192.168.43.1:5555" });
  let names = r.calls.map((c) => c[0]);
  assert(names.join() === "prefetch,start", "Wi-Fi: сначала скачивание, потом запуск, без вопроса про устройство: " + names);
  assert(r.calls[1][3] === "192.168.43.1:5555" && r.calls[1][5] === true, "start с адресом из окна и prefetched=true");
  assert(/скачиваем приложения/.test(r.runs[0].detail), "окно установки объясняет порядок");

  r = await scenario(code, { mode: "wifi", prefetch: { ok: false, error: "Не удалось скачать: a.apk" }, askWifi: async () => { throw new Error("не должно вызываться"); } });
  assert(r.calls.map((c) => c[0]).join() === "prefetch" && r.finishes[0].success === false && /a\.apk/.test(r.finishes[0].message), "ошибка скачивания останавливает запуск");

  r = await scenario(code, { mode: "wifi", prefetch: { ok: true }, askWifi: async () => null });
  assert(r.calls.map((c) => c[0]).join() === "prefetch" && /Приложения уже скачаны/.test(r.finishes[0].message), "закрыли окно подключения");

  r = await scenario(code, { mode: "wired", prefetch: null, askWifi: null, device: "SER123" });
  assert(r.calls.map((c) => c[0]).join() === "start" && r.calls[0][3] === "SER123" && r.calls[0][5] === false, "провод не изменился");

  r = await scenario(code, { mode: "wired", prefetch: null, askWifi: null, device: null, confirm: false });
  assert(r.calls.map((c) => c[0]).join() === "confirm" && r.runs.length === 0, "без устройства по проводу спрашиваем и выходим");

  let release; const waiting = new Promise((res) => { release = res; });
  const pending = scenario(code, { mode: "wifi", prefetch: { ok: true }, askWifi: () => waiting });
  await sleep(20);
  release(null);
  r = await pending;
  assert(r.calls.map((c) => c[0]).join() === "prefetch", "после закрытия окна установка не стартует");
};
