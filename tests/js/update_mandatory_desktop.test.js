// ПК: обязательное обновление (update_api.py: mandatory, владелец 2026-09-25) — окно нельзя закрыть или
// отложить: нет «Позже», Esc не закрывает, после сбоя установки — «Повторить», а не выход из окна.
"use strict";
const { read, slice, assert, run, sleep } = require("./_util");

function element() {
  const listeners = {};
  return {
    listeners, style: {}, hidden: false, disabled: false, textContent: "", innerHTML: "", scrollTop: 0, scrollHeight: 0,
    addEventListener(type, fn) { listeners[type] = fn; },
    appendChild() {}, showModal() { this.open = true; }, close() { this.open = false; },
  };
}

module.exports = async function () {
  const file = "app/web/frontend/js/screens/dialogs.js";
  const src = read(file);
  const code = slice(src, "  const update = (() => {", "    return { init, open };\n  })();", file) + "    return { init, open };\n  })();\nthis.update = update;";
  const els = {};
  const events = {};
  let installResult = { ok: false, error: "Нет соединения с сервером." };
  const ctx = run(code, {
    document: { getElementById: (id) => els[id] || (els[id] = element()), createElement: () => element() },
    window: {
      events: { on: (kind, fn) => { events[kind] = fn; } },
      classifyLogLevel: () => "info",
      pywebview: { api: { update_install: async () => installResult } },
    },
  });
  ctx.update.init();
  const later = els["update-later-btn"], install = els["update-install-btn"], dialog = els["update-dialog"];
  const escape = () => { let prevented = false; dialog.listeners.cancel({ preventDefault: () => { prevented = true; } }); return prevented; };

  ctx.update.open({ version: "1.0.41", changelog: "x", download_url: "u" });
  assert(later.style.display === "" && els["update-mandatory"].hidden === true, "обычное: «Позже» есть");
  assert(!escape(), "обычное: Esc закрывает");
  assert(els["update-title"].textContent === "Доступно обновление Magic SQD", "обычное: прежний заголовок");

  ctx.update.open({ version: "1.0.41", changelog: "x", download_url: "u", mandatory: true });
  assert(later.style.display === "none" && els["update-mandatory"].hidden === false, "обязательное: без «Позже», с пояснением");
  assert(els["update-title"].textContent === "Обязательное обновление Magic SQD", "обязательное: заголовок");
  assert(escape(), "обязательное: Esc не закрывает");

  await install.listeners.click();
  assert(install.textContent === "Повторить" && later.style.display === "none" && !install.disabled,
    "обязательное: сбой запуска — «Повторить», «Позже» так и нет");
  installResult = { ok: true };
  await install.listeners.click();
  events.update_finished({ success: false, message: "Не удалось скачать" });
  await sleep(0);
  assert(install.textContent === "Повторить" && later.style.display === "none", "обязательное: сбой установки — то же");
  assert(escape(), "обязательное: Esc не закрывает и после сбоя");
};
