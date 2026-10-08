// ПК, ИИ-мастер: что мастер этапов (screens/stage_wizard.js, stageWizard.ai) даёт панели ИИ — пути приложений как у
// сервера и обратно, совет по приложениям, нажатия только разрешённого (кнопку «работа в движении» ИИ не жмёт).
"use strict";
const { read, slice, assert, run } = require("./_util");

function button(label) {
  return { label, clicks: 0, disabled: false, style: {}, textContent: label, click() { this.clicks += 1; },
           closest() { return null; } };
}

module.exports = async function () {
  const file = "app/web/frontend/js/screens/stage_wizard.js";
  const code = slice(read(file), "  // -- ИИ-мастер («Установка с ИИ», js/screens/ai_panel.js) ---", "  window.stageWizard = {", file);
  const base = "C:\\Users\\tech\\AppData\\Local\\Magic SQD";
  const motion = button("Выполнить");
  const other = button("Выполнить");
  const cards = [{ h3: "Разрешить работу в движении", btn: motion }, { h3: "Открыть настройки", btn: other }].map((c) => {
    const card = { querySelector: (sel) => (sel === "button" ? c.btn : { textContent: c.h3 }) };
    c.btn.closest = () => card;
    return card;
  });
  const renders = [];
  const ctx = {
    model: { brand: "Haval", name: "Dargo", modification: "Dargo New" },
    stages: [{ index: 0, type: "instruction", title: "Доступ к ADB" }, { index: 1, type: "apps", title: "Приложения" },
             { index: 2, type: "actions", title: "Доп. кнопки", actions: [{ label: "Разрешить работу в движении" }, { label: "Открыть настройки" }] }],
    currentIndex: 2, runnerBusy: false, activeRun: null, historyStack: [1], appsActiveTab: {}, aiAdvice: {}, personalApks: [],
    appSelection: {
      [`${base}\\apk\\Магазины приложений\\GStore.apk`]: false,
      [`${base}\\apk\\GPS\\GNSS Client v1.7.1.apk`]: true,
      [`${base}\\cars\\Haval\\Dargo\\Dargo New\\files\\apps_2\\optional\\Yandex Music.apk`]: false,
    },
    sessionLog: ["строка 1", "строка 2"], failedStages: new Map(), loadError: null, aiTransport: null, aiLastDevice: null,
    aiFingerprints: new Map(), TYPE_LABELS: {}, Instructions12: { textDocument: (t) => t },
    navNextBtn: button("Далее"), goBack() {}, render() { renders.push(1); },
    contentEl: { dataset: {}, querySelectorAll: (sel) => (sel === ".stage06-command" ? cards : []), querySelector: () => null },
    document: { querySelector: () => null, getElementById: () => null },
    window: { StageRun: { current: () => null },
              DeviceHint: { modelPath: (m) => [m.brand, m.name, m.modification].join("/"), key: () => "" } },
  };
  run(code + "\nthis.__ai = { aiRelPath, aiLocalPath, aiSelectApps, aiPress, aiState, aiControls };", ctx);
  const ai = ctx.__ai;

  assert(ai.aiRelPath(`${base}\\apk\\GPS\\GNSS Client v1.7.1.apk`) === "apk/GPS/GNSS Client v1.7.1.apk", "библиотека → apk/…");
  assert(ai.aiRelPath(`${base}\\cars\\Haval\\Dargo\\Dargo New\\files\\apps_2\\optional\\Yandex Music.apk`)
         === "cars/Haval/Dargo/Dargo New/files/apps_2/optional/Yandex Music.apk", "файл модели → cars/…/files/…");
  assert(ai.aiLocalPath("apk/gps/gnss client v1.7.1.apk", Object.keys(ctx.appSelection)) === `${base}\\apk\\GPS\\GNSS Client v1.7.1.apk`,
         "путь сервера находится по хвосту, без учёта регистра");
  assert(ai.aiLocalPath("Client v1.7.1.apk", Object.keys(ctx.appSelection)) === null, "только целыми частями пути");

  const out = ai.aiSelectApps(
    [{ path: "apk/Магазины приложений/GStore.apk", why: "магазин" },
     { path: "cars/Haval/Dargo/Dargo New/files/apps_2/optional/Yandex Music.apk", why: "музыка" },
     { path: "apk/Нет/такого.apk", why: "?" }],
    [{ path: "apk/GPS/GNSS Client v1.7.1.apk", why: "старая версия" }]);
  assert(out.ok && out.missing.join() === "apk/Нет/такого.apk", "чего нет в программе — в ответе: " + JSON.stringify(out));
  assert(ctx.appSelection[`${base}\\apk\\Магазины приложений\\GStore.apk`] === true
         && ctx.appSelection[`${base}\\apk\\GPS\\GNSS Client v1.7.1.apk`] === false, "отметил и снял");
  assert(ctx.aiAdvice[1].picks.length === 2 && ctx.aiAdvice[1].avoid[0].why === "старая версия", "совет — этапу приложений");
  assert(ctx.appsActiveTab[1] === "ai" && renders.length === 0, "вкладка совета откроется на этапе приложений (сейчас другой этап)");
  assert(ai.aiState().selected.includes("apk/Магазины приложений/GStore.apk"), "состояние — пути как у сервера");

  const hands = await ai.aiPress("action:0");
  assert(!hands.ok && motion.clicks === 0, "«Разрешить работу в движении» ИИ не нажимает: " + hands.output);
  const fine = await ai.aiPress("action:1");
  assert(fine.ok && other.clicks === 1, "обычное доп. действие — нажимает");
  assert(ai.aiControls().join() === "next,back,action:0,action:1", "кнопки в состоянии: " + ai.aiControls().join());
  ctx.runnerBusy = true;
  const busy = await ai.aiPress("next");
  assert(!busy.ok && ctx.navNextBtn.clicks === 0, "идёт этап — ничего не нажимает");
};
