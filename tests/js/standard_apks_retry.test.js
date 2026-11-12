// ПК: список приложений модели — один повтор при сбое, потом пустой список и строка в журнале, а не необработанная
// ошибка в окне («TimeoutError: The read operation timed out», ПК 1.1.21, №15424; разбор 12.11.2026).
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "app/web/frontend/js/screens/stage_wizard.js";
  const src = read(file);
  let calls = 0, fail = 2;
  const logs = [];
  const ctx = run(slice(src, "  async function standardApks(", "  async function initAppSelectionDefaults(", file)
    + "\nthis.standardApks = standardApks;", {
    log: (m) => logs.push(m),
    setTimeout: (fn) => fn(),
    window: { pywebview: { api: { install_standard_apks: async () => {
      calls++;
      if (calls <= fail) throw new Error("The read operation timed out");
      return { required: [{ path: "a.apk" }], optional: [] };
    } } } },
  });
  fail = 1;
  let result = await ctx.standardApks("Haval/H3", 1, null);
  assert(calls === 2 && result.required.length === 1 && !logs.length, "второй раз ответил — список есть, журнал чист");
  calls = 0; fail = 5;
  result = await ctx.standardApks("Haval/H3", 1, null);
  assert(calls === 2 && result.required.length === 0 && /не загрузился .*проверьте интернет/.test(logs[0]),
    "два сбоя — пустой список и понятная строка: " + logs[0]);
  assert(!/await window\.pywebview\.api\.install_standard_apks\(model/.test(src), "все вызовы — через standardApks");
};
