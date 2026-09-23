// ПК, редактор → «Сохранить»: публикация на сервер идёт в фоне и с APK длится минутами. Владелец
// (2026-09-23) посмотрел сервер посреди загрузки и решил, что файлы не дошли: редактор закрывался сразу,
// итог публикации был только строкой в журнале при общем «Готово.». Проверяем обвязку в
// screens/graph_wizard.js (init): окно «Сохранение модели» показывает этапы и процент отправки
// (car_save_log/car_save_progress), а car_save_finished закрывает его и явно сообщает итог.
"use strict";
const { read, slice, assert, run } = require("./_util");

function mkEvents() {
  const handlers = {};
  return {
    on(kind, h) { (handlers[kind] = handlers[kind] || []).push(h); },
    emit(kind, e) { (handlers[kind] || []).forEach((h) => h(e)); },
  };
}

module.exports = async function () {
  const file = "app/web/frontend/js/screens/graph_wizard.js";
  const code = slice(read(file), '    window.events.on("car_save_log", (event) => {', "  // -- вход в админку перед первым", file);
  const notices = [], logs = [], sets = [];
  let closed = 0;
  const ctx = {
    window: { events: mkEvents(), notice: (text, opts = {}) => notices.push({ text, ...opts }) },
    logFn: (text) => logs.push(text), setMainProgressVisible() {}, onCreatedCb: null, saveBusy: null,
  };
  const busy = () => ({ set: (percent, text) => sets.push([percent, text]), close: () => { closed++; } });
  run(code.replace(/\n  }\n\s*$/, "\n"), ctx); // срез заканчивается закрывающей скобкой init — она не нужна
  const { events } = ctx.window;

  // Успешная публикация админом: прогресс в окне, потом окно закрыто и явное «Опубликовано».
  ctx.saveBusy = busy();
  events.emit("car_save_log", { text: "Архивирую Haval/Jolion/2026..." });
  events.emit("car_save_progress", { done: 22 * 1024 * 1024, total: 44 * 1024 * 1024 });
  assert(sets[0][0] === null && sets[0][1] === "Архивирую Haval/Jolion/2026...", "этап виден в окне: " + JSON.stringify(sets[0]));
  assert(sets[1][0] === 50 && sets[1][1] === "Отправляем на сервер: 22.0 из 44.0 МБ", "процент отправки: " + JSON.stringify(sets[1]));
  events.emit("car_save_finished", { success: true, message: "Готово.", publish: { mode: "admin", ok: true, error: null } });
  assert(closed === 1 && ctx.saveBusy === null, "окно сохранения закрыто");
  assert(notices.length === 1 && notices[0].title === "Опубликовано" && !notices[0].danger, "итог публикации: " + JSON.stringify(notices));

  // Публикация не удалась (сессия истекла) — не «Готово.» молча, а явная ошибка.
  notices.length = 0;
  ctx.saveBusy = busy();
  events.emit("car_save_finished", { success: true, message: "Готово.",
    publish: { mode: "admin", ok: false, error: "Сессия входа истекла — войдите заново." } });
  assert(notices.length === 1 && notices[0].danger && notices[0].title === "Не опубликовано"
    && /не опубликована: Сессия входа истекла/.test(notices[0].text), "ошибка публикации видна: " + JSON.stringify(notices));

  // Техник (заявка на проверку) — своё сообщение.
  notices.length = 0;
  events.emit("car_save_finished", { success: true, message: "Готово.", publish: { mode: "submit", ok: true, error: null } });
  assert(notices.length === 1 && notices[0].title === "Отправлено", "заявка: " + JSON.stringify(notices));

  // Сохранение само не удалось — одно окно с причиной, без лишних про публикацию.
  notices.length = 0;
  events.emit("car_save_finished", { success: false, message: "Такая модель уже существует" });
  assert(notices.length === 1 && notices[0].title === "Не удалось сохранить", "ошибка сохранения: " + JSON.stringify(notices));

  // Правка заявки клиента (без публикации) — никаких окон.
  notices.length = 0;
  events.emit("car_save_finished", { success: true, message: "Сохранено." });
  assert(notices.length === 0, "локальное сохранение заявки — без окна");
};
