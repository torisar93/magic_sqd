// Android: «Скопировать лог» на Huawei (Android 10–12) — WebView без clipboard API, было «Не удалось скопировать лог
// автоматически» (№17312, №17259, №17313). Теперь — запасной способ через выделение (как copyText в stage_run.js).
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "android/app/src/main/assets/js/app.js";
  const src = read(file);
  const code = slice(src, "  async function copyLogText(", "  function setMenuActionLabel(", file)
    + "\nthis.copyLogText = copyLogText;";

  function makeDoc(execOk) {
    const doc = { copied: null, appended: 0, removed: 0 };
    doc.createElement = () => {
      const area = { value: "", style: {}, setAttribute() {}, select() { doc.selected = area.value; },
        setSelectionRange() {}, remove() { doc.removed++; } };
      return area;
    };
    doc.body = { append() { doc.appended++; } };
    doc.execCommand = (cmd) => {
      if (cmd !== "copy" || execOk === "throw") throw new Error("no");
      if (execOk) doc.copied = doc.selected;
      return execOk;
    };
    return doc;
  }

  // Clipboard API есть — им и копируем.
  let written = null;
  let doc = makeDoc(true);
  let ctx = run(code, { navigator: { clipboard: { writeText: async (t) => { written = t; } } }, document: doc });
  assert(await ctx.copyLogText("лог") === true, "clipboard API работает");
  assert(written === "лог" && doc.appended === 0, "запасной способ не нужен");

  // Huawei: clipboard API отказал — копируем выделением.
  doc = makeDoc(true);
  ctx = run(code, { navigator: { clipboard: { writeText: async () => { throw new Error("NotAllowedError"); } } }, document: doc });
  assert(await ctx.copyLogText("лог 2") === true, "скопировано запасным способом");
  assert(doc.copied === "лог 2" && doc.removed === 1, "текст выделен, поле убрано");

  // Нет clipboard вовсе и execCommand не скопировал — честное «не удалось».
  doc = makeDoc(false);
  ctx = run(code, { navigator: {}, document: doc });
  assert(await ctx.copyLogText("x") === false, "оба способа не сработали");
  doc = makeDoc("throw");
  ctx = run(code, { navigator: {}, document: doc });
  assert(await ctx.copyLogText("x") === false && doc.removed === 1, "исключение execCommand — тоже «нет», поле убрано");

  assert(!/navigator\.clipboard\.writeText\(logPanelEl/.test(src), "обе кнопки «Скопировать лог» — через copyLogText");
  assert((src.match(/await copyLogText\(logPanelEl\.innerText\)/g) || []).length === 2, "меню и панель лога");
};
