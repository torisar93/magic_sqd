// macOS 27 (WebKit с appearance:base-select): выпадающий список в окне выбора приложения открывался невидимым (жалоба
// техника на Jolion 2026, 29.10.2026; воспроизведено в собранной 1.1.15 с эмулятором). Там список пакетов — прямо в окне
// (select.size), без заглушки «— выберите приложение —»; на Windows (Chromium) и старом macOS — прежний выпадающий.
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "app/web/frontend/js/screens/stage_wizard.js";
  const src = read(file);
  const fn = slice(src, "  function showAskInputDialog(event) {", "  // Закрыть окно этапа с итогом", file);
  const isPkg = slice(src, "  function isPackageList(choices) {", "  function showAskInputDialog(event) {", file);
  function makeDom(ua, mode) {
    const els = {};
    const mk = (id) => ({ id, style: {}, dataset: {}, children: [], textContent: "", value: "", size: 0, selectedIndex: 0,
      set innerHTML(v) { this.children = []; }, appendChild(c) { this.children.push(c); }, showModal() { this.open = true; } });
    ["ask-input-dialog", "ask-input-prompt", "ask-input-choices", "ask-input-value"].forEach((id) => { els[id] = mk(id); });
    return { els, ctx: { MANUAL_CHOICE_VALUE: "__manual__", navigator: { userAgent: ua }, window: { Select08: { mode } },
      document: { getElementById: (id) => els[id], createElement: () => ({}) } } };
  }
  const pk = ["ru.yandex.music", "com.vk.vkvideo"];
  const mac = makeDom("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)", "native-customizable");
  run(isPkg + fn + "\nthis.show = showAskInputDialog;", mac.ctx).show({ id: "1", prompt: "Выберите приложение", choices: pk });
  const sel = mac.els["ask-input-choices"];
  assert(sel.size === 3 && sel.selectedIndex === -1, `macOS 27: список в окне (size=${sel.size}, index=${sel.selectedIndex})`);
  assert(!sel.children.some((o) => o.textContent === "— выберите приложение —"), "без заглушки в списке");
  const win = makeDom("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36 Edg/152.0", "native-customizable");
  run(isPkg + fn + "\nthis.show = showAskInputDialog;", win.ctx).show({ id: "2", prompt: "Выберите приложение", choices: pk });
  assert(win.els["ask-input-choices"].size === 0 && win.els["ask-input-choices"].children[0].textContent === "— выберите приложение —",
    "Windows: прежний выпадающий список с заглушкой");
};
