// ПК, редактор моделей: «Кто видит» у кнопки сохранения (скрытые модели для групп пользователей,
// server/user_groups.py). Только администратору; не меняли выбор — car_save получает null и доступ
// на сервере не трогается (app/web/frontend/js/screens/graph_wizard.js: loadAccessControl/accessChoice).
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "app/web/frontend/js/screens/graph_wizard.js";
  const code = "let accessInitial = null; let accessMultiGroups = [];\n"
    + slice(read(file), "  async function loadAccessControl() {", "  // Шаги без сохранённой позиции", file);
  const box = { hidden: true, title: "" };
  const select = { children: [], value: "", replaceChildren(...opts) {
    this.children = opts; const chosen = opts.find((o) => o.selected !== null); this.value = chosen ? chosen.value : opts[0].value;
  } };
  let info = null;
  const calls = [];
  const ctx = {
    isPendingModel: false, isEditing: true, editModelKey: "cars/Haval/Jolion",
    document: { getElementById: (id) => (id === "graph-wizard-access" ? box : select) },
    el: (tag, attrs) => ({ tag, value: attrs.value, text: attrs.text, selected: attrs.selected }),
    window: { pywebview: { api: { car_get_access: async (key) => { calls.push(key); return info; } } } },
  };
  const api = run(code + "\nthis.__api = { loadAccessControl, accessChoice };", ctx).__api;
  const groups = [{ id: 1, name: "Тестировщики" }, { id: 3, name: "Бета" }];

  info = { ok: true, available: false };
  await api.loadAccessControl();
  assert(box.hidden && api.accessChoice() === null, "не администратор — выбора нет, доступ не трогаем");

  info = { ok: true, available: true, restricted: false, groups: [], all_groups: groups };
  await api.loadAccessControl();
  assert(!box.hidden && calls.at(-1) === "cars/Haval/Jolion", "выбор показан для открытой модели");
  assert(select.children.map((o) => o.value).join() === "all,admins,group:1,group:3" && select.value === "all", "варианты");
  assert(api.accessChoice() === null, "не меняли — null");
  select.value = "group:3";
  assert(JSON.stringify(api.accessChoice()) === JSON.stringify({ restricted: true, groups: [3] }), "скрыть для группы");

  info = { ok: true, available: true, restricted: true, groups: [1], all_groups: groups };
  await api.loadAccessControl();
  assert(select.value === "group:1", "текущая группа выбрана");
  select.value = "all";
  assert(JSON.stringify(api.accessChoice()) === JSON.stringify({ restricted: false, groups: [] }), "открыть всем");

  info = { ok: true, available: true, restricted: true, groups: [1, 3], all_groups: groups };
  await api.loadAccessControl();
  assert(select.value === "multi" && select.children.at(-1).text === "Группы: Тестировщики, Бета", "несколько групп");
  assert(api.accessChoice() === null, "несколько групп, не меняли — null");
  select.value = "admins";
  assert(JSON.stringify(api.accessChoice()) === JSON.stringify({ restricted: true, groups: [] }), "только администраторы");

  ctx.isEditing = false;
  info = { ok: true, available: true, all_groups: groups };
  await api.loadAccessControl();
  assert(calls.at(-1) === null && select.value === "all", "новая модель — по умолчанию видна всем");

  ctx.isPendingModel = true;
  await api.loadAccessControl();
  assert(box.hidden && api.accessChoice() === null, "заявка клиента — без выбора");
};
