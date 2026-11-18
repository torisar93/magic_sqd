// macOS 27, собранная программа (WKWebView, SDK 12.1): base-select заявлен, а Popover API выключен — ':popover-open'
// «не селектор». Встроенный список открывался невидимым («Выберите накопитель», владелец 18.11.2026), свой список
// обрезался краем окна («полторы строки»). select08.js: без Popover API — свой список; select-polyfill.js: без Popover
// API — без top layer, а на время раскрытия окно не обрезает, если его содержимое помещается.
"use strict";
const { read, slice, assert, run } = require("./_util");

function cssWith(popover) {
  return { supports: (a, b) => {
    const q = b === undefined ? String(a) : `${a}:${b}`;
    if (q.includes(":popover-open")) return popover;
    return /base-select|::picker\(select\)/.test(q);
  } };
}

function classList() {
  const set = new Set();
  return { toggle: (c, on) => (on ? set.add(c) : set.delete(c)), has: (c) => set.has(c) };
}

module.exports = async function () {
  const s08 = read("app/web/frontend/js/select08.js");
  for (const [popover, expected] of [[true, "native-customizable"], [false, "native-dark"]]) {
    const ctx = { CSS: cssWith(popover), document: { documentElement: { classList: classList() } } };
    ctx.window = ctx;
    run(s08, ctx);
    assert(ctx.Select08.mode === expected, `Popover API ${popover ? "есть" : "выключен"} → ${expected} (было ${ctx.Select08.mode})`);
  }

  const file = "app/web/frontend/js/select-polyfill.js";
  const src = read(file);
  const detect = slice(src, "  let topLayer = false;", "  const GAP = 4;", file);
  for (const [popover, expected] of [[true, true], [false, false]]) {
    const ctx = run(detect + "\nthis.topLayer = topLayer;", {
      CSS: cssWith(popover), HTMLElement: function HTMLElement() {} });
    ctx.HTMLElement.prototype.showPopover = function () {};
    const again = run(detect + "\nthis.topLayer = topLayer;", { CSS: cssWith(popover), HTMLElement: ctx.HTMLElement });
    assert(again.topLayer === expected, `top layer при Popover API=${popover}: ${again.topLayer}`);
  }

  const clip = slice(src, "  function unclipDialog(trigger) {", "  function closeAllExcept(exceptPopup) {", file);
  const ctx = run(clip + "\nthis.unclip = unclipDialog; this.restore = restoreDialogClip;", {});
  function dialog(scrollH, clientH, openPopups = 0) {
    return { style: { overflow: "auto" }, dataset: {}, scrollHeight: scrollH, clientHeight: clientH,
      querySelector: () => (openPopups ? {} : null) };
  }
  const short = dialog(300, 300);
  const trigger = { closest: () => short };
  ctx.unclip(trigger);
  assert(short.style.overflow === "visible", "помещающееся окно на время раскрытия не обрезает");
  ctx.restore({ closest: () => short });
  assert(short.style.overflow === "auto" && !("cselOverflow" in short.dataset), "после закрытия — как было");
  const tall = dialog(900, 600);
  ctx.unclip({ closest: () => tall });
  assert(tall.style.overflow === "auto", "прокручиваемое окно не трогаем");
  ctx.unclip({ closest: () => null });
  assert(/if \(!topLayer\) unclipDialog\(trigger\);\n\s+popup\.hidden = false;/.test(src), "обрезка снимается ДО показа списка");
};
