// Android: окно подключения по Wi-Fi подставляет адрес прошлой магнитолы, только если он из сети, в которой сейчас
// телефон (владелец, 2026-09-27). Техники переходили к следующей машине, не закрывая программу, жали «Подключиться»
// на адрес прошлой магнитолы и ждали таймаут (8 логов, №1226, №1339).
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "android/app/src/main/assets/js/app.js";
  const src = read(file);
  const code = slice(src, "  function lastHostForThisNetwork(", "  function promptHostPicker(", file);
  const make = (lastWifiHost, answer) => run(
    `let lastWifiHost = ${JSON.stringify(lastWifiHost)};\n` + code + "\nthis.pick = lastHostForThisNetwork;",
    { Bridge: { call: (method, args) => {
      if (method !== "wifi_host_in_subnet") throw new Error("не тот вызов: " + method);
      if (answer instanceof Error) throw answer;
      return { inSubnet: answer, host: args.host };
    } } });

  assert(make("192.168.223.69", false).pick() === "", "адрес из другой сети не подставляется");
  assert(make("192.168.26.14", true).pick() === "192.168.26.14", "адрес из той же сети подставляется");
  assert(make("fe80::1%wlan0", null).pick() === "fe80::1%wlan0", "сеть не понять — подставляем, как раньше");
  assert(make("192.168.1.5", new Error("нет моста")).pick() === "192.168.1.5", "мост не ответил — как раньше");
  assert(make("", false).pick() === "", "адреса не было — пусто");
  assert(src.includes("host:lastHostForThisNetwork(),"), "окно подключения берёт адрес через проверку сети");
};
