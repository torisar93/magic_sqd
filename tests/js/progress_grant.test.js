// Кольцо окна установки (progress08.js, общий для ПК и Android): выдача разрешений — своя фаза «Выдача
// разрешений» с ходом «Разрешение N из M», строка приложения — «Разрешения…», «Готово» — только после неё.
// Владелец (2026-09-28): скачивание и установка идут с анимацией, а выдача разрешений — без неё, непонятно,
// зависла ли программа. Раньше на последнем приложении кольцо закрывалось галочкой до выдачи.
"use strict";
const { read, assert, run } = require("./_util");
const { makeDom } = require("./_ring_dom");

module.exports = async function () {
  for (const file of ["app/web/frontend/js/progress08.js", "android/app/src/main/assets/js/progress08.js"]) {
    const { document, LabUI, body } = makeDom();
    run(read(file), { window: {}, document, LabUI, Number, Math, String, Map });

    const root = document.createElement("div");
    root.dataset.stageIndex = "3";
    body.append(root);
    const box = LabUI.busy(root, "Установка приложений", [{ name: "Музыка", path: "/apk/music.apk" }]);
    const title = () => box.querySelector("h2").textContent;
    const hint = () => box.querySelector(".install-phase-detail").textContent;
    const row = () => box.querySelector(".run-queue li");
    const event = (e) => LabUI.progress({ stage_index: 3, path: "/apk/music.apk", completed: 0, total: 1, ...e });

    event({ state: "running", phase: "install", determinate: false });
    assert(title() === "Установка приложения", `${file}: фаза install — ${title()}`);

    // Выдача началась — число шагов ещё неизвестно (dumpsys): крутится, но подписано, что идёт.
    event({ state: "running", phase: "grant", determinate: false });
    assert(title() === "Выдача разрешений", `${file}: заголовок фазы grant — ${title()}`);
    assert(box.dataset.determinate === "false", `${file}: без числа шагов кольцо крутится`);
    assert(hint() === "Выдаём приложению разрешения", `${file}: подпись без шагов — ${hint()}`);
    assert(row().dataset.state === "running" && row().querySelector("small").textContent === "Разрешения…",
      `${file}: строка — ${row().querySelector("small").textContent}`);

    // Ход по шагам.
    event({ state: "running", phase: "grant", determinate: true, steps_done: 5, steps_total: 20 });
    assert(box.dataset.determinate === "true", `${file}: шаги — процент`);
    assert(box.querySelector(".install-count").textContent === "25%", `${file}: 25% — ${box.querySelector(".install-count").textContent}`);
    assert(box.querySelector(".run-progress").value === 25, `${file}: полоса 25`);
    assert(hint() === "Разрешение 5 из 20", `${file}: подпись шагов — ${hint()}`);
    assert(box.querySelector(".queue-summary").textContent === "Готово 0 из 1", `${file}: приложение ещё не готово`);

    // «Готово» — после разрешений: только тогда кольцо закрывается галочкой.
    event({ state: "done", completed: 1 });
    assert(row().dataset.state === "done" && title() === "Готово" && hint() === "Все приложения установлены",
      `${file}: итог — ${title()} / ${hint()}`);
  }
};
