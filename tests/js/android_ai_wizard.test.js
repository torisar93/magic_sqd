// Android, ИИ-мастер: что мастер (assets/js/app.js, window.__aiWizard) даёт чату ИИ — пути приложений как у сервера,
// совет по приложениям (выбор техника — globalSelectedApks), нажатия только разрешённого («работа в движении» — нет).
"use strict";
const { read, slice, assert, run } = require("./_util");

function button(label) {
  return { label, clicks: 0, disabled: false, style: {}, textContent: label, click() { this.clicks += 1; } };
}

module.exports = async function () {
  const file = "android/app/src/main/assets/js/app.js";
  const code = slice(read(file), "  // -- ИИ-мастер («Установка с ИИ», js/ai_phone.js) ---", "  document.addEventListener(\"DOMContentLoaded\"", file);
  const base = "/data/user/0/ru.magicsqd.mobile/files";
  const motion = button("Выполнить");
  const other = button("Выполнить");
  const cards = [motion, other].map((btn) => ({ querySelector: () => btn }));
  const renders = [];
  const apps = { index: 1, type: "apps", title: "Приложения",
                 standard_apks_optional: [{ path: `${base}/cars/Haval/Dargo/Dargo New/files/apps_2/optional/Yandex Music.apk` }] };
  const ctx = {
    model: { brand: "Haval", name: "Dargo", modification: "Dargo New", display_label: "Haval / Dargo — Dargo New" },
    stages: [{ index: 0, type: "instruction", title: "Доступ к ADB" }, apps,
             { index: 2, type: "actions", title: "Доп. кнопки", actions: [{ label: "Разрешить работу в движении" }, { label: "Открыть настройки" }] }],
    currentIndex: 2, labInstallBusy: false, activeRun: null, historyStack: [1], appsActiveTabs: {}, aiAdvice: {}, aiBanner: "",
    appsSelection: { 1: { variant: null, optional: new Set([`${base}/apk/GPS/GNSS Client v1.7.1.apk`]) } },
    globalSelectedApks: new Set([`${base}/apk/GPS/GNSS Client v1.7.1.apk`]),
    apkLibrary: [{ path: `${base}/apk/Магазины приложений/GStore.apk` }, { path: `${base}/apk/GPS/GNSS Client v1.7.1.apk` }],
    personalApks: [], sessionLog: ["строка"], failedStages: new Map(), adbConnected: true,
    screenWizard: { classList: { contains: () => true } },
    wizardNextBtn: button("Далее"), wizardBackBtn: button("Назад"), adbConnectBtn: button("Подключить ADB"),
    adbBarEl: { style: { display: "none" } },
    wizardContentEl: { querySelectorAll: (sel) => (sel === ".flow-action-card" ? cards : []), querySelector: () => null },
    document: { querySelector: () => null },
    window: { StageRun: { current: () => null } },
    DeviceHint: { modelPath: (m) => [m.brand, m.name, m.modification].join("/"), fromBanner: () => null, key: () => "" },
    Instructions12: { textDocument: (t) => t },
    stageApkLists: (stage) => ({ required: [], optional: stage.standard_apks_optional || [] }),
    selectedAppsForStage: () => ({ entries: [] }), connectionModeFor: () => "wired",
    render() { renders.push(1); }, goBack() {}, log() {}, onAdbConnect() {}, setAdbStatus() {},
    Bridge: { call() {} }, findCatalogModel: () => null, selectModel() {},
  };
  run(code + "\nthis.__ai = window.__aiWizard; this.__rel = aiRelPath;", ctx);
  const ai = ctx.__ai;

  assert(ctx.__rel(`${base}/apk/GPS/GNSS Client v1.7.1.apk`) === "apk/GPS/GNSS Client v1.7.1.apk", "библиотека → apk/…");
  assert(ctx.__rel(`${base}/cars/Haval/Dargo/Dargo New/files/apps_2/optional/Yandex Music.apk`)
         === "cars/Haval/Dargo/Dargo New/files/apps_2/optional/Yandex Music.apk", "файл модели → cars/…/files/…");

  const out = ai.selectApps(
    [{ path: "apk/Магазины приложений/GStore.apk", why: "магазин" },
     { path: "cars/Haval/Dargo/Dargo New/files/apps_2/optional/Yandex Music.apk", why: "музыка" }],
    [{ path: "apk/GPS/GNSS Client v1.7.1.apk", why: "старая версия" }, { path: "apk/Нет/такого.apk" }]);
  assert(out.ok && out.missing.join() === "apk/Нет/такого.apk", "чего нет в программе — в ответе: " + JSON.stringify(out));
  assert(ctx.globalSelectedApks.has(`${base}/apk/Магазины приложений/GStore.apk`)
         && !ctx.globalSelectedApks.has(`${base}/apk/GPS/GNSS Client v1.7.1.apk`), "отметил и снял");
  assert(ctx.appsSelection[1].optional.size === 0, "снятое ушло и из выбора этапа");
  assert(ctx.aiAdvice[1].picks.length === 2 && ctx.appsActiveTabs[1] === "ai" && renders.length === 0,
         "совет — этапу приложений, вкладка откроется на нём (сейчас другой этап)");
  assert(ai.state().selected.includes("apk/Магазины приложений/GStore.apk") && ai.state().model === "Haval/Dargo/Dargo New",
         "состояние — пути и модель как у сервера");

  const hands = await ai.press("action:0");
  assert(!hands.ok && motion.clicks === 0, "«Разрешить работу в движении» ИИ не нажимает: " + hands.output);
  const fine = await ai.press("action:1");
  assert(fine.ok && other.clicks === 1, "обычное доп. действие — нажимает");
  ctx.labInstallBusy = true;
  const busy = await ai.press("next");
  assert(!busy.ok && ctx.wizardNextBtn.clicks === 0, "идёт этап — ничего не нажимает");
};
