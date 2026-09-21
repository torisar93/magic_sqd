// ПК: запись на флешку показывает то же кольцо с процентом/очередью файлов, что и установка приложений
// (жалоба клиента, 2026-09-21 — видно только окно с анимацией, реального прогресса нет). Проверяем, что
// app/web/frontend/js/screens/dialogs.js: usb зовёт usb_list_items ПЕРЕД usb_start и передаёт очередь в
// window.LabUI.busy, а при пустом списке/ошибке сводки тихо остаётся на старой индетерминированной полосе.
"use strict";
const { read, slice, assert, run, sleep } = require("./_util");

function mkEl() {
  const listeners = {};
  const node = {
    children: [], value: "", textContent: "", disabled: false, hidden: false,
    dataset: {}, style: {}, classList: { toggle() {} }, listeners,
    appendChild(c) { this.children.push(c); },
    addEventListener(t, h) { listeners[t] = h; },
    removeEventListener(t) { delete listeners[t]; },
    setAttribute() {}, removeAttribute() {},
    querySelector() { return mkEl(); },
    querySelectorAll() { return []; },
    closest() { return mkEl(); },
    showModal() { this.open = true; }, close() { this.open = false; },
    scrollTop: 0, scrollHeight: 0,
  };
  return node;
}

module.exports = async function () {
  const file = "app/web/frontend/js/screens/dialogs.js";
  const src = read(file);
  const endMarker = "    return { init, open };\n  })();";
  const a = src.indexOf("  const usb = (() => {");
  const b = src.indexOf(endMarker, a) + endMarker.length;
  assert(a > 0 && b > a, "маркеры диалога usb найдены");

  const els = {};
  const getEl = (id) => els[id] || (els[id] = mkEl());
  const fsRadios = [{ value: "FAT32", checked: false }, { value: "exFAT", checked: false }];
  const pywebviewCalls = [];
  const labUiBusyCalls = [];
  const eventHandlers = {};
  let listItemsResult = { ok: true, items: [] };

  const ctx = {
    document: {
      getElementById: getEl,
      createElement: () => mkEl(),
      querySelectorAll: (selector) => (selector.includes("usb-fs") ? fsRadios : []),
    },
    clear: (n) => { n.children = []; },
    window: {
      events: { on: (kind, handler) => { eventHandlers[kind] = handler; } },
      classifyLogLevel: () => "info",
      confirmDialog: async () => true,
      LabUI: { busy: (...callArgs) => { labUiBusyCalls.push(callArgs); return mkEl(); } },
      pywebview: {
        api: {
          usb_list_drives: async () => [{ letter: "E:", label: "USB", total_bytes: 1000, free_bytes: 500, display: "USB (E:)" }],
          usb_list_items: async (...callArgs) => { pywebviewCalls.push(["usb_list_items", callArgs]); return listItemsResult; },
          usb_start: async (...callArgs) => { pywebviewCalls.push(["usb_start", callArgs]); return { ok: true }; },
          usb_cancel: async () => ({ ok: true }),
        },
      },
    },
  };

  const usbApi = run(src.slice(a, b) + "\nthis.__usb = usb;", ctx).__usb;
  usbApi.init();

  async function startWriting(opts) {
    usbApi.open(opts);
    await sleep(0); // дождаться refreshDrives() внутри open()
    els["usb-drive"].value = "E:"; // техник выбрал накопитель из списка
    await els["usb-start"].listeners.click();
  }

  // 1) Список файлов не пуст -> кольцо активируется с очередью, старая полоса скрыта.
  listItemsResult = { ok: true, items: [{ name: "f.bin", path: "/local/f.bin", size: 100 }] };
  await startWriting({ modelKey: "Test/Model", stageIndex: 2, variant: "Full", selectedApkPaths: ["/a.apk"], titleSuffix: "x" });

  assert(pywebviewCalls[0][0] === "usb_list_items", "usb_list_items вызван первым: " + JSON.stringify(pywebviewCalls));
  assert(JSON.stringify(pywebviewCalls[0][1]) === JSON.stringify(["Test/Model", 2, "Full", ["/a.apk"]]),
    "аргументы usb_list_items совпадают с opts: " + JSON.stringify(pywebviewCalls[0][1]));
  assert(pywebviewCalls[1][0] === "usb_start", "usb_start вызван после сводки списка файлов");
  assert(labUiBusyCalls.length === 1, "LabUI.busy вызван ровно один раз: " + labUiBusyCalls.length);
  assert(labUiBusyCalls[0][0] === els["usb06-ring"], "LabUI.busy вызван на контейнере кольца");
  assert(labUiBusyCalls[0][1] === "Запись на флешку", "заголовок кольца");
  assert(JSON.stringify(labUiBusyCalls[0][2]) === JSON.stringify(listItemsResult.items), "очередь файлов передана как есть");
  assert(els["usb06-ring"].hidden === false, "кольцо показано");
  assert(els["usb06-ring"].dataset.stageIndex === "2", "stageIndex размечен для LabUI.progress: " + els["usb06-ring"].dataset.stageIndex);
  assert(els["usb-progress"].hidden === true, "старая индетерминированная полоса скрыта, пока активно кольцо");

  els["usb06-ring"].children.push({ fake: true }); // имитируем то, что реальный LabUI.busy вставил бы внутрь
  eventHandlers.usb_finished({ success: true, message: "Готово" });
  assert(els["usb06-ring"].hidden === true, "кольцо скрыто после завершения записи");
  assert(els["usb06-ring"].children.length === 0, "кольцо очищено после завершения записи");

  // 2) Пустой список файлов -> кольцо не показывается, старое поведение (индетерминированная полоса) как раньше.
  pywebviewCalls.length = 0; labUiBusyCalls.length = 0;
  listItemsResult = { ok: true, items: [] };
  await startWriting({ modelKey: "Test/Model", stageIndex: 0, variant: null, selectedApkPaths: [] });

  assert(labUiBusyCalls.length === 0, "LabUI.busy НЕ вызван при пустом списке файлов");
  assert(els["usb06-ring"].hidden === true, "кольцо остаётся скрытым без списка файлов");
  assert(els["usb-progress"].hidden === false, "старая индетерминированная полоса показана как раньше");
  assert(pywebviewCalls.some((c) => c[0] === "usb_start"), "запись всё равно стартовала без списка файлов");
  eventHandlers.usb_finished({ success: true, message: "Готово" }); // сбросить running перед следующим open()

  // 3) usb_list_items падает с ошибкой -> сводка молча пропускается, сама запись не блокируется.
  pywebviewCalls.length = 0; labUiBusyCalls.length = 0;
  const okListItems = ctx.window.pywebview.api.usb_list_items;
  ctx.window.pywebview.api.usb_list_items = async () => { throw new Error("boom"); };
  await startWriting({ modelKey: "Test/Model", stageIndex: 0, variant: null, selectedApkPaths: [] });
  ctx.window.pywebview.api.usb_list_items = okListItems;

  assert(labUiBusyCalls.length === 0, "ошибка сводки списка файлов не показывает кольцо");
  assert(els["usb06-ring"].hidden === true, "кольцо остаётся скрытым при ошибке сводки");
  assert(pywebviewCalls.some((c) => c[0] === "usb_start"), "запись всё равно стартовала несмотря на сбой сводки");
};
