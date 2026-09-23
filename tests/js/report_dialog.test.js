// ПК: окно «Сообщить о проблеме» работает и без выбранной модели (только общие причины, пустые
// марка/модель) и с моделью (все причины) — app/web/frontend/js/screens/dialogs.js: report.
// Почта для ответа: вошедшему — почта аккаунта (поле скрыто), остальным — поле (запоминается).
"use strict";
const { read, slice, assert, run } = require("./_util");

function mkEl() {
  return {
    children: [], value: "", textContent: "", disabled: false, listeners: {},
    appendChild(c) { this.children.push(c); }, addEventListener(t, h) { this.listeners[t] = h; },
    showModal() { this.opened = true; }, close() { this.opened = false; },
  };
}

module.exports = async function () {
  const file = "app/web/frontend/js/screens/dialogs.js";
  const src = read(file);
  const endMarker = "    return { init, open };\n  })();";
  const a = src.indexOf("  const report = (() => {");
  const b = src.indexOf(endMarker, a) + endMarker.length;
  assert(a > 0 && b > a, "маркеры диалога report найдены");
  const els = {};
  const getEl = (id) => els[id] || (els[id] = mkEl());
  const sent = [];
  let info = { available: true };
  const ctx = {
    document: { getElementById: getEl, createElement: () => mkEl() }, clear: (n) => { n.children = []; },
    window: { notice: async () => {}, pywebview: { api: {
      report_get_info: async () => info,
      report_send: async (...args) => { sent.push(args); return { ok: true, message: "Спасибо!" }; },
    } } },
  };
  const rep = run(src.slice(a, b) + "\nthis.__report = report;", ctx).__report;
  rep.init();
  const options = () => els["report-reason"].children.map((o) => o.value);

  await rep.open(null);
  assert(els["report-dialog"].opened, "диалог открылся без модели");
  assert(els["report-dialog-title"].textContent === "Сообщить о проблеме — работа программы", "заголовок для программы в целом");
  assert(!options().includes("Не работает этап установки") && options().includes("Ошибка в работе программы") && options().at(-1) === "Другое", "только общие причины: " + options());
  els["report-reason"].value = "Предложение или идея"; els["report-description"].value = "  хочу тёмную тему ";
  await els["report-send"].listeners.click();
  assert(JSON.stringify(sent[0]) === JSON.stringify(["", "", "Предложение или идея", "хочу тёмную тему", ""]), "пустые марка/модель: " + JSON.stringify(sent[0]));
  assert(els["report-email-field"].hidden === false && els["report-account-note"].hidden === true, "без входа — поле почты");

  await rep.open({ brand: "Geely", name: "Monjaro", modification: "SE", no_instruction: false });
  assert(els["report-dialog-title"].textContent.includes("Geely / Monjaro — SE"), "заголовок с моделью");
  assert(options()[0] === "Появился способ установки" && options().includes("Ошибка в работе программы") && options().at(-1) === "Другое", "все причины: " + options());
  els["report-reason"].value = "Не работает этап установки"; els["report-description"].value = "x";
  await els["report-send"].listeners.click();
  assert(sent[1][0] === "Geely" && sent[1][1] === "Monjaro — SE", "марка/модель модели: " + JSON.stringify(sent[1]));

  // Не вошёл: запомненная почта подставлена, кривая — не отправляется, нормальная — уходит 5-м аргументом.
  info = { available: true, saved_email: "tech@example.com" };
  await rep.open(null);
  assert(els["report-email"].value === "tech@example.com", "запомненная почта подставлена");
  els["report-email"].value = "не почта";
  await els["report-send"].listeners.click();
  assert(sent.length === 2 && /почту/.test(els["report-status"].textContent), "кривая почта не отправляется");
  els["report-email"].value = " tech@example.com ";
  await els["report-send"].listeners.click();
  assert(sent[2][4] === "tech@example.com", "почта уходит: " + JSON.stringify(sent[2]));

  // Вошёл: поле скрыто, подпись с почтой аккаунта, почта из поля не уходит.
  info = { available: true, account_email: "owner@example.com", saved_email: "tech@example.com" };
  await rep.open(null);
  assert(els["report-email-field"].hidden === true && els["report-account-note"].hidden === false, "вошёл — без поля");
  assert(els["report-account-note"].textContent.includes("owner@example.com"), "подпись с почтой аккаунта");
  await els["report-send"].listeners.click();
  assert(sent[3][4] === "", "вписанная почта не нужна вошедшему: " + JSON.stringify(sent[3]));
};
