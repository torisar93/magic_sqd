// Окно выбора приложения: всегда выпадающий список с заглушкой «— выберите приложение —», и на macOS 27, и на Windows.
// 29.10.2026 на macOS 27 его сделали списком «прямо в окне» (select.size) — невидимый выпадающий; 18.11.2026 причина
// найдена (в программе выключен Popover API — js/select08.js включает свой список), а вид «в окне» владелец отверг:
// «выглядит супер уродски».
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
  assert(sel.size === 0 && sel.children[0].textContent === "— выберите приложение —", `macOS 27: выпадающий с заглушкой (size=${sel.size})`);
  const win = makeDom("Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0 Safari/537.36 Edg/152.0", "native-customizable");
  run(isPkg + fn + "\nthis.show = showAskInputDialog;", win.ctx).show({ id: "2", prompt: "Выберите приложение", choices: pk });
  assert(win.els["ask-input-choices"].size === 0 && win.els["ask-input-choices"].children[0].textContent === "— выберите приложение —",
    "Windows: прежний выпадающий список с заглушкой");
};
