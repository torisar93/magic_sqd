// Android: «Подключить», пока идёт действие (запуск, «Работа в движении»…), — окно «дождитесь», а не сброс статуса в
// «не подключено» и окна «Магнитола не подключена» посреди заливки (H3 по Wi-Fi, №16412; разбор 14.11.2026).
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const file = "android/app/src/main/assets/js/app.js";
  const src = read(file);
  const st = { busy: true, notices: 0, calls: [], status: [] };
  const ctx = run("let adbUsbConnecting = false, lastWifiHost = '', modelWifiPort = 5555;\n"
    + slice(src, "  function onAdbConnect() {", "  function onAdbConnectResult(", file)
    + "\nthis.onAdbConnect = onAdbConnect;", {
    operationBusy: () => st.busy, showLabBusyNotice: () => { st.notices++; },
    stages: [{ index: 0 }], currentIndex: 0, connectionModeFor: () => "wired",
    setAdbStatus: (ok, text) => st.status.push(text), setAdbUsbConnecting: () => {},
    Bridge: { call: (m) => st.calls.push(m) }, promptHostPicker: () => {}, connectionPortFor: () => 5555,
  });
  ctx.onAdbConnect();
  assert(st.notices === 1 && !st.calls.length && !st.status.length, "занято — «дождитесь», статус не трогаем");
  st.busy = false;
  ctx.onAdbConnect();
  assert(st.calls[0] === "adb_connect", "свободно — подключаемся как раньше");
};
