// Подсказка «Сначала нажмите „Начать установку“, к Wi-Fi магнитолы — потом» в нижней панели этапа приложений
// (владелец, 15.10.2026): общий AppTabs.wifiHint (ПК и Android), когда её видно — Android app.js (updateWifiHint) и
// ПК stage_wizard.js (buildTransportBar → buildStartStopButtons). Видна, пока этап по Wi-Fi, магнитола не подключена
// и из выбранного есть что скачивать (владелец, 15.10: «не должен появляться, если все приложения скачаны»).
"use strict";
const { read, slice, assert, run } = require("./_util");
const { makeDom } = require("./_ring_dom");

const TEXT = /^Сначала нажмите «Начать установку»: приложения скачаются, пока есть интернет\. К Wi-Fi магнитолы подключитесь потом/;

// Узел мини-DOM с тем, что нужно панели этапа ПК: appendChild, after, options у select, обработчики кликов.
function pcDom() {
  const { document } = makeDom();
  const proto = Object.getPrototypeOf(document.createElement("div"));
  proto.appendChild = function (node) { this.append(node); return node; };
  proto.after = function (node) {
    node.remove();
    const list = this.parent.children;
    list.splice(list.indexOf(this) + 1, 0, node);
    node.parent = this.parent;
  };
  proto.addEventListener = function (type, fn) { (this.handlers = this.handlers || {})[type] = fn; };
  proto.click = function () { return this.handlers && this.handlers.click && this.handlers.click(); };
  Object.defineProperty(proto, "options", { get() { return this.children; } });
  const el = (tag, attrs = {}, children = []) => {
    const node = document.createElement(tag);
    if (attrs.class) node.className = attrs.class;
    if (attrs.text != null) node.textContent = attrs.text;
    children.forEach((child) => node.append(child));
    return node;
  };
  return { document, el };
}

