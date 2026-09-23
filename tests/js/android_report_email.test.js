// Android: «Сообщить о проблеме» — почта для ответа. Вошедшему — подпись с почтой аккаунта (поле не
// нужно), остальным — поле (запомненная почта подставлена, кривая не отправляется).
// (android/app/src/main/assets/js/app.js: showReportModal/onReportResult)
"use strict";
const { read, slice, assert, run } = require("./_util");

function mkEl(tag, attrs = {}, children = []) {
  return {
    tag, attrs, children, textContent: attrs.text || "", value: "", disabled: false, listeners: {},
    addEventListener(type, handler) { this.listeners[type] = handler; },
  };
}
function find(node, predicate) {
  if (predicate(node)) return node;
  for (const child of node.children || []) { const hit = find(child, predicate); if (hit) return hit; }
  return null;
}

module.exports = async function () {
  const file = "android/app/src/main/assets/js/app.js";
  const code = slice(read(file), "  const REPORT_MODEL_REASONS = [", "  function showStatusWarningModal(", file);
  const calls = [];
  let info = {};
  let modal = null;
  const ctx = {
    screenWizard: { classList: { contains: () => false } }, model: null, el: mkEl,
    showModal: (children) => { modal = { tag: "modal", children, removed: false,
      querySelector: () => ({ classList: { add() {} } }), remove() { this.removed = true; } }; return modal; },
    Bridge: { call: (name, args) => { calls.push([name, args]); return name === "report_info" ? info : {}; } },
    setTimeout: () => {},
  };
  const api = run(code + "\nthis.__api = { showReportModal, onReportResult };", ctx).__api;
  const input = () => find(modal, (n) => n.tag === "input");
  const sendButton = () => find(modal, (n) => n.tag === "button" && n.textContent === "Отправить");
  const status = () => find(modal, (n) => n.tag === "p" && (n.attrs.class || "").includes("report-status"));
  const sent = () => calls.filter(([name]) => name === "report_send");

  info = { account_email: "", saved_email: "tech@example.com" };
  api.showReportModal();
  assert(input() && input().value === "tech@example.com", "без входа — поле с запомненной почтой");
  input().value = "не почта";
  sendButton().listeners.click();
  assert(!sent().length && /почту/.test(status().textContent), "кривая почта не отправляется");
  input().value = " tech@example.com ";
  sendButton().listeners.click();
  assert(sent().length === 1 && sent()[0][1].email === "tech@example.com", "почта уходит: " + JSON.stringify(sent()[0]));
  api.onReportResult({ result: { ok: true } });
  assert(status().textContent.includes("tech@example.com"), "итог называет почту: " + status().textContent);

  info = { account_email: "owner@example.com", saved_email: "tech@example.com" };
  api.showReportModal();
  assert(!input(), "вошедшему поле не нужно");
  assert(find(modal, (n) => (n.textContent || "").includes("owner@example.com")), "подпись с почтой аккаунта");
  sendButton().listeners.click();
  assert(sent().length === 2 && sent()[1][1].email === "", "вошедший: почта из сессии, не из поля");
  api.onReportResult({ result: { ok: true } });
  assert(status().textContent.includes("owner@example.com"), "итог — почта аккаунта");

  info = {};
  api.showReportModal();
  assert(input() && input().value === "", "ничего не запомнено — пустое поле");
  sendButton().listeners.click();
  api.onReportResult({ result: { ok: true } });
  assert(status().textContent === "Спасибо! Обращение отправлено.", "без почты — обычное спасибо");
};
