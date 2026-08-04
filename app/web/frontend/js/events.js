// Приёмник событий от Python (см. app/web/events.py: EventBridge) — простая
// pub/sub-шина, чтобы экраны подписывались на события по kind, не завязываясь
// на глобальную функцию напрямую.
(function () {
  const listeners = {};

  function on(kind, handler) {
    (listeners[kind] ||= []).push(handler);
  }

  function off(kind, handler) {
    if (!listeners[kind]) return;
    listeners[kind] = listeners[kind].filter((h) => h !== handler);
  }

  window.__onBackendEvent = function (event) {
    const handlers = listeners[event.kind] || [];
    for (const handler of handlers) {
      try {
        handler(event);
      } catch (err) {
        console.error("event handler failed for", event.kind, err);
      }
    }
  };

  window.events = { on, off };
})();
