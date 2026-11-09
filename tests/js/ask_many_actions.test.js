// Галочки и для «Выдать разрешения», «Отключить/Включить приложение» (владелец, 09.11.2026: «можно такой же список
// сделать и для кнопок выдать права, вкл откл приложения»). ПК — по подписи кнопки (вопрос задаёт файл модели), ответ —
// JSON-список (adb_permissions понимает); Android — тот же список и обработка по очереди.
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "app/web/frontend/js/screens/stage_wizard.js";
  const src = read(file);
  const ctx = run(slice(src, "  const ASK_MANY_MARK", "  function showAskManyDialog(", file)
    + slice(src, "  function isPackageList(", "  // Выбор нескольких вариантов галочками", file)
    + "\nthis.isAskMany = isAskMany; this.ok = ASK_MANY_ACTIONS;", {});
  const pkgs = ["com.aimp.player", "ru.yandex.music"];
  for (const title of ["Выдать разрешения", "Выдать разрешения приложению", "Отключить приложение", "Включить приложение"]) {
    assert(ctx.isAskMany({ title, choices: pkgs }), "галочки: " + title);
  }
  assert(!ctx.isAskMany({ title: "Удалить приложение", choices: pkgs }), "удаление — по одному, как раньше");
  assert(!ctx.isAskMany({ title: "Запустить приложение", choices: pkgs }), "запуск — по одному");
  assert(!ctx.isAskMany({ title: "Выдать разрешения", choices: ["192.168.1.5"] }), "не список пакетов — обычный вопрос");
  assert(ctx.isAskMany({ title: "Режим стелс⁣", choices: pkgs }), "стелс — как раньше");
  assert(ctx.ok["Отключить приложение"] === "Отключить", "кнопка «Отключить (N)»");

  const android = read("android/app/src/main/assets/js/app.js");
  for (const kind of ["grant_permissions", "disable_app", "enable_app"]) {
    assert(new RegExp(`${kind}: \\{[^}]*many: "`).test(android), "Android: галочки у " + kind);
  }
  assert(!/uninstall_app: \{[^}]*many:/.test(android), "Android: удаление — по одному");
  const batch = slice(android, "  async function runPackageBatch(", "  function renderActionsStage(", "app.js");
  assert(batch.includes("Bridge.call(method, { pkg: packages[i] })") && batch.includes("operationBusy()"),
    "по очереди: следующее — когда Kotlin закончил предыдущее");
};
