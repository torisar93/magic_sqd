// ПК: итог записи на флешку в журнале сессии — с причиной неудачи (разбор логов 2026-09-25: в №804 и №913
// была только «Флешка: запись не удалась — …», причину техник видел в окне, а в лог она не попадала;
// прежний usb-этап не писал в журнал вообще ничего).
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "app/web/frontend/js/screens/stage_wizard.js";
  const src = read(file);
  const ctx = run(slice(src, "  function flashResultLine(", "  // Та же полоса выбора флешки", file)
    + "\nthis.line = flashResultLine;", {});

  const ok = ctx.line("svlog.flag", true, { message: "Копирование на флешку завершено.", cancelled: false });
  assert(ok === "Флешка: записано — svlog.flag.", "успех: " + ok);
  const failed = ctx.line("svlog.flag", false, { message: "Флешка E: не найдена.", cancelled: false });
  assert(failed === "Флешка: запись не удалась — svlog.flag: Флешка E: не найдена.", "причина в строке: " + failed);
  const bare = ctx.line("выбранные приложения", false, { message: "", cancelled: false });
  assert(bare === "Флешка: запись не удалась — выбранные приложения.", "без причины: " + bare);
  const stopped = ctx.line("файлы этапа", false, { message: "Остановлено пользователем.", cancelled: true });
  assert(stopped === "Флешка: запись остановлена пользователем — файлы этапа.", "остановка — не «не удалась»: " + stopped);
  const legacy = ctx.line("файлы этапа", false, undefined);
  assert(legacy === "Флешка: запись не удалась — файлы этапа.", "вызов без итога окна: " + legacy);

  // И блок этапа «Флешка», и прежний usb-этап пишут итог через одну функцию.
  assert((src.match(/log\(flashResultLine\(/g) || []).length === 2, "обе записи на флешку пишут итог в журнал");
};
