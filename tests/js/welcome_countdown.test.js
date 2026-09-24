// Окно при запуске (владелец, 2026-09-25): предупреждение «программа — для людей с техническими знаниями,
// всё на свой страх и риск», а «Понятно» — только после отсчёта 3, 2, 1. Функция countdownButton одинаковая
// на ПК (components/boosty.js) и Android (assets/js/app.js); плюс предупреждение есть в обоих окнах.
"use strict";
const { read, slice, assert, run } = require("./_util");

const DISCLAIMER = "Программа предназначена для людей с техническими знаниями о работе Android-магнитол. " +
  "Все действия вы выполняете на свой страх и риск.";

module.exports = async function () {
  for (const file of ["app/web/frontend/js/components/boosty.js", "android/app/src/main/assets/js/app.js"]) {
    const src = read(file);
    const timers = [];
    const ctx = run(slice(src, "  function countdownButton(", "\n  }\n", file) + "\n  }\nthis.countdown = countdownButton;", {
      setInterval: (fn, ms) => { timers.push({ fn, ms, cleared: false }); return timers.length; },
      clearInterval: (id) => { timers[id - 1].cleared = true; },
    });
    let focused = false;
    const button = { disabled: false, textContent: "Понятно", focus: () => { focused = true; } };
    ctx.countdown(button, "Понятно", 3);
    const seen = [[button.textContent, button.disabled]];
    for (let tick = 0; tick < 3; tick++) { timers[0].fn(); seen.push([button.textContent, button.disabled]); }
    assert(timers[0].ms === 1000, `${file}: раз в секунду`);
    assert(JSON.stringify(seen) === JSON.stringify([["3", true], ["2", true], ["1", true], ["Понятно", false]]),
      `${file}: 3, 2, 1 и только потом «Понятно» — ${JSON.stringify(seen)}`);
    assert(timers[0].cleared && focused, `${file}: таймер остановлен, фокус на кнопке`);
  }
  const html = read("app/web/frontend/index.html");
  const welcome = html.slice(html.indexOf('<dialog id="welcome-dialog"'), html.indexOf("</dialog>", html.indexOf('<dialog id="welcome-dialog"')));
  assert(welcome.includes(DISCLAIMER), "ПК: предупреждение в окне при запуске");
  const app = read("android/app/src/main/assets/js/app.js");
  const modal = slice(app, "  function maybeShowWelcomeModal() {", "\n  }\n", "app.js");
  assert(modal.includes(DISCLAIMER) && modal.includes('countdownButton(okButton, "Понятно", 3)') && modal.includes("dismissible: false"),
    "Android: предупреждение, отсчёт и окно без закрытия тапом мимо");
  const boosty = read("app/web/frontend/js/components/boosty.js");
  assert(boosty.includes('countdownButton(welcomeCloseButton, "Понятно", 3)') && boosty.includes("if (welcomeCloseButton.disabled) event.preventDefault()"),
    "ПК: отсчёт и Esc не закрывает окно, пока идёт отсчёт");
};
