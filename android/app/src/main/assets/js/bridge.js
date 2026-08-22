// Мост к Kotlin. В отличие от pywebview (window.pywebview.api.X(), честный
// async through IPC), Android @JavascriptInterface — синхронный вызов: JS-поток
// блокируется, пока Kotlin-метод не вернёт строку. Поэтому Bridge.call()
// используется для БЫСТРЫХ операций (чтение каталога машин и т.п.), а для
// долгих/фоновых вещей (ADB-этапы, запись флешки) Kotlin сам зовёт обратно
// window.__onBridgeEvent(...) через evaluateJavascript — тот же принцип
// событийной шины, что и в desktop-версии (см. app/web/frontend/js/events.js),
// просто источник событий другой.
window.Bridge = {
  call(method, args) {
    if (!window.AndroidBridge) {
      console.error("AndroidBridge не внедрён — страница открыта не в WebView приложения?");
      return null;
    }
    const raw = window.AndroidBridge.call(method, JSON.stringify(args || {}));
    return JSON.parse(raw);
  },
};

(function () {
  const listeners = {};

  function on(kind, handler) {
    if (!listeners[kind]) listeners[kind] = [];
    listeners[kind].push(handler);
  }

  window.__onBridgeEvent = function (json) {
    const event = JSON.parse(json);
    const handlers = listeners[event.kind] || [];
    for (const handler of handlers) {
      try {
        handler(event);
      } catch (err) {
        console.error("event handler failed for", event.kind, err);
      }
    }
  };

  window.events = { on };
})();
