// Группа Magic SQD в Telegram (владелец 30.10.2026: «добавь ссылки на мою телеграм группу @magicsqd»): экран каталога,
// настройки/меню и приветственное окно — на ПК и Android. На ПК ссылки панели каталога прячет ui-lab.css, Telegram
// должен остаться видимым.
"use strict";
const { read, slice, assert } = require("./_util");

const URL = 'href="https://t.me/magicsqd"';

module.exports = async function () {
  const picker = read("app/web/frontend/js/screens/main_picker.js");
  assert(slice(picker, '<nav class="cat-top-actions"', "</nav>", "main_picker.js").includes(URL), "ПК: панель каталога");
  const css = read("app/web/frontend/css/components.css");
  assert(css.includes(".cat-top-actions > a.catalog-topbar-telegram { display: inline-flex;"), "ПК: ссылка не спрятана");
  assert(read("app/web/frontend/js/components/settings.js").includes(URL), "ПК: окно «Настройки»");
  const desktopWelcome = read("app/web/frontend/js/components/boosty.js");
  assert(desktopWelcome.includes("welcomeLinksEl.appendChild(boostyLinksRow({ telegram: true }))"), "ПК: приветственное окно");
  assert(desktopWelcome.includes('href: "https://t.me/magicsqd"'), "ПК: ссылка в строке Boosty");

  const index = read("android/app/src/main/assets/index.html");
  assert(slice(index, '<div class="catalog-actions"', "</div>", "index.html").includes(URL), "Android: каталог");
  const app = read("android/app/src/main/assets/js/app.js");
  const welcome = slice(app, "  function maybeShowWelcomeModal() {", "  }\n", "app.js");
  assert(welcome.includes("boostyLinksRow({ telegram: true })"), "Android: приветственное окно");
  assert(app.includes("reportIssue, copyLog, telegram, github]"), "Android: меню");

  for (const base of ["app/web/frontend/js/", "android/app/src/main/assets/js/"]) {
    assert(read(base + "i18n_en.js").includes('"Группа в Telegram": "Telegram group"'), base + ": перевод");
  }
};
