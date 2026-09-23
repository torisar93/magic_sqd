// ПК, смена модели (screens/stage_wizard.js: open): install_load_stages докачивает инструкции новой
// модели (на macOS после переезда данных в Application Support — у каждой модели впервые), и всё это
// время на экране висел этап ПРЕДЫДУЩЕЙ модели, а прогресс уходил только строкой в свёрнутый лог
// (жалоба владельца, 2026-09-23). Проверяем: экран «Загружаем модель» сразу, прогресс sync_progress в
// нём, поздний ответ прошлой модели не перетирает новую, ошибка моста не оставляет вечную загрузку.
"use strict";
const { read, slice, assert, run } = require("./_util");

function node(tag, attrs = {}) {
  return { tag, attrs, children: [], style: {}, hidden: false, textContent: attrs.text || "", className: attrs.class || "",
    dataset: {}, classList: { remove() {} },
    appendChild(child) { child.parentElement = this; this.children.push(child); return child; } };
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return { promise, resolve, reject };
}

const flush = () => new Promise((r) => setImmediate(r));

module.exports = async function () {
  const src = read("app/web/frontend/js/screens/stage_wizard.js");
  const file = "stage_wizard.js";
  const code = [
    // состояние модуля — как в шапке stage_wizard.js (те же имена)
    "let model=null, stages=[], loadError=null, loadErrorNeedsUpdate=false, hasIntro=false, currentIndex=0;",
    "let loadingStatusEl=null, loadingFillEl=null, openGeneration=0, renderRevision=0;",
    "let chosenVariants={}, appSelection={}, personalApks=[], sharedApksPromise=null, activeRun=null, afterRunClose=null;",
    "let activeCommand=null, modelWifiPort=5555, modelWifi=false, installCompletedShown=false, writePermissionWarningShown=false;",
    "let sessionLog=[], sessionHasActivity=false, sessionSent=false, sessionToken='', nextAction=null;",
    "const commandResults=new Map(), prefetchedStages=new Set(), failedStages=new Map(), done=new Set(), historyStack=[];",
    "function ensureMounted(){} function flushSessionLog(){} function log(){} function advanceAfter(){}",
    "function render(){ renders.push({ key: model.key, stages: stages.map((s) => s.title), loadError }); }",
    slice(src, "  function updateSyncProgress(", "  // -- построение разметки", file),
    slice(src, "  async function open(selectedModel) {", "  // -- навигация", file),
    "this.__state = () => ({ stages, loadError, loadingStatusEl });",
  ].join("\n");

  const calls = [];
  const contentEl = node("div");
  contentEl.parentElement = node("div");
  const byId = { "main-progress": node("div"), "main-progress-fill": node("div"), "main-progress-label": node("div"),
    "log-panel": node("div") };
  const nav = { next: node("button"), back: node("button"), video: node("button"), label: node("span") };
  const ctx = {
    renders: [], Number, Math, Map, Set, Promise, setImmediate,
    contentEl, navNextBtn: nav.next, navBackBtn: nav.back, navVideoBtn: nav.video, navLabelEl: nav.label,
    clear: (n) => { n.children = []; },
    el: (tag, attrs, children = []) => { const n = node(tag, attrs); [].concat(children).forEach((c) => n.appendChild(c)); return n; },
    document: {
      querySelector: () => null,
      getElementById: (id) => byId[id],
      createElement: (tag) => node(tag),
    },
    window: {
      pywebview: { api: {
        install_load_stages: (key) => { const d = deferred(); calls.push({ key, d }); return d.promise; },
        install_standard_apks: async () => ({ required: [], optional: [] }),
        scanner_list_apks: async () => [],
      } },
      notice() {},
    },
  };
  run(code, ctx);
  const state = ctx.__state;
  const loadingBlock = () => contentEl.children.find((c) => c.className === "model-loading");

  // --- открытие: сразу экран загрузки, без этапа прошлой модели ---
  contentEl.children.push(node("h1", { text: "Доступ к ADB (прошлая модель)" }));
  const openA = ctx.open({ key: "A" });
  assert(contentEl.dataset.stageType === "loading", "сразу режим загрузки");
  assert(contentEl.children.length === 1 && loadingBlock(), "этап прошлой модели убран, вместо него — блок загрузки");
  assert(nav.next.style.display === "none" && nav.back.hidden === true, "навигация спрятана на время загрузки");
  const track = loadingBlock().children.find((c) => c.className === "model-loading-track");
  assert(track.hidden, "полоса прогресса скрыта, пока качать нечего");

  // --- прогресс докачки — в самом экране загрузки ---
  ctx.updateSyncProgress(50, 200, 3, 12);
  assert(state().loadingStatusEl.textContent === "Скачиваем файлы модели: 25% · 3 из 12 файлов",
    "строка прогресса: " + state().loadingStatusEl.textContent);
  assert(!track.hidden && track.children[0].style.width === "25%", "полоса видна и заполнена на 25%");

  // --- техник ушёл и открыл другую модель, пока первая ещё грузилась ---
  const openB = ctx.open({ key: "B" });
  calls[1].d.resolve({ stages: [{ title: "Этап B", type: "instruction" }], install_log_token: "tB" });
  await openB;
  calls[0].d.resolve({ stages: [{ title: "Этап A", type: "instruction" }], install_log_token: "tA" });
  await openA;
  await flush();
  assert(ctx.renders.length === 1 && ctx.renders[0].key === "B", "отрисована только модель B: " + JSON.stringify(ctx.renders));
  assert(state().stages[0].title === "Этап B", "поздний ответ модели A не перетёр этапы B");
  assert(state().loadingStatusEl === null, "режим загрузки снят");
  assert(ctx.window.__installLogSessionToken === "tB", "токен журнала — от модели B");

  // --- сбой моста: ошибка на экране, а не вечная загрузка ---
  const openC = ctx.open({ key: "C" });
  calls[2].d.reject(new Error("timeout"));
  await openC;
  const last = ctx.renders[ctx.renders.length - 1];
  assert(last.key === "C" && /Не удалось открыть модель: timeout/.test(last.loadError || ""), "ошибка показана: " + JSON.stringify(last));
};
