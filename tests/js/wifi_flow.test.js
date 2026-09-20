// Android: Wi-Fi ADB на этапе приложений — скачать → окно подключения → установка
// (android/app/src/main/assets/js/app.js: wifiInstallFlow / beginWifiInstall / askWifiForInstall / cancelWifiInstall).
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "android/app/src/main/assets/js/app.js";
  const src = read(file);
  const code = slice(src, "  let wifiInstallFlow = null;", "  function onAdbConnect() {", file)
    + slice(src, "  function onAdbConnectResult(event) {", "  function onAdbLog(event) {", file);
  const calls = [], stageResults = [];
  let picker = null;
  const ctx = {
    stages: [{ index: 3, type: "apps", apps_connection: "wifi", apps_wifi_port: 5555 }], currentIndex: 0,
    Bridge: { call: (n, a) => calls.push([n, a]) },
    connectionPortFor: () => 5555, connectionModeFor: () => "wifi",
    lastWifiHost: "", modelWifiPort: 5555,
    setAdbStatus() {}, log() {}, adbStatusEl: { title: "" }, showOtgHintModal() {},
    onAdbStageResult: (e) => stageResults.push(e),
    document: { querySelector: () => ({ close: () => calls.push(["dialog.close"]) }) },
    promptHostPicker: (title, port, onSubmit, opts) => { picker = { title, port, onSubmit, opts }; },
  };
  const api = run(code + "\nthis.__api={beginWifiInstall,onApkDownloadDone,cancelWifiInstall,onAdbConnectResult,getFlow:()=>wifiInstallFlow};", ctx).__api;

  // 1) успешный путь: скачать -> окно -> подключиться -> установка ровно один раз
  let proceeded = 0;
  api.beginWifiInstall(ctx.stages[0], ["/a.apk"], () => proceeded++);
  assert(calls.at(-1)[0] === "adb_download_apks" && calls.at(-1)[1].index === 3, "старт со скачивания");
  assert(picker === null, "окно подключения не открывается до конца скачивания");
  api.onApkDownloadDone({ index: 99 });
  assert(picker === null, "чужой индекс игнорируется");
  api.onApkDownloadDone({ index: 3 });
  assert(picker && /интернет больше не нужен/.test(picker.opts.help) && !/Не удалось/.test(picker.opts.help), "подсказка про Wi-Fi магнитолы");
  picker.onSubmit("192.168.43.1", 5555);
  assert(calls.at(-1)[0] === "adb_connect_wifi" && calls.at(-1)[1].host === "192.168.43.1", "adb_connect_wifi");
  assert(ctx.lastWifiHost === "192.168.43.1", "адрес запомнен");
  api.onAdbConnectResult({ result: { connected: true, banner: "device::ro.product.model=X" } });
  assert(proceeded === 1 && api.getFlow() === null, "после подключения — установка, поток завершён");

  // 2) неудачное подключение -> окно снова с причиной, потом успех
  proceeded = 0; picker = null;
  api.beginWifiInstall(ctx.stages[0], ["/a.apk"], () => proceeded++);
  api.onApkDownloadDone({ index: 3 });
  picker.onSubmit("10.0.0.1", 5555);
  picker = null;
  api.onAdbConnectResult({ result: { connected: false, reason: "таймаут" } });
  assert(picker && /Не удалось подключиться: таймаут/.test(picker.opts.help) && proceeded === 0, "повторный показ с причиной");
  picker.onSubmit("10.0.0.2", 5555);
  api.onAdbConnectResult({ result: { connected: true, banner: "" } });
  assert(proceeded === 1, "установка после повторной попытки");

  // 3) окно закрыли без подключения -> установка отменена как остановленная
  proceeded = 0; stageResults.length = 0;
  api.beginWifiInstall(ctx.stages[0], ["/a.apk"], () => proceeded++);
  api.onApkDownloadDone({ index: 3 });
  picker.opts.onDismiss();
  assert(stageResults.length === 1 && stageResults[0].result.cancelled === true && stageResults[0].index === 3
    && /уже скачаны/.test(stageResults[0].result.reason), "отмена окна = остановлено");
  assert(proceeded === 0 && api.getFlow() === null, "установка не запускалась");

  // 4) «Остановить», пока ждём подключения
  stageResults.length = 0;
  api.beginWifiInstall(ctx.stages[0], ["/a.apk"], () => proceeded++);
  api.onApkDownloadDone({ index: 3 });
  api.cancelWifiInstall(api.getFlow(), "Установка остановлена пользователем.");
  assert(stageResults.length === 1 && api.getFlow() === null && calls.some((c) => c[0] === "dialog.close"), "остановка закрывает окно");
  picker.opts.onDismiss();
  assert(stageResults.length === 1, "нет двойного результата");

  // 5) подключение из шапки (не из потока) установку не запускает
  stageResults.length = 0; proceeded = 0;
  api.onAdbConnectResult({ result: { connected: true, banner: "" } });
  assert(proceeded === 0 && stageResults.length === 0, "обычное подключение не запускает установку");
};
