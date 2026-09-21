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

  // Глобальный перехват необработанных ошибок — портировано с desktop
  // (app/web/frontend/js/events.js), на Android такого не было вовсе: до
  // этой правки падение на чистом JS (не дошедшее до нативного Kotlin-кода)
  // не оставляло вообще никакого следа, даже локально. bridge.js — самый
  // первый <script> в index.html, поэтому обработчик ставится максимально
  // рано.
  // ResizeObserver — известное доброкачественное сообщение браузера (сам
  // ResizeObserver не успел доставить уведомление в пределах кадра, ничего
  // не сломано, см. спецификацию/WICG issue #38), но всплывает через
  // window.onerror и в редком случае зацикливается на каждый кадр —
  // реальный случай (лог #462, 2026-09-21): 1285 одинаковых строк подряд,
  // ни одной реальной строки установки, до того, как что-либо успело
  // произойти на этом экране. Не шлём вовсе.
  const BENIGN_ERROR_PREFIX = "ResizeObserver loop";
  // Общая страховка ПОВЕРХ фильтра выше — на случай, если зациклится что-то
  // ДРУГОЕ, не ResizeObserver: не даём одной сессии затопить журнал
  // (и js_errors.log на телефоне, и прочный журнал сессии) тысячами
  // одинаковых строк.
  const MAX_ERRORS_PER_SESSION = 20;
  let sentErrorCount = 0;
  function sendError(message, stack) {
    if (String(message).startsWith(BENIGN_ERROR_PREFIX)) return;
    if (sentErrorCount >= MAX_ERRORS_PER_SESSION) return;
    sentErrorCount += 1;
    if (sentErrorCount === MAX_ERRORS_PER_SESSION) {
      message = `${message}\n(достигнут предел ${MAX_ERRORS_PER_SESSION} ошибок за сессию — дальнейшие подавляются)`;
    }
    if (window.AndroidBridge) {
      window.Bridge.call("client_log_error", { message, stack: stack || "" });
    }
  }
  window.addEventListener("error", (e) => {
    // Ошибки ЗАГРУЗКИ ресурса всплывают только в фазе capture, на самом
    // элементе (см. MDN: GlobalEventHandlers/error) — без capture:true этот
    // обработчик ловил бы только настоящие JS runtime-ошибки.
    if (e.target && e.target !== window) {
      const el = e.target;
      sendError(`resource load failed: <${el.tagName}> ${el.src || el.href || ""}`, "");
      return;
    }
    sendError(String(e.message || e.error || "unknown error"), e.error && e.error.stack || "");
  }, true);
  window.addEventListener("unhandledrejection", (e) => {
    const reason = e.reason;
    sendError("unhandledrejection: " + String(reason && reason.message || reason),
      reason && reason.stack || "");
  });
})();