module.exports = async function () {
  // 1) Общий модуль: абзац с текстом, скрыт, пока платформа не решила; перевод — в словаре.
  for (const base of ["app/web/frontend/js/", "android/app/src/main/assets/js/"]) {
    const { document } = makeDom();
    const window = {};
    run(read(base + "apps_tabs.js"), { window, document });
    const hint = window.AppTabs.wifiHint();
    assert(hint.className === "apps-dock-hint" && hint.hidden === true, `${base}: скрыта до решения платформы`);
    assert(TEXT.test(hint.textContent), `${base}: текст — ${hint.textContent}`);
    const i18n = {};
    run(read(base + "i18n_en.js") + "\n" + read(base + "i18n.js"), { window: i18n, JSON, RegExp, Map, WeakMap, Object, String });
    const english = i18n.i18n.translate(hint.textContent);
    assert(/^Press “Start installation” first/.test(english || ""), `${base}: перевод — ${english}`);
    const need = window.AppTabs.needsDownload;
    assert(need([{ path: "/a", remote_only: true }, { path: "/b", remote_only: false }]), `${base}: одно не скачано`);
    assert(!need([{ path: "/a", remote_only: false }]) && !need([]), `${base}: всё скачано / ничего не выбрано`);
    assert(need([{ path: "/model.apk" }]), `${base}: о скачанности не сообщают — считаем, что качать`);
    assert(!need([{ path: "/mine.apk" }], (entry) => entry.path === "/mine.apk"), `${base}: свой APK — на устройстве`);
  }

  // 2) Android: этап приложений по Wi-Fi — видна, пока ADB не подключён; провод, «спросить» с проводом, другой этап — нет.
  {
    const file = "android/app/src/main/assets/js/app.js";
    const src = read(file);
    const code = slice(src, "  function updateWifiHint() {", "  // Способ подключения для ТЕКУЩЕГО этапа", file);
    const hint = { hidden: true };
    const shared = { document: makeDom().document, window: {} };
    run(read("android/app/src/main/assets/js/apps_tabs.js"), shared);
    const library = { path: "/apk/Навигация/navi.apk", name: "Navi", remote_only: true };
    const modelApp = { path: "/cars/M/files/pack/optional/panel.apk", name: "Panel" };  // 1.1.8: поля нет
    let selected = [library];
    const ctx = {
      document: { querySelector: (selector) => (selector === ".apps-dock-hint" ? hint : null) },
      window: shared.window, personalApks: [{ path: "/mine.apk", name: "Мой" }], apkLibrary: [library],
      stages: [{ index: 0, type: "apps", apps_connection: "wifi", standard_apks: [], standard_apks_optional: [modelApp] }],
      currentIndex: 0, adbConnected: false, selectedAppsForStage: () => ({ entries: selected }),
      connectionModeFor: (stage) => stage.mode || (stage.apps_connection === "wifi" ? "wifi" : "wired"),
    };
    run(code, ctx);
    ctx.updateWifiHint();
    assert(hint.hidden === false, "Android: Wi-Fi, не подключено, выбранное не скачано — видна");
    library.remote_only = false; ctx.updateWifiHint();
    assert(hint.hidden === true, "Android: всё выбранное скачано — скрыта");
    selected = []; ctx.updateWifiHint();
    assert(hint.hidden === true, "Android: ничего не выбрано — скрыта");
    selected = [{ path: "/mine.apk", name: "Мой" }]; ctx.updateWifiHint();
    assert(hint.hidden === true, "Android: только свой APK — скрыта");
    selected = [modelApp]; ctx.updateWifiHint();
    assert(hint.hidden === false, "Android: приложение модели, о скачанности не сообщают — видна");
    ctx.markDownloaded([modelApp.path]);                     // скачали перед подключением (apk_download_done)
    assert(modelApp.remote_only === false && hint.hidden === true, "Android: скачанное отмечено — скрыта");
    library.remote_only = true; selected = [library]; ctx.updateWifiHint();
    assert(hint.hidden === false, "Android: снова есть что скачивать — видна");
    ctx.adbConnected = true; ctx.updateWifiHint();
    assert(hint.hidden === true, "Android: подключено — скрыта");
    ctx.adbConnected = false; ctx.stages[0] = { index: 0, type: "apps", apps_connection: "wired" }; ctx.updateWifiHint();
    assert(hint.hidden === true, "Android: по проводу — скрыта");
    ctx.stages[0] = { index: 0, type: "apps", apps_connection: "ask", mode: "wifi" }; ctx.updateWifiHint();
    assert(hint.hidden === false, "Android: «спросить», выбран Wi-Fi — видна");
    ctx.stages[0] = { index: 0, type: "adb", mode: "wifi" }; ctx.updateWifiHint();
    assert(hint.hidden === true, "Android: не этап приложений — скрыта");
    // Обновляется там, где меняются подключение и переключатель, и при показе этапа.
    for (const [start, end] of [["  function setAdbStatus(", "  function updateWifiHint() {"],
      ["  function updateTransportBars(stage) {", "  function renderStage(stage) {"]]) {
      assert(slice(src, start, end, file).includes("updateWifiHint();"), `Android: ${start.trim()} зовёт updateWifiHint`);
    }
    const apps = slice(src, "  function renderAppsStage(page, stage) {", "    const result=appInstallResults[stage.index];", file);
    assert(apps.includes("window.AppTabs.wifiHint()") && /wizardContentEl\.after\(dock\);\s*updateWifiHint\(\);/.test(apps),
      "Android: подсказка в нижней панели этапа приложений");
    const choice = slice(src, "  function appendInlineAppSelection(", "  function appTabGroups(", file);
    assert(/AppTabs\.summary\(summary[^\n]*\n\s*updateWifiHint\(\);/.test(choice), "Android: выбор поменялся — пересчёт подсказки");
  }

  // 3) ПК: настоящие buildTransportBar + buildStartStopButtons — подсказка между «Выбрано» и кнопкой, следит за
  // переключателем «Провод»/«Wi-Fi» и подключением по Wi-Fi.
  {
    const file = "app/web/frontend/js/screens/stage_wizard.js";
    const src = read(file);
    const code = slice(src, "  function buildTransportBar(stage) {", "  function buildInstructionBlock(", file)
      + slice(src, "  function buildStartStopButtons(", "  // Wi-Fi: заранее докачать свои файлы этапа", file);
    const make = (connection) => {
      const { document, el } = pcDom();
      const wizard = el("div");
      const contentEl = el("div");
      wizard.append(contentEl);                               // панель уезжает из этапа сюда: contentEl.after(...)
      let connect = null;
      const window = { pywebview: { api: { install_list_devices: async () => [], install_wifi_connect: async () => ({ ok: true }) } } };
      run(read("app/web/frontend/js/apps_tabs.js"), { window, document });
      const ctx = run(code, {
        window, document, el, clear: (node) => node.replaceChildren(), noteDeviceFingerprint() {}, modelWifiPort: 5555,
        LabUI: { connection: (opts) => { connect = opts.connect; } }, Number, String, Boolean, Promise,
        runnerBusy: false, contentEl,
      });
      const stage = { index: 2, type: "apps", apps_connection: connection };
      const transport = ctx.buildTransportBar(stage);
      const panel = el("div");
      const state = { rows: [{ path: "/apk/a.apk", remote: true }] };   // строки выбранного (data-remote-only)
      panel._appChooser = { entries: () => state.rows.map((r) => ({ path: r.path, row: { dataset: r.remote ? { remoteOnly: "1" } : {} } })) };
      const summary = el("p", { class: "apps-dock-summary" });
      ctx.buildStartStopButtons(panel, stage, () => null, { startLabel: "Начать установку", transport, summary });
      const dock = wizard.children.find((node) => node.classList.contains("apps-dock"));
      return { transport, dock, connectNow: () => connect, bar: transport.element, state, panel };
    };

    const wifi = make("wifi");
    const order = wifi.dock.children.map((node) => node.className || node.tagName);
    assert(order.join(" | ") === "apps-dock-summary | apps-dock-hint | accent", `ПК: порядок в панели — ${order.join(" | ")}`);
    const hint = wifi.dock.children[1];
    assert(hint.hidden === false && TEXT.test(hint.textContent), "ПК: Wi-Fi, не подключено, есть что скачать — видна");
    wifi.state.rows = [{ path: "/apk/a.apk", remote: false }, { path: "/mine.apk", remote: false }];
    wifi.panel._syncWifiHint();                               // выбор поменялся (renderAppsStage: chooser.update)
    assert(hint.hidden === true, "ПК: всё выбранное скачано — скрыта");
    wifi.state.rows = []; wifi.panel._syncWifiHint();
    assert(hint.hidden === true, "ПК: ничего не выбрано — скрыта");
    wifi.state.rows = [{ path: "/cars/M/files/pack/optional/p.apk", remote: true }]; wifi.panel._syncWifiHint();
    assert(hint.hidden === false, "ПК: приложение модели не скачано — видна");
    assert(/if \(apk\.remote_only\) row\.dataset\.remoteOnly = "1";/.test(slice(src, "  function buildAppRow(apk, required) {", "  async function pickPersonalApks()", file)),
      "ПК: строка приложения помнит, что его ещё качать");
    assert(/panel\._syncWifiHint\?\.\(\)/.test(slice(src, "  async function renderAppsStage(", "  // Разделы дерева buildAppsTree", file)),
      "ПК: выбор поменялся — пересчёт подсказки");
    wifi.transport.wifiButton.click();                       // «Подключить Wi-Fi» в панели над этапом
    await wifi.connectNow()("192.168.1.10", 5555);
    assert(wifi.transport.wifiConnected() && hint.hidden === true, "ПК: подключились по Wi-Fi — скрыта");

    const wired = make("wired");
    assert(wired.dock.children[1].hidden === true, "ПК: по проводу — скрыта");

    const ask = make("ask");
    const askHint = ask.dock.children[1];
    assert(askHint.hidden === true, "ПК: «спросить», по умолчанию провод — скрыта");
    const [wiredBtn, wifiBtn] = ask.bar.children[0].children;
    wifiBtn.click();
    assert(ask.transport.mode() === "wifi" && askHint.hidden === false, "ПК: выбрали Wi-Fi — видна");
    wiredBtn.click();
    assert(askHint.hidden === true, "ПК: снова провод — скрыта");
  }
};
