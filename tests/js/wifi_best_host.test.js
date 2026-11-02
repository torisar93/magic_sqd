// Окно «Подключение по Wi-Fi»: самый вероятный адрес магнитолы — первым, с подписью и причиной (владелец 02.11.2026:
// «просто показывает все что в сети и не подписывает что вот этот самый наиболее вероятный»). С переходом на новый
// интерфейс (0.9.9) Android перестал выделять android.local, а окно ПК не знало причин. LabUI.rankHosts — общий для
// ПК и Android (refinement05.js), обе копии одинаковые.
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const desktop = read("app/web/frontend/js/refinement05.js");
  assert(desktop === read("android/app/src/main/assets/js/refinement05.js"), "refinement05.js на ПК и Android одинаковые");
  const code = slice(desktop, "  const HOST_REASONS=", "  LabUI.connection=", "refinement05.js");
  const ctx = run(code + "\nthis.rank = LabUI.rankHosts;", { LabUI: {} });
  const rank = (list) => JSON.parse(JSON.stringify(ctx.rank(list)));

  // Ничего не известно — порядок как пришёл, без рекомендаций.
  let items = rank(["192.168.1.5", "192.168.1.20"]);
  assert(items.map((x) => x.host).join() === "192.168.1.5,192.168.1.20" && !items.some((x) => x.recommended), "без признаков");

  // Android до 1.1.19: android.local в списке дважды (recommended + found) — одна строка, первая, с причиной.
  items = rank([{ host: "192.168.43.7", reason: "mdns" }, "192.168.43.2", "192.168.43.7"]);
  assert(items.length === 2 && items[0].host === "192.168.43.7" && items[0].recommended, "android.local первым");
  assert(items[0].why === "откликается как android.local", "причина: " + items[0].why);

  // Открытый порт важнее шлюза; адрес с двумя признаками — выше адреса с одним того же веса.
  items = rank(["10.0.0.9", { host: "10.0.0.1", reason: "gateway" }, { host: "10.0.0.9", reason: "open" },
    { host: "10.0.0.4", reason: "open" }, { host: "10.0.0.4", reason: "gateway" }]);
  assert(items.map((x) => x.host).join() === "10.0.0.4,10.0.0.9,10.0.0.1", "порядок: " + items.map((x) => x.host));
  assert(items[0].why === "открыт порт ADB, раздаёт эту сеть Wi-Fi", "две причины: " + items[0].why);

  // ПК до 1.1.19: recommended без причины; с портом — «Беспроводная отладка», порт сохраняется.
  items = rank(["192.168.0.10", { host: "192.168.0.33", port: 37123, recommended: true }, { host: "192.168.0.50", recommended: true }]);
  assert(items[0].host === "192.168.0.33" && items[0].port === 37123 && items[0].why === "служба «Беспроводная отладка»", "ADB-служба первой");
  assert(items[1].host === "192.168.0.50" && items[1].why === "откликается как android.local", "android.local вторым");
  assert(!items[2].recommended, "остальные — без рекомендации");

  // Окно: лучший адрес — с подписью «Скорее всего магнитола» и сразу в поле, если оно пустое.
  const conn = slice(desktop, "      try{const items=LabUI.rankHosts(await scan(p));", "      catch{", "refinement05.js");
  assert(conn.includes("'Скорее всего магнитола'") && conn.includes("if(item===best&&!hostInput.value.trim())"), "подпись и подстановка");

  // Android передаёт окну причины: android.local, шлюз, открытый порт, службу «Беспроводная отладка».
  const app = read("android/app/src/main/assets/js/app.js");
  const picker = slice(app, "  function promptHostPicker(", "\n  }\n", "app.js");
  for (const reason of ["reason:'mdns'", "reason:'gateway'", "reason:'open'", "reason:'adb'"]) {
    assert(picker.includes(reason), "Android передаёт " + reason);
  }
  assert(app.includes("{ open: event.open || [], gateway: event.gateway || null }"), "Android читает open/gateway");
};
