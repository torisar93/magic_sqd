// ПК: подключение по Wi-Fi — на всю модель, а не на один этап (разбор логов 09.11.2026, мак 1.1.22, №14575: подключились
// на «Приложениях», на «Доп. действиях» — «Магнитола не подключена», хотя adb видит её «device»). Бар следующего этапа
// берёт подключение, если adb всё ещё видит магнитолу «device»; «offline» — нет.
"use strict";
const { read, slice, assert, run, sleep } = require("./_util");

function fakeEl(tag, attrs = {}, children = []) {
  const node = {
    tag, attrs, children: [], listeners: {}, textContent: attrs.text || "", value: "", options: [], className: attrs.class || "",
    appendChild(child) { this.children.push(child); if (tag === "select") this.options.push(child); return child; },
    append(...kids) { kids.forEach((k) => this.appendChild(k)); },
    addEventListener(kind, fn) { (this.listeners[kind] = this.listeners[kind] || []).push(fn); },
    click() { (this.listeners.click || []).forEach((fn) => fn()); },
  };
  [].concat(children).forEach((c) => c && node.appendChild(c));
  return node;
}

module.exports = async function () {
  const file = "app/web/frontend/js/screens/stage_wizard.js";
  const src = read(file);
  const code = "let modelWifiPort = 5555; let modelWifiSerial = null;\n"
    + slice(src, "  function buildTransportBar(", "  function buildInstructionBlock(", file)
    + "\nthis.buildTransportBar = buildTransportBar; this.setModelWifi = (s) => { modelWifiSerial = s; };"
    + " this.modelWifi = () => modelWifiSerial;";
  let devices = [];
  let connectCb = null;
  const ctx = run(code, {
    el: fakeEl, clear: (n) => { n.children = []; n.options = []; },
    noteDeviceFingerprint: () => {},
    LabUI: { connection: (opts) => { connectCb = opts.connect; } },
    window: { pywebview: { api: {
      install_list_devices: async () => devices,
      install_wifi_connect: async (port, ip) => ({ ok: true, ip }),
    } } },
    console,
  });

  // Этап «Приложения» по Wi-Fi: подключились — запомнено на модель.
  const apps = ctx.buildTransportBar({ type: "apps", apps_connection: "wifi" });
  assert(apps.getDevice() === null, "до подключения устройства нет");
  apps.wifiButton.click();
  await connectCb("192.168.1.5", 5555);
  assert(apps.getDevice() === "192.168.1.5:5555", "подключились на этапе приложений");
  assert(ctx.modelWifi() === "192.168.1.5:5555", "подключение запомнено на модель");

  // «Доп. действия» по Wi-Fi: adb видит магнитолу «device» — берём её сами, окна «не подключена» нет.
  devices = [{ serial: "192.168.1.5:5555", state: "device" }];
  const actions = ctx.buildTransportBar({ type: "actions", actions_connection: "wifi" });
  await sleep(0);
  assert(actions.getDevice() === "192.168.1.5:5555", "следующий этап взял подключение");
  assert(actions.wifiConnected(), "статус «подключено»");

  // adb видит её «offline» — не берём (будет окно и переподключение).
  devices = [{ serial: "192.168.1.5:5555", state: "offline" }];
  const later = ctx.buildTransportBar({ type: "actions", actions_connection: "wifi" });
  await sleep(0);
  assert(later.getDevice() === null, "offline не берём");
  assert(await later.adoptWifi() === null, "и по запросу findDevice — тоже");
  devices = [{ serial: "192.168.1.5:5555", state: "device" }];
  assert(await later.adoptWifi() === "192.168.1.5:5555", "findDevice берёт её, когда она снова «device»");

  // Не подключилось — забыто и на модель.
  ctx.setModelWifi("192.168.1.5:5555");
  const failing = ctx.buildTransportBar({ type: "apps", apps_connection: "wifi" });
  ctx.window = ctx.window || {};
  failing.wifiButton.click();
  const api = { install_wifi_connect: async () => ({ ok: false }) };
  Object.assign(ctx.window.pywebview.api, api);
  await connectCb("192.168.1.9", 5555);
  assert(ctx.modelWifi() === null, "неудачное подключение сбрасывает запомненное");

  assert(src.includes("const device = await transport.adoptWifi();"), "findDevice в режиме Wi-Fi спрашивает adoptWifi");
  assert(/modelWifiPort = 5555;\n    modelWifiSerial = null;/.test(src), "новая модель — подключение забыто");
};
