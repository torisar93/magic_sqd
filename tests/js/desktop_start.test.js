// ПК: кнопка «Начать установку» на этапе приложений — при Wi-Fi сначала prefetch, потом окно подключения,
// потом start_stage(prefetched=true); провод — как раньше (app/web/frontend/js/screens/stage_wizard.js:
// buildStartStopButtons).
"use strict";
const { read, slice, assert, run, sleep } = require("./_util");

async function scenario(code, { mode, prefetch, askWifi, device = null, confirm = true, refreshed = null,
                                 stageType = "apps", modelWifi = false }) {
  const calls = [], runs = [], finishes = [];
  let handler = null;
  const btn = { addEventListener: (ev, h) => { handler = h; }, disabled: false, appendChild() {}, prepend() {}, classList: { add() {} } };
  const docked = [];
  const ctx = {
    el: () => btn, runnerBusy: false, navBackBtn: {}, navNextBtn: {}, model: { key: "M" }, prefetchedStages: new Set(),
    modelWifi,
    contentEl: { after: (node) => docked.push(node) }, // «Начать установку» — в нижней панели под этапом

    selectedApkPaths: () => ["/a.apk"], log() {}, render() {},
    openStageRun: (o) => { runs.push(o); },
    finishRun: (o) => finishes.push(o),
    document: { querySelector: () => ({ close: () => calls.push(["dialog.close"]) }) },
    window: {
      confirmDialog: async () => { calls.push(["confirm"]); return confirm; }, notice() {},
      StageRun: { showUserError: (input, opts) => { calls.push(["popup", input.id, typeof opts.retry]); return {}; } },
      pywebview: { api: {
        install_prefetch_apks: async (...a) => { calls.push(["prefetch", ...a]); return prefetch; },
        install_start_stage: async (...a) => { calls.push(["start", ...a]); return { ok: true }; },
        install_cancel_stage: () => calls.push(["cancel_stage"]),
      } },
    },
  };
  const panel = { appendChild() {}, querySelector: () => ({ append() {} }), _appChooser: { paths: () => ["/a.apk"], entries: () => [] } };
  let current = device;
  const transport = {
    mode: () => mode, askWifi,
    // «Обновить» списка устройств: refreshed — что нашлось (и что станет выбранным), null — список пуст.
    refreshDevices: async () => {
      calls.push(["refresh"]);
      // Как buildTransportBar: выбранной может стать только готовая магнитола (state "device").
      current = refreshed && refreshed.state === "device" ? refreshed.serial : null;
      return refreshed ? [refreshed] : [];
    },
  };
  run(code + "\nthis.__f = buildStartStopButtons;", ctx);
  ctx.__f(panel, { type: stageType, index: 2 }, () => current, { startLabel: "Начать установку", transport });
  await handler();
  return { calls, runs, finishes, docked };
}

module.exports = async function () {
  const file = "app/web/frontend/js/screens/stage_wizard.js";
  const code = slice(read(file), "  async function findDevice(", "  function renderAdbStage(", file);

  let r = await scenario(code, { mode: "wifi", prefetch: { ok: true }, askWifi: async () => "192.168.43.1:5555" });
  let names = r.calls.map((c) => c[0]);
  assert(names.join() === "prefetch,start", "Wi-Fi: сначала скачивание, потом запуск, без вопроса про устройство: " + names);
  assert(r.calls[1][3] === "192.168.43.1:5555" && r.calls[1][5] === true, "start с адресом из окна и prefetched=true");
  assert(/скачиваем приложения/.test(r.runs[0].detail), "окно установки объясняет порядок");
  assert(r.docked.length === 1, "«Начать установку» — в нижней панели под этапом");

  r = await scenario(code, { mode: "wifi", prefetch: { ok: false, error: "Не удалось скачать: a.apk" }, askWifi: async () => { throw new Error("не должно вызываться"); } });
  assert(r.calls.map((c) => c[0]).join() === "prefetch" && r.finishes[0].success === false && /a\.apk/.test(r.finishes[0].message), "ошибка скачивания останавливает запуск");

  r = await scenario(code, { mode: "wifi", prefetch: { ok: true }, askWifi: async () => null });
  assert(r.calls.map((c) => c[0]).join() === "prefetch" && /Приложения уже скачаны/.test(r.finishes[0].message), "закрыли окно подключения");

  r = await scenario(code, { mode: "wired", prefetch: null, askWifi: null, device: "SER123" });
  assert(r.calls.map((c) => c[0]).join() === "start" && r.calls[0][3] === "SER123" && r.calls[0][5] === false, "провод не изменился");

  // Без магнитолы по проводу: больше не «Продолжить всё равно?» (после «да» перебирались все способы с
  // «no devices», логи #734/#736/#786/#789) — обновляем список сами и, если пусто, окно «Магнитола не подключена».
  r = await scenario(code, { mode: "wired", prefetch: null, askWifi: null, device: null });
  assert(r.calls.map((c) => c[0]).join() === "refresh,popup" && r.runs.length === 0, "без магнитолы — окно, а не запуск: " + JSON.stringify(r.calls));
  assert(r.calls[1][1] === "no_device" && r.calls[1][2] === "function", "окно «Магнитола не подключена» с «Повторить»");

  r = await scenario(code, { mode: "wired", prefetch: null, askWifi: null, device: null, refreshed: { serial: "NEW1", state: "device" } });
  assert(r.calls.map((c) => c[0]).join() === "refresh,start" && r.calls[1][3] === "NEW1", "магнитолу подключили после открытия этапа — нашли и запустили: " + JSON.stringify(r.calls));

  r = await scenario(code, { mode: "wired", prefetch: null, askWifi: null, device: null, refreshed: { serial: "U1", state: "unauthorized" } });
  assert(r.calls.map((c) => c[0]).join() === "refresh,popup" && r.calls[1][1] === "unauthorized", "не разрешила отладку — своё окно");

  r = await scenario(code, { mode: "wired", prefetch: null, askWifi: null, device: null, stageType: "adb", modelWifi: true });
  assert(r.calls.map((c) => c[0]).join() === "start" && r.calls[0][3] === null, "adb-этап модели «ADB по Wi-Fi» подключается сам");

  let release; const waiting = new Promise((res) => { release = res; });
  const pending = scenario(code, { mode: "wifi", prefetch: { ok: true }, askWifi: () => waiting });
  await sleep(20);
  release(null);
  r = await pending;
  assert(r.calls.map((c) => c[0]).join() === "prefetch", "после закрытия окна установка не стартует");
};
