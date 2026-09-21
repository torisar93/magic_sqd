// Необработанные JS-ошибки (window.onerror/unhandledrejection) на обеих платформах —
// app/web/frontend/js/events.js (десктоп) и android/app/src/main/assets/js/bridge.js (Android).
// Жалоба/находка (лог #462, 2026-09-21): ResizeObserver — доброкачественное сообщение
// браузера, зациклилось на каждый кадр и дало 1285 одинаковых строк в журнале сессии, ни
// одной реальной строки установки. Проверяем: 1) ResizeObserver не шлётся вовсе, 2) обычная
// ошибка шлётся как раньше, 3) общий лимит на сессию режет ЛЮБУЮ зацикленную ошибку.
"use strict";
const { read, assert, run } = require("./_util");

async function checkFile(file, { window: windowMock, listeners, calls }) {
  const src = read(file);
  run(src, { window: windowMock });

  const errorHandler = listeners.error && listeners.error[0];
  const rejectionHandler = listeners.unhandledrejection && listeners.unhandledrejection[0];
  assert(typeof errorHandler === "function", `${file}: обработчик "error" зарегистрирован`);
  assert(typeof rejectionHandler === "function", `${file}: обработчик "unhandledrejection" зарегистрирован`);

  // 1) ResizeObserver — не шлётся вовсе (оба известных варианта текста).
  errorHandler({ message: "ResizeObserver loop completed with undelivered notifications.", error: null, target: windowMock });
  errorHandler({ message: "ResizeObserver loop limit exceeded", error: null, target: windowMock });
  assert(calls.length === 0, `${file}: ResizeObserver не отправлен: ${JSON.stringify(calls)}`);

  // 2) Обычная ошибка — отправляется как раньше.
  errorHandler({ message: "TypeError: x is not a function", error: { stack: "at foo()" }, target: windowMock });
  assert(calls.length === 1, `${file}: обычная ошибка отправлена`);
  assert(calls[0].message === "TypeError: x is not a function", `${file}: текст ошибки передан как есть: ${calls[0].message}`);

  // unhandledrejection — тоже доходит (второй путь в sendError, не ResizeObserver-специфичный).
  rejectionHandler({ reason: { message: "boom", stack: "at bar()" } });
  assert(calls.length === 2, `${file}: unhandledrejection тоже отправлен`);
  assert(calls[1].message === "unhandledrejection: boom", `${file}: текст unhandledrejection: ${calls[1].message}`);

  // 3) Общий лимит на сессию — зацикленная НЕ-ResizeObserver ошибка тоже режется.
  // MAX_ERRORS_PER_SESSION считает ВСЕ отправленные ошибки за сессию (включая 2 уже
  // отправленных выше), а не только эту серию — значит из 30 новых пройдёт только 18.
  for (let i = 0; i < 30; i++) {
    errorHandler({ message: "Loop error " + i, error: null, target: windowMock });
  }
  assert(calls.length === 20, `${file}: лимит на сессию сработал, вызовов: ${calls.length}`);
  const capped = calls.filter((c) => c.message.includes("достигнут предел"));
  assert(capped.length === 1, `${file}: пометка о пределе ровно один раз: ${capped.length}`);

  // ResizeObserver по-прежнему не шлётся, даже после исчерпания лимита обычными ошибками.
  errorHandler({ message: "ResizeObserver loop completed with undelivered notifications.", error: null, target: windowMock });
  assert(calls.length === 20, `${file}: ResizeObserver и после лимита не даёт нового вызова`);
}

module.exports = async function () {
  // Десктоп: window.pywebview.api.client_log_error(message, stack) -> Promise.
  {
    const listeners = {};
    const calls = [];
    const windowMock = { addEventListener(kind, handler) { (listeners[kind] = listeners[kind] || []).push(handler); } };
    windowMock.pywebview = { api: {
      client_log_error: async (message, stack) => { calls.push({ message, stack }); return {}; },
      install_log_append: async () => ({}),
    } };
    await checkFile("app/web/frontend/js/events.js", { window: windowMock, listeners, calls });
  }

  // Android: window.AndroidBridge.call(method, argsJson) -> JSON-строка (синхронный мост).
  {
    const listeners = {};
    const calls = [];
    const windowMock = {
      addEventListener(kind, handler) { (listeners[kind] = listeners[kind] || []).push(handler); },
      AndroidBridge: {
        call(method, argsJson) {
          if (method === "client_log_error") calls.push(JSON.parse(argsJson));
          return "{}";
        },
      },
    };
    await checkFile("android/app/src/main/assets/js/bridge.js", { window: windowMock, listeners, calls });
  }
};
