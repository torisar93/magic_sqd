// Общая библиотека APK: приложения, которые админ скрыл на модели (<файл>.json "hidden_models", владелец
// 2026-09-25), мастер на этой модели не показывает — на ПК и на Android одной функцией из общего apps_tabs.js
// (AppTabs.forModel). Путь модели — «Марка/Модель[/Модификация]», как в списке галочек админки.
"use strict";
const { read, assert, run } = require("./_util");

module.exports = async function () {
  for (const file of ["app/web/frontend/js/apps_tabs.js", "android/app/src/main/assets/js/apps_tabs.js"]) {
    const ctx = run(read(file), { window: {}, document: {} });
    const forModel = ctx.window.AppTabs.forModel;
    const gsplit = { path: "/apk/gsplit.apk", hidden_models: ["Geely/Cityray/после 2026 (без Wi-Fi, Monji)", "Haval/Jolion"] };
    const yandex = { path: "/apk/yandex.apk", hidden_models: [] };
    const old = { path: "/apk/old.apk" };  // сайдкар без поля — видно везде
    const all = [gsplit, yandex, old];

    const monji = { brand: "Geely", name: "Cityray", modification: "после 2026 (без Wi-Fi, Monji)" };
    const wifi = { brand: "Geely", name: "Cityray", modification: "со значком Wi-Fi (до 2026)" };
    const jolion = { brand: "Haval", name: "Jolion", modification: null };  // модель без модификаций

    assert(forModel(all, monji).map((a) => a.path).join() === "/apk/yandex.apk,/apk/old.apk", `${file}: скрыт на Monji`);
    assert(forModel(all, wifi).length === 3, `${file}: другая модификация той же модели — видно`);
    assert(forModel(all, jolion).map((a) => a.path).join() === "/apk/yandex.apk,/apk/old.apk", `${file}: модель без модификаций`);
    assert(forModel(all, null) === all, `${file}: модель не выбрана — список как есть`);
  }
};
