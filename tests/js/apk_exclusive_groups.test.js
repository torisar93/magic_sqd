// «Только одно из группы» (<файл>.json "exclusive_group"; группы заводит админ, владелец 2026-09-25: «ставишь
// галочку на одном, пытаешься на второе — и она просто перепрыгивает»). Общая функция apps_tabs.js
// (AppTabs.exclusiveGroups) для ПК и Android — здесь на заглушках строк: [data-apk-path] с галочкой внутри.
// Ничего не блокируется и не подписывается — только снимается прежнее отмеченное из той же группы.
"use strict";
const { read, assert, run } = require("./_util");

function fakeDom(selection) {
  const handlers = [];
  const rows = [];
  const container = {
    addEventListener: (type, fn) => { if (type === "change") handlers.push(fn); },
    querySelectorAll: (sel) => (sel === "[data-apk-path]" ? rows : []),
    handlers,
  };
  function row(path) {
    const r = { dataset: { apkPath: path } };
    const box = {
      type: "checkbox", checked: false, disabled: false,
      matches: (sel) => sel === 'input[type="checkbox"]',
      closest: (sel) => (sel === "[data-apk-path]" ? r : null),
      // Как в программе: сначала обработчик самой строки обновляет выбор, потом событие всплывает к контейнеру.
      dispatchEvent: () => {
        if (box.checked) selection.add(path); else selection.delete(path);
        handlers.forEach((fn) => fn({ target: box }));
      },
    };
    r.box = box;
    r.querySelector = (sel) => (sel === 'input[type="checkbox"]' ? box : null);
    rows.push(r);
    return r;
  }
  const click = (r) => { r.box.checked = !r.box.checked; r.box.dispatchEvent(); };
  const sync = () => rows.forEach((r) => { r.box.checked = selection.has(r.dataset.apkPath); });
  return { container, row, click, sync };
}

module.exports = async function () {
  for (const file of ["app/web/frontend/js/apps_tabs.js", "android/app/src/main/assets/js/apps_tabs.js"]) {
    const selection = new Set();
    const dom = fakeDom(selection);
    const ctx = run(read(file), { window: {}, document: {}, Event: function Event() {} });
    const apks = [
      { path: "/l1.apk", name: "GLauncher Link", exclusive_group: "Лаунчер" },
      { path: "/l2.apk", name: "3screen", exclusive_group: " лаунчер " },  // регистр и пробелы не важны
      { path: "/n1.apk", name: "Навигатор v29", exclusive_group: "Навигатор" },
      { path: "/u.apk", name: "AIMP" },
    ];
    const [l1, l2, n1, u] = apks.map((apk) => dom.row(apk.path));
    const api = ctx.window.AppTabs;
    const chosen = () => [...selection].sort().join(",");
    api.exclusiveGroups(dom.container, apks, (p) => selection.has(p));

    dom.click(l1);
    assert(chosen() === "/l1.apk", `${file}: отмечен первый лаунчер`);
    dom.click(l2);
    assert(chosen() === "/l2.apk" && !l1.box.checked, `${file}: второй лаунчер — галочка перепрыгнула`);
    dom.click(n1);
    dom.click(u);
    assert(chosen() === "/l2.apk,/n1.apk,/u.apk", `${file}: другие группы и без группы не мешают`);
    dom.click(l2);
    assert(chosen() === "/n1.apk,/u.apk", `${file}: снять можно как обычно`);
    assert(![l1, l2, n1, u].some((r) => r.box.disabled), `${file}: ничего не блокируется`);

    // Уже выбраны оба (старое состояние) — при показе остаётся первый по списку.
    selection.add("/l1.apk"); selection.add("/l2.apk"); dom.sync();
    api.exclusiveGroups(dom.container, apks, (p) => selection.has(p));
    assert(chosen() === "/l1.apk,/n1.apk,/u.apk", `${file}: двух из группы не бывает`);
    assert(dom.container.handlers.length === 1, `${file}: повторный вызов не вешает второй слушатель`);
  }
};
