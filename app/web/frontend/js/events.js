// Приёмник событий от Python (см. app/web/events.py: EventBridge) — простая
// pub/sub-шина, чтобы экраны подписывались на события по kind, не завязываясь
// на глобальную функцию напрямую.
(function () {
  const listeners = {};

  function on(kind, handler) {
    // Не ||= — старый Chromium в Qt5/PySide2 (см. installer_win7_x86.iss)
    // не умеет логические операторы присваивания (||=/&&=/??=, V8 85+),
    // падает синтаксической ошибкой уже на парсинге файла. Равнозначная
    // запись без него — тот же результат в любом движке.
    if (!listeners[kind]) listeners[kind] = [];
    listeners[kind].push(handler);
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

  // Глобальный перехват необработанных ошибок — до сих пор такие ошибки
  // были видны ТОЛЬКО в DevTools (недоступна обычному технику, F12 в
  // обычной сборке не открывается), а window.pywebview.api ничего о них
  // не узнавал: DEBUG_LOG_ALL (см. main_web.py) оборачивает только сами
  // js_api-методы, а не то, что происходит в JS до/помимо их вызова.
  // events.js — самый первый <script> в index.html, поэтому обработчик
  // ставится максимально рано; window.pywebview может быть ещё не готов
  // (ошибка до события pywebviewready) — тогда просто копим в очередь и
  // отправляем её целиком, как только мост появится.
  const pendingErrors = [];
  // ResizeObserver — известное доброкачественное сообщение браузера (сам
  // ResizeObserver не успел доставить уведомление в пределах кадра, ничего
  // не сломано, см. спецификацию/WICG issue #38), но всплывает через
  // window.onerror и в редком случае зацикливается на каждый кадр —
  // реальный случай на Android-порте этого же кода (лог #462, 2026-09-21):
  // 1285 одинаковых строк подряд, ни одной реальной строки установки. Не
  // шлём вовсе.
  const BENIGN_ERROR_PREFIX = "ResizeObserver loop";
  // Общая страховка ПОВЕРХ фильтра выше — на случай, если зациклится что-то
  // ДРУГОЕ, не ResizeObserver: не даём одной сессии затопить журнал
  // тысячами одинаковых строк.
  const MAX_ERRORS_PER_SESSION = 20;
  let sentErrorCount = 0;
  function sendError(message, stack) {
    if (String(message).startsWith(BENIGN_ERROR_PREFIX)) return;
    if (sentErrorCount >= MAX_ERRORS_PER_SESSION) return;
    sentErrorCount += 1;
    if (sentErrorCount === MAX_ERRORS_PER_SESSION) {
      message = `${message}\n(достигнут предел ${MAX_ERRORS_PER_SESSION} ошибок за сессию — дальнейшие подавляются)`;
    }
    if (window.pywebview && window.pywebview.api && window.pywebview.api.client_log_error) {
      window.pywebview.api.client_log_error(message, stack || "").catch(() => {});
      // Если ошибка случилась прямо во время активной сессии установки (см.
      // stage_wizard.js: window.__installLogSessionToken) — дописываем её и в
      // прочный журнал ЭТОЙ сессии (app/pending_install_logs.py), с явным
      // маркером: иначе падение на чистом JS осталось бы только в локальном
      // js_errors.log, недоступном обычному технику, вместо того чтобы уйти
      // на сервер вместе с остальным логом сессии.
      const token = window.__installLogSessionToken;
      if (token && window.pywebview.api.install_log_append) {
        window.pywebview.api.install_log_append(
          token, `=== НЕОБРАБОТАННАЯ ОШИБКА JS: ${message} ===${stack ? "\n" + stack : ""}`, true,
        ).catch(() => {});
      }
    } else {
      pendingErrors.push([message, stack || ""]);
    }
  }
  window.addEventListener("error", (e) => {
    // Ошибки ЗАГРУЗКИ ресурса (<script src>/<link href>/<img> не нашли
    // файл, сеть оборвалась и т.п.) не всплывают до window — событие
    // "error" на них видно ТОЛЬКО в фазе capture, на самом элементе (см.
    // MDN: GlobalEventHandlers/error). Без capture:true этот обработчик
    // ловил только настоящие JS runtime-ошибки, а сломанную/пропавшую
    // css/js-подгрузку — никогда. e.target — DOM-элемент только для
    // ошибок загрузки ресурса; у обычной JS-ошибки e.target === window.
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
  window.addEventListener("pywebviewready", () => {
    while (pendingErrors.length) {
      const [message, stack] = pendingErrors.shift();
      sendError(message, stack);
    }
  });
})();
