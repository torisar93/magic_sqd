/* «Скачать заранее» — подписчики Boosty (владелец, 2026-10-05): кнопка рядом с «Открыть инструкцию» (вариант B —
   текст и размер) и «таблетка» «✓ офлайн» / «↻ обновить» в списке моделей (её рисует js/catalog-ui.js по
   offline_state версий). Файл одинаковый для ПК (app/web/frontend/js/) и Android (assets/js/); платформа отдаёт
   init() свои вызовы и окна. Бэкенд: app/web/api/offline_api.py (ПК), WebBridge.kt + mobile_bridge.py (Android).

   Состояния кнопки: none — «Скачать заранее · размер», locked — то же с замком (без подписки, нажатие — окно
   Boosty), progress — «Скачивается… 45%» (нажатие останавливает), done — «Доступна офлайн» (нажатие — удалить
   скачанное), update — «Обновить · размер»; нет пакета на сервере (своя модель, модель без файлов) — кнопки нет. */
(() => {
  const MB = 1048576;
  const svg = (d, width = 1.9) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
  const ICONS = {
    download: svg('<path d="M12 4v11"/><path d="m7.5 10.5 4.5 4.5 4.5-4.5"/><path d="M5 19.5h14"/>'),
    done: svg('<path d="M5 19.5h14"/><path d="m8 11.5 3 3 5.5-6"/>'),
    update: svg('<path d="M20 12a8 8 0 1 1-2.6-5.9"/><path d="M20 4v5h-5"/>'),
    lock: svg('<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>'),
  };
  let platform = null;          // {call, subscriber, showLocked, confirm, notify} — см. init
  const statuses = new Map();   // ключ модели -> последнее состояние {state, available, total_bytes, missing_bytes, done, total}
  const known = new Map();      // ключ модели -> Set версий (объекты каталога) — им проставляем offline_state
  let current = null;           // {version, button} — кнопка открытой карточки

  function size(bytes) {
    if (!(bytes > 0)) return "";
    if (bytes < MB) return "< 1 МБ";
    if (bytes < 1024 * MB) return `${Math.round(bytes / MB)} МБ`;
    return `${(bytes / 1024 / MB).toFixed(1).replace(".", ",")} ГБ`;
  }

  function track(version) {
    if (!version || !version.key) return;
    if (!known.has(version.key)) known.set(version.key, new Set());
    known.get(version.key).add(version);
  }

  // Итог для значка в списке: только устойчивые состояния (во время скачивания значок прежний)
  function settle(key, state) {
    if (state !== "done" && state !== "update" && state !== "none") return;
    for (const version of known.get(key) || []) version.offline_state = state === "none" ? null : state;
    window.CatalogUI?.refreshPills?.();
  }

  function publishable(version) {
    return version && version.has_wizard_spec !== false && !version.is_pending && !version.early_locked;
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text) node.textContent = text;
    return node;
  }

  function iconEl(name) {
    const node = el("span", "offline-ico");
    node.setAttribute("aria-hidden", "true");
    node.innerHTML = ICONS[name];
    return node;
  }

  function render(button, version) {
    const st = statuses.get(version.key) || { state: version.offline_state || "none", available: publishable(version) };
    let state = st.state || "none";
    const subscriber = !!platform?.subscriber?.();
    if (!st.available && state === "none") { button.hidden = true; return; }
    button.hidden = false;
    if (state === "none" && !subscriber) state = "locked";
    button.className = `model-offline is-${state}`;
    button.dataset.state = state;
    const children = [];
    let label = "";
    if (state === "progress") {
      const total = st.total || st.missing_bytes || 0;
      const done = Math.min(st.done || 0, total);
      const pct = total > 0 ? Math.floor((done / total) * 100) : 0;
      const fill = el("i", "offline-fill");
      fill.style.width = `${pct}%`;
      children.push(fill, el("span", "offline-label", total > 0 ? `Скачивается… ${pct}%` : "Скачивается…"));
      if (total > 0) children.push(el("small", "", `${Math.round(done / MB)} из ${size(total)}`));
      label = "Скачивается заранее. Нажмите, чтобы остановить";
    } else if (state === "done") {
      children.push(iconEl("done"), el("span", "offline-label", "Доступна офлайн"));
      label = "Модель скачана заранее и доступна без интернета. Нажмите, чтобы удалить скачанные файлы";
    } else if (state === "update") {
      children.push(iconEl("update"), el("span", "offline-label", "Обновить"));
      if (st.missing_bytes > 0) children.push(el("small", "", size(st.missing_bytes)));
      label = "На сервере новые файлы модели — докачать";
    } else if (state === "locked") {
      children.push(iconEl("lock"), el("span", "offline-label", "Скачать заранее"));
      label = "Скачать заранее — для подписчиков Boosty";
    } else {
      children.push(iconEl("download"), el("span", "offline-label", "Скачать заранее"));
      const bytes = st.missing_bytes || st.total_bytes;
      if (bytes > 0) children.push(el("small", "", size(bytes)));
      label = "Скачать все файлы модели заранее, чтобы работать без интернета";
    }
    button.replaceChildren(...children);
    button.title = label;
    button.setAttribute("aria-label", label);
  }

  function rerender(key) {
    if (current && current.version.key === key && current.button.isConnected) render(current.button, current.version);
  }

  function applyStatus(result) {
    if (!result || !result.key || !result.state) return;
    const prev = statuses.get(result.key) || {};
    // Пока идёт скачивание, ответ о состоянии с диска (без хода) его не перебивает
    if (prev.state === "progress" && result.state !== "progress" && !result.final) return;
    statuses.set(result.key, { ...prev, ...result });
    settle(result.key, result.state);
    rerender(result.key);
  }

  function request(key) {
    Promise.resolve(platform.call("offline_status", { model_key: key }))
      .then((result) => { if (result && result.state) applyStatus({ key, ...result }); })
      .catch(() => {});
  }

  function bind(version, button) {
    if (!button) return;
    if (!platform || !version || !version.key || version.early_locked) { button.hidden = true; return; }
    track(version);
    current = { version, button };
    button.onclick = () => onClick(version, button);
    render(button, version);
    request(version.key);
  }

  async function onClick(version, button) {
    const key = version.key;
    const st = statuses.get(key) || { state: version.offline_state || "none" };
    if (st.state === "progress") {
      platform.call("offline_cancel", {});
      return;
    }
    if (st.state === "done") {
      const info = await Promise.resolve(platform.call("offline_delete_info", { model_key: key }));
      const name = [version.brand, version.name, version.modification].filter(Boolean).join(" ");
      const bytes = info && info.bytes > 0 ? ` (${size(info.bytes)})` : "";
      const ok = await platform.confirm({
        title: "Удалить скачанные файлы?",
        text: `Скачанные заранее файлы модели ${name}${bytes} будут удалены. Модель останется в списке, `
          + "а нужные файлы снова скачаются по ходу установки — для этого понадобится интернет.",
        ok: "Удалить",
      });
      if (!ok) return;
      const result = await Promise.resolve(platform.call("offline_delete", { model_key: key }));
      if (result && result.ok !== undefined) applyDeleted({ key, ...result });
      return;
    }
    if (!platform.subscriber()) { platform.showLocked(version); return; }
    statuses.set(key, { ...st, state: "progress", done: 0, total: st.missing_bytes || 0, available: true });
    rerender(key);
    const result = await Promise.resolve(platform.call("offline_download", { model_key: key }));
    if (result && result.ok === false) applyFinished({ key, ...result });
  }

  function applyProgress(event) {
    if (!event || !event.key) return;
    const prev = statuses.get(event.key) || {};
    statuses.set(event.key, { ...prev, state: "progress", available: true, done: event.done || 0, total: event.total || 0 });
    rerender(event.key);
  }

  function applyFinished(event) {
    if (!event || !event.key) return;
    const prev = statuses.get(event.key) || {};
    const fallback = prev.state === "progress" ? null : prev.state;
    const state = event.state || fallback || (known.get(event.key) && [...known.get(event.key)][0]?.offline_state) || "none";
    statuses.set(event.key, { ...prev, ...event, state, done: 0, total: 0, available: true });
    settle(event.key, state);
    rerender(event.key);
    const version = [...(known.get(event.key) || [])][0];
    if (event.locked) {
      if (event.error) platform.notify(event.error);
      else platform.showLocked(version || { key: event.key });
    } else if (event.error) {
      platform.notify(event.error);
    } else if (event.failed > 0) {
      platform.notify(`Скачалось не всё: не удалось скачать файлов — ${event.failed}. Проверьте интернет и нажмите «Обновить».`);
    }
    if (!event.cancelled && !event.locked && !event.error) request(event.key);  // свежий размер «Обновить»
  }

  function applyDeleted(event) {
    if (!event || !event.key) return;
    if (!event.ok) { if (event.error) platform.notify(event.error); return; }
    const prev = statuses.get(event.key) || {};
    statuses.set(event.key, { ...prev, state: "none", final: true });
    settle(event.key, "none");
    rerender(event.key);
    request(event.key);
  }

  // Текст окна «Скачать заранее» для неподписчика — общий, окна у платформ свои (как CatalogUI.earlyAccessParagraphs)
  function lockedParagraphs() {
    return [
      el("p", "early-body", "Подписчики Boosty могут скачать все файлы модели заранее — приложения, прошивки, "
        + "файлы для флешки — и работать у машины без интернета."),
      el("p", "early-hint", "Без подписки файлы, как и раньше, скачиваются по ходу установки. Уже подписались? "
        + "Войдите в аккаунт с той же почтой, что на Boosty."),
    ];
  }

  /* platform: call(method, args) — вызов бэкенда (ответ или Promise; на Android ответы приходят событиями);
     subscriber() — отмечен ли вошедший как подписчик Boosty; showLocked(version) — окно с Boosty;
     confirm({title, text, ok}) — Promise<boolean>; notify(text) — сообщение. */
  function init(api) {
    platform = api;
    const events = window.events;
    if (events) {
      events.on("offline_status", (event) => applyStatus(event));
      events.on("offline_progress", applyProgress);
      events.on("offline_finished", (event) => applyFinished({ ...event, final: true }));
      events.on("offline_deleted", applyDeleted);
      events.on("auth_subscriber_result", () => { if (current) render(current.button, current.version); });
    }
  }

  window.OfflineUI = { init, bind, track, size, lockedParagraphs, refresh: () => current && render(current.button, current.version) };
})();
