// ПК, этап «Флешка»/QR ADB: выбор флешки не теряется, пока она в магнитоле, а единственную флешку программа
// выбирает сама. Лог #799 (Haval Jolion 2026, v1.0.38): после «вынули-вставили» выбор сбрасывался, и
// «Получить пароль» 13 раз подряд отвечало «Флешка не найдена», хотя флешка была в списке.
// (app/web/frontend/js/screens/stage_wizard.js: keepDrive, noDriveMessage)
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "app/web/frontend/js/screens/stage_wizard.js";
  const code = slice(read(file), "  function keepDrive(", "  // Блок пройден:", file);
  const api = run(code + "\nthis.__api = { keepDrive, noDriveMessage };", {}).__api;
  const E = { letter: "E:" }, F = { letter: "F:" };

  assert(api.keepDrive([E], "", false) === "E:", "единственная флешка выбирается сама");
  assert(api.keepDrive([], "E:", false) === "", "флешка в магнитоле — в списке пусто");
  assert(api.keepDrive([E], "E:", false) === "E:", "вернули ту же флешку — снова выбрана без клика");
  assert(api.keepDrive([E, F], "F:", false) === "F:", "из нескольких — та, что выбрал техник");
  assert(api.keepDrive([E, F], "", false) === "", "из нескольких сами не выбираем");
  assert(api.keepDrive([E], "", true) === "", "«Показать все локальные диски» — сами не выбираем");

  assert(/Подключите флешку/.test(api.noDriveMessage([])), "флешки нет — «Подключите флешку»");
  assert(/Выберите флешку в списке/.test(api.noDriveMessage([E, F])), "флешки есть, не выбрана — «Выберите флешку»");
};
