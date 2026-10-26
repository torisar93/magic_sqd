// Android: «Выполнить» у доп. действия с выбором приложения, пока идёт другое действие (запуск, разрешения, снятие APK).
// Kotlin отказывал без ответа экрану — карточка висела «Получаем список приложений…» с неактивной кнопкой (18 раз в
// логах №9428–9955, 27.10.2026). Теперь JS спрашивает operation_busy до запроса списка (едет бандлом интерфейса), а
// Kotlin при отказе всё равно присылает actions_packages_result с busy (для гонки — с выпуском).
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "android/app/src/main/assets/js/app.js";
  const src = read(file);
  const click = slice(src, "        const pickerAction = PACKAGE_PICKER_ACTIONS[action.kind];", "        if (action.kind && action.kind !== \"command\"", file);
  assert(click.indexOf("if (operationBusy())") < click.indexOf('log("Получаю список приложений...")'),
    "занятость проверяется до запроса списка");
  assert(click.includes("if (busy) { note.textContent=busyText;log(busyText);return; }"), "ответ busy от Kotlin — тот же текст");

  const result = slice(src, "  function onActionsPackagesResult(event) {", "  // Результат \"Добавить свой APK...\"", file);
  const got = [];
  const ctx = { pendingPackagesCallback: (packages, busy) => got.push([packages, busy]) };
  run(result.replace(/pendingPackagesCallback/g, "this.pendingPackagesCallback") + "\nthis.on = onActionsPackagesResult;", ctx);
  ctx.on({ packages: [], busy: true });
  assert(JSON.stringify(got) === JSON.stringify([[[], true]]), JSON.stringify(got));

  const kotlin = read("android/app/src/main/java/ru/magicsqd/mobile/WebBridge.kt");
  const list = slice(kotlin, "    private fun actionsListPackages(thirdPartyOnly: Boolean) = runExclusive({", "    }) {", "WebBridge.kt");
  assert(list.includes('.put("busy", true)'), "Kotlin отвечает экрану и при занятой программе");

  const en = read("android/app/src/main/assets/js/i18n_en.js");
  assert(en.includes("Сейчас выполняется другое действие — дождитесь «Готово.» в журнале и нажмите «Выполнить» ещё раз."),
    "перевод текста");
};
