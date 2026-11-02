// Ранний доступ (владелец 02.11.2026): «подписать в программе что ранний доступ на машинах он для подписчиков бусти и
// тестеров так как способ полноценно не проверен и потенциально опасен, тестером можно стать бесплатно если написать в
// телеграм». Текст окна — общий catalog-ui.js (ПК и Android одинаковые), кнопка Telegram — в окне обеих платформ.
"use strict";
const { read, slice, assert } = require("./_util");

module.exports = async function () {
  const shared = read("app/web/frontend/js/catalog-ui.js");
  assert(shared === read("android/app/src/main/assets/js/catalog-ui.js"), "catalog-ui.js на ПК и Android одинаковые");
  const text = slice(shared, "  function earlyAccessParagraphs(", "\n", "catalog-ui.js");
  for (const part of ["не проверен полностью и потенциально опасен", "подписчики Boosty и тестировщики",
    "Стать тестировщиком можно бесплатно — напишите нам в Telegram."]) {
    assert(text.includes(part), "окно раннего доступа: " + part);
  }
  assert(shared.includes("'для подписчиков и тестировщиков'"), "кнопка закрытой модели — для подписчиков и тестировщиков");

  const pc = read("app/web/frontend/js/components/boosty.js");
  const pcDialog = slice(pc, "  function showEarlyAccessDialog(", "\n  }\n", "boosty.js");
  assert(pcDialog.includes('telegram: true, telegramLabel: "Написать в Telegram"'), "ПК: кнопка «Написать в Telegram»");

  const app = read("android/app/src/main/assets/js/app.js");
  const anDialog = slice(app, "  function showEarlyAccessModal(", "\n  }\n", "app.js");
  assert(anDialog.includes("...paragraphs"), "Android: в окне все абзацы, и про тестировщиков тоже");
  assert(anDialog.includes('telegram: true, telegramLabel: "Написать в Telegram"'), "Android: кнопка «Написать в Telegram»");

  // Замок «Скачать заранее» и закрытые этапы — по-прежнему только «Подписаться на Boosty».
  assert(pc.includes('replaceChildren(boostyLinksRow({ subscribeOnly: true, ...links }))'), "ПК: ссылки окна замка");
  assert(!slice(pc, "  function showOfflineDialog(", "\n  }\n", "boosty.js").includes("telegram"), "«Скачать заранее» без Telegram");

  const en = read("app/web/frontend/js/i18n_en.js");
  for (const key of ["для подписчиков и тестировщиков", "Стать тестировщиком можно бесплатно — напишите нам в Telegram.",
    "Написать в Telegram"]) {
    assert(en.includes(`"${key}":`), "перевод: " + key);
  }
};
