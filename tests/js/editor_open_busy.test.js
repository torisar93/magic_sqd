// ПК, «Изменить» машину: car_load_spec сначала докачивает все файлы модели (car_editor_api.py:
// _sync_own_files) — на машине, где модель ещё не качалась, это минуты. Раньше экран всё это время не
// менялся вовсе и выглядело так, будто редактор не открывается (macOS, 2026-09-23, Geely Preface
// Обычная). Проверяем окно «Открываем редактор» (components/modal.js: busyDialog) и обвязку в
// screens/graph_wizard.js: open — прогресс из editor_sync_progress, закрытие и отписка при любом исходе.
"use strict";
const { read, slice, assert, run } = require("./_util");

function mkEl() {
  const el = {
    open: false, textContent: "", value: undefined, style: {}, listeners: {}, subs: {},
    showModal() { this.open = true; }, close() { this.open = false; },
    addEventListener(type, handler) { this.listeners[type] = handler; },
    removeAttribute(name) { if (name === "value") this.value = undefined; },
    querySelector(sel) { return this.subs[sel] || (this.subs[sel] = mkEl()); },
    appendChild() {},
  };
  return el;
}

function mkEvents() {
  const handlers = {};
  return {
    handlers,
    on(kind, h) { (handlers[kind] = handlers[kind] || []).push(h); },
    off(kind, h) { handlers[kind] = (handlers[kind] || []).filter((x) => x !== h); },
    emit(kind, e) { (handlers[kind] || []).forEach((h) => h(e)); },
  };
}

module.exports = async function () {
  const dialogs = [];
  const ctx = {
    document: { createElement: () => { const el = mkEl(); dialogs.push(el); return el; }, body: { appendChild() {} } },
    window: {}, Number,
  };
  run(read("app/web/frontend/js/components/modal.js"), ctx);
  const { window } = ctx;
  assert(typeof window.busyDialog === "function", "busyDialog экспортирован");

  // --- сам busyDialog ---
  const busy = window.busyDialog("Открываем редактор", "Проверяем файлы модели…");
  const dlg = dialogs.find((d) => d.subs["#app-busy-title"]);
  const title = dlg.subs["#app-busy-title"], message = dlg.subs["#app-busy-message"], bar = dlg.subs["#app-busy-progress"];
  assert(dlg.open, "окно открыто сразу");
  assert(title.textContent === "Открываем редактор", "заголовок");
  assert(bar.value === undefined, "до первых чисел — неопределённый прогресс");
  busy.progress(50, 200, 3, 11);
  assert(bar.value === 25 && message.textContent === "25% скачано · 3 из 11 файлов", "прогресс: " + message.textContent);
  busy.progress(0, 0);
  assert(message.textContent === "25% скачано · 3 из 11 файлов", "total=0 не сбрасывает показанное");
  let prevented = false;
  dlg.listeners.cancel({ preventDefault() { prevented = true; } });
  assert(prevented, "Esc не закрывает окно ожидания");
  busy.close();
  assert(!dlg.open, "close() закрывает");

  // --- обвязка в graph_wizard.js: open (ветка «Изменить») ---
  const file = "app/web/frontend/js/screens/graph_wizard.js";
  const body = slice(read(file), "    if (isEditing) {\n      // car_load_spec сначала докачивает", "    } else {\n      brand = \"\";", file);
  run(`async function openEditing(editModel) {
    let brand, model, modification, wifi, wifiPort, status, steps;
    const isEditing = true;
${body}    }
    return { brand, steps };
  }`, ctx);

  window.events = mkEvents();
  let busyOpenDuringLoad = null;
  window.pywebview = { api: { car_load_spec: async () => {
    busyOpenDuringLoad = dlg.open;
    window.events.emit("editor_sync_progress", { done: 1, total: 4, files_done: 1, files_total: 4 });
    assert(message.textContent === "25% скачано · 1 из 4 файлов", "прогресс докачки виден во время ожидания");
    return { brand: "Geely", model: "Preface", modification: "Обычная", wifi: false, wifi_port: 5555, steps: [{ id: 0 }] };
  } } };
  const result = await ctx.openEditing({ key: "Geely/Preface/Обычная" });
  assert(busyOpenDuringLoad === true, "окно открыто, пока идёт car_load_spec");
  assert(!dlg.open, "после загрузки окно закрыто");
  assert((window.events.handlers.editor_sync_progress || []).length === 0, "подписка снята");
  assert(result.brand === "Geely" && result.steps.length === 1, "данные модели разобраны как раньше");

  // Ошибка из Python ({error}) — окно ожидания закрыто ДО notice с текстом ошибки.
  let busyOpenAtNotice = null;
  window.notice = async () => { busyOpenAtNotice = dlg.open; };
  window.pywebview.api.car_load_spec = async () => ({ error: "нет _wizard_spec.json" });
  await ctx.openEditing({ key: "x" });
  assert(busyOpenAtNotice === false, "при ошибке сначала закрыли ожидание, потом показали notice");

  // Исключение моста — окно всё равно закрыто, подписка снята.
  window.pywebview.api.car_load_spec = async () => { throw new Error("мост упал"); };
  let threw = false;
  try { await ctx.openEditing({ key: "x" }); } catch (e) { threw = true; }
  assert(threw && !dlg.open, "исключение не оставляет висеть окно ожидания");
  assert((window.events.handlers.editor_sync_progress || []).length === 0, "и подписку тоже");
};
