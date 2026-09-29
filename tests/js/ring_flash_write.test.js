// Кольцо записи на флешку (progress08.js, общий для ПК и Android): запись — своя фаза «Запись на флешку» с именем
// файла и номером «файл N из M», докачка перед записью подписана «Скачиваем с сервера». Владелец (2026-09-29): над ходом
// записи стояло «Передача приложения» (запись шла фазой transfer), а под кольцом — «Ожидаем ответ устройства».
// Кто шлёт фазу write — usb_api.py (_progress_apk) и WebBridge.kt (usbRunStage).
"use strict";
const { read, assert, run } = require("./_util");
const { makeDom } = require("./_ring_dom");

const MB = 1048576;

module.exports = async function () {
  for (const file of ["app/web/frontend/js/progress08.js", "android/app/src/main/assets/js/progress08.js"]) {
    const { document, LabUI, body } = makeDom();
    run(read(file), { window: {}, document, LabUI, Number, Math, String, Map });
    const root = document.createElement("div");
    root.dataset.stageIndex = "0";
    body.append(root);

    // 1) Свежая установка: списка файлов нет (они ещё не скачаны) — очередь пуста, ход идёт событиями без строки.
    let box = LabUI.busy(root, "Запись на флешку", []);
    const title = () => box.querySelector("h2").textContent;
    const hint = () => box.querySelector(".install-phase-detail").textContent;
    const line = () => box.querySelector(".run-event").textContent;
    const event = (e) => LabUI.progress({ stage_index: 0, path: "", completed: 0, total: 0, state: "running", ...e });

    event({ phase: "download", determinate: true, bytes_done: 172 * MB, bytes_total: 465 * MB });
    assert(title() === "Скачивание файлов" && hint() === "172 из 465 МБ", `${file}: докачка — ${title()} / ${hint()}`);
    assert(line() === "Скачиваем с сервера", `${file}: под кольцом при докачке — ${line()}`);
    // Общая папка считается файлами, а не байтами: процент без «МБ», подпись та же.
    event({ phase: "download", determinate: true, percent: 50 });
    assert(box.querySelector(".install-count").textContent === "50%" && line() === "Скачиваем с сервера",
      `${file}: докачка по числу файлов — ${line()}`);

    event({ phase: "write", determinate: true, bytes_done: 90 * MB, bytes_total: 212 * MB,
      path: "C:\\MagicSQD\\apk\\com.google.android.webview.apk", completed: 3, total: 19 });
    assert(title() === "Запись на флешку", `${file}: заголовок записи — ${title()}`);
    assert(hint() === "90 из 212 МБ", `${file}: байты записи — ${hint()}`);
    assert(line() === "com.google.android.webview.apk · файл 4 из 19", `${file}: текущий файл (Windows-путь) — ${line()}`);
    event({ phase: "write", determinate: true, bytes_done: 1, bytes_total: 1, path: "/storage/emulated/0/usb/update.bin",
      completed: 19, total: 19, state: "done" });
    assert(line() === "update.bin · файл 19 из 19", `${file}: последний файл готов — ${line()}`);

    // 2) Список файлов известен заранее — строка файла в очереди: «Запись…», итог — «Все файлы записаны».
    box = LabUI.busy(root, "Запись на флешку", [{ name: "update.bin", path: "/f/update.bin" }]);
    const row = () => box.querySelector(".run-queue li");
    event({ phase: "write", determinate: true, bytes_done: 5, bytes_total: 10, path: "/f/update.bin", total: 1 });
    assert(row().dataset.state === "running" && row().querySelector("small").textContent === "Запись…",
      `${file}: строка файла — ${row().querySelector("small").textContent}`);
    assert(line() === "update.bin", `${file}: под кольцом — имя из очереди, ${line()}`);
    event({ phase: "write", determinate: true, bytes_done: 10, bytes_total: 10, path: "/f/update.bin", completed: 1,
      total: 1, state: "done" });
    assert(title() === "Готово" && hint() === "Все файлы записаны", `${file}: итог записи — ${title()} / ${hint()}`);
  }

  // 3) Настоящее окно этапа (stage_run.js) без списка файлов — так Android пишет флешку после свежей установки.
  //    Окно убирало поле процента, кольцо падало на каждом событии: всю докачку и запись ни процента, ни «X из Y МБ»,
  //    под кольцом — «Не отключайте флешку…» (Belgee S50, лог №1718, 29.09: «просто крутилось»).
  for (const [ring, stageRun] of [["app/web/frontend/js/progress08.js", "app/web/frontend/js/components/stage_run.js"],
    ["android/app/src/main/assets/js/progress08.js", "android/app/src/main/assets/js/stage_run.js"]]) {
    const { document, LabUI, body } = makeDom();
    const window = { LabUI };
    run(read(ring) + "\n" + read(stageRun), { window, document, LabUI, Number, Math, String, Map, navigator: {}, setTimeout });
    window.StageRun.open({ title: "Запись файлов на флешку", stageIndex: 2, icon: "usb", items: [],
      detail: "Не отключайте флешку до завершения записи." });
    const box = body.querySelector(".progress08");
    const count = () => box.querySelector(".install-count");
    const hint = () => box.querySelector(".install-phase-detail").textContent;
    const line = () => box.querySelector(".run-event").textContent;
    assert(count() && count().textContent === "", `${stageRun}: поле процента на месте и пустое (пустое скрыто стилем)`);
    LabUI.progress({ stage_index: 2, path: "", completed: 0, total: 0, state: "running", phase: "download",
      determinate: true, bytes_done: 84 * MB, bytes_total: 443 * MB });
    assert(count().textContent === "18%" && hint() === "84 из 443 МБ" && line() === "Скачиваем с сервера",
      `${stageRun}: докачка — ${count().textContent} / ${hint()} / ${line()}`);
    LabUI.progress({ stage_index: 2, path: "/data/user/0/ru.magicsqd.mobile/files/cars/_shared/freetuga/magic_sqd/" +
      "for_install/com.google.android.webview.apk", completed: 3, total: 19, state: "running", phase: "write",
      determinate: true, bytes_done: 90 * MB, bytes_total: 212 * MB });
    assert(box.querySelector("h2").textContent === "Запись на флешку" && count().textContent === "42%"
      && hint() === "90 из 212 МБ" && line() === "com.google.android.webview.apk · файл 4 из 19",
      `${stageRun}: запись — ${count().textContent} / ${hint()} / ${line()}`);
  }

  // 4) Поля процента нет (окно этапа до 1.0.50) — кольцо не падает, подписи обновляются.
  for (const file of ["app/web/frontend/js/progress08.js", "android/app/src/main/assets/js/progress08.js"]) {
    const { document, LabUI, body } = makeDom();
    run(read(file), { window: {}, document, LabUI, Number, Math, String, Map });
    const root = document.createElement("div");
    root.dataset.stageIndex = "0";
    body.append(root);
    const box = LabUI.busy(root, "Запись файлов на флешку", []);
    box.querySelector(".install-count").remove();
    LabUI.progress({ stage_index: 0, path: "", completed: 0, total: 0, state: "running", phase: "download",
      determinate: true, bytes_done: 1 * MB, bytes_total: 4 * MB });
    assert(box.querySelector(".install-phase-detail").textContent === "1 из 4 МБ"
      && box.querySelector(".run-event").textContent === "Скачиваем с сервера", `${file}: без поля процента`);
  }
};
