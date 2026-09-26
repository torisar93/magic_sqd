// ПК: в списке «Выберите приложение» ничего не выбрано заранее (разбор логов 2026-09-26). Первым в списке
// пакетов стоит «android» — ядро системы; он был выбран сам, и техник, нажав «OK», отключил его (лог #1013:
// «Отключаю приложение: android … Готово.») или пытался удалить (№1042, №757).
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "app/web/frontend/js/screens/stage_wizard.js";
  const src = read(file);
  const ctx = run(slice(src, "  function isPackageList(", "  function showAskInputDialog(", file)
    + "\nthis.isPackageList = isPackageList;", {});

  assert(ctx.isPackageList(["android", "android.ext.services", "com.android.systemui", "ru.yandex.music"]),
    "список пакетов — с пустым пунктом");
  assert(!ctx.isPackageList(["192.168.1.5", "192.168.1.7"]), "IPv4 из скана — первый выбран, как раньше");
  assert(!ctx.isPackageList(["fe80::1%wlan0"]), "IPv6 — как раньше");
  assert(!ctx.isPackageList(["COM3", "COM4"]), "COM-порты — как раньше");

  const show = slice(src, "  function showAskInputDialog(", "  // Закрыть окно этапа с итогом", file);
  assert(show.includes('select.value = packages ? "" : choices[0];'), "для пакетов ничего не выбрано заранее");
  assert(show.includes("placeholder.disabled = true;"), "пустой пункт нельзя выбрать вручную");
  assert(src.includes('if (usingChoices && select.value === "") { select.focus(); return; }'),
    "«OK» без выбора не отправляет пустой ответ");
};
