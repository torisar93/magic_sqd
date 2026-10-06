// Настройки и правила с сервера в интерфейсе (js/client_config.js + UserErrors.setRemote в user_errors.js; владелец,
// 2026-10-06: «чтобы обновлений приложения стало поменьше»): окно «что сделать» для новой ошибки и исправленный текст
// приходят с сервера; серверное правило — первым, с тем же id — вместо встроенного; битое правило не ломает окно.
"use strict";
const { read, assert, run } = require("./_util");

function load({ android = false, version = "1.0.62", config = {} } = {}) {
  const listeners = {};
  const window = {
    events: { on: (kind, fn) => { (listeners[kind] = listeners[kind] || []).push(fn); } },
    addEventListener: () => {},
  };
  if (android) {
    window.AndroidBridge = {};
    window.Bridge = {
      call: (method) => (method === "client_config" ? config : method === "app_version" ? { version } : null),
    };
  } else {
    window.appInfo = { app_version: version };
  }
  const context = { window, console, RegExp, Set, JSON, String, Number, Math, Array, Object };
  run(read("app/web/frontend/js/components/user_errors.js"), context);
  run(read("app/web/frontend/js/client_config.js"), context);
  return { window, emit: (kind, event) => (listeners[kind] || []).forEach((fn) => fn(event)) };
}

const VERIFY = "Failure [INSTALL_FAILED_VERIFICATION_FAILURE]";
const NO_DEVICE = "adb: error: failed to get feature set: device offline";

module.exports = async function () {
  // Android: копия из Bridge при загрузке, отбор по платформе и версии — здесь же.
  const config = {
    schema: 1,
    user_errors: [
      { id: "verify", icon: "shield", title: "Магнитола отклонила приложение", match: ["VERIFICATION_FAILURE"],
        text: { pc: "ПК", android: "Телефон" }, steps: { pc: ["шаг ПК"], android: ["шаг телефона", 7] } },
      { id: "no_device", title: "Новый заголовок «нет магнитолы»", match: ["device offline"], steps: ["один шаг"] },
      { id: "future", title: "Только для новых", match: ["VERIFICATION"], min_app: "1.0.70" },
      { id: "pc_only", title: "Только ПК", match: ["VERIFICATION"], platforms: ["pc"] },
      { id: "broken", title: "Битый", match: ["(("] },
      { id: "untitled", match: ["offline"] },
    ],
    flags: { new_flow: true }, settings: { wait: 120 }, texts: { hello: "Привет" },
  };
  const a = load({ android: true, config });
  const verify = a.window.UserErrors.classify(VERIFY);
  assert(verify && verify.id === "verify", "серверное правило для новой ошибки работает на Android");
  assert(verify.text === "Телефон" && verify.steps.length === 1 && verify.steps[0] === "шаг телефона",
    "текст и шаги — для своей платформы, мусор в шагах отброшен");
  const offline = a.window.UserErrors.classify(NO_DEVICE);
  assert(offline.id === "no_device" && offline.title === "Новый заголовок «нет магнитолы»" && offline.steps.length === 1,
    "правило с тем же id заменяет встроенное");
  assert(a.window.UserErrors.byId("no_device").title === "Новый заголовок «нет магнитолы»", "byId — тоже серверное");
  assert(a.window.ClientConfig.flag("new_flow") === true && a.window.ClientConfig.flag("missing") === false, "флажки");
  assert(a.window.ClientConfig.setting("wait", 90) === 120 && a.window.ClientConfig.setting("wait", "x") === "x",
    "настройки — с проверкой типа");
  assert(a.window.ClientConfig.text("hello", "Hi") === "Привет", "тексты");

  // ПК: правило «только ПК» подходит, «только для новых версий» — нет.
  const p = load({ android: false, version: "1.0.62" });
  p.window.ClientConfig.apply(config);
  assert(p.window.UserErrors.classify(VERIFY).id === "verify", "первое подходящее правило");
  p.window.ClientConfig.apply({ schema: 1, user_errors: config.user_errors.slice(2) });
  assert(p.window.UserErrors.classify(VERIFY).id === "pc_only", "правило «только ПК» на ПК работает, «для новых» — нет");

  // Свежая копия событием client_config_updated; пустой файл — снова встроенные правила.
  p.emit("client_config_updated", { config: {} });
  assert(p.window.UserErrors.classify(VERIFY) === null, "без серверных правил — прежнее поведение");
  assert(p.window.UserErrors.classify(NO_DEVICE).title === "Магнитола не подключена", "встроенное правило на месте");
  p.emit("client_config_updated", { config: null });
  assert(p.window.UserErrors.classify(NO_DEVICE).id === "no_device", "битый ответ не ломает окна");
};
