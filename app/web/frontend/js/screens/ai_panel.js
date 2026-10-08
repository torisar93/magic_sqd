// ИИ-мастер на ПК («Установка с ИИ», платная функция для подписчиков Boosty): панель слева и кнопка «✦ ИИ» в шапке
// — как в утверждённом макете (_previews/ai-master, ПК). Кнопку видят только те, кому сервер включил функцию
// (status.enabled). Чат и протокол — общее с Android ядро js/ai_master.js; программа для ИИ — stageWizard.ai
// (screens/stage_wizard.js: состояние, кнопки, инструкции, совет по приложениям); мост — ai_call/ai_shell
// (app/web/api/ai_api.py). Пока панель закрыта, события мастера копятся и уходят ИИ, когда её откроют.
(function () {
  "use strict";
  const SUBSCRIBE_URL = "https://boosty.to/magic_sqd?locale=ru_RU";
  const HIGHLIGHT_MS = 12000;
  let panel = null;
  let master = null;
  let toggle = null;
  let available = false;
  let pendingEvents = [];
  let spot = null;

  const api = () => window.pywebview.api;
  const wizard = () => window.stageWizard.ai;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // Сервер ответил «нет связи» — ядро чата копит ввод и повторяет (исключение = нет интернета).
  async function call(path, payload) {
    const result = await api().ai_call(path, payload || null);
    if (result && result.network_error) throw new Error(result.network_error);
    return result || { status: "error" };
  }

  const bridge = {
    status: () => call("status"),
    start: (payload) => call("start", payload),
    turn: (payload) => call("turn", payload),
    shell: (cmd, mode) => {
      const state = wizard().state();
      return api().ai_shell(state.device ? state.device.serial : null, cmd, mode, !!state.busy);
    },
  };

  // -- подсветка кнопки/вкладки/приложения в окне программы: рамка и ярлык, пока элемент на месте ----------
  function clearSpot() {
    if (!spot) return;
    spot.ring.remove();
    spot.tag.remove();
    spot = null;
  }

  // Кнопка может появиться чуть позже (этап перерисовывается асинхронно — например, после совета по приложениям).
  async function highlight(target) {
    clearSpot();
    let node = wizard().element(target);
    for (let tries = 0; !node && tries < 15; tries += 1) {
      await sleep(200);
      node = wizard().element(target);
    }
    if (!node || spot) return;
    const ring = document.createElement("div");
    ring.className = "ai-ring";
    const tag = document.createElement("div");
    tag.className = "ai-tag";
    tag.textContent = /^app:|^tab:/.test(String(target)) ? "ИИ: здесь" : "ИИ предлагает нажать";
    document.body.append(ring, tag);
    spot = { node, ring, tag, until: Date.now() + HIGHLIGHT_MS };
    const current = spot;
    node.addEventListener("click", () => { if (spot === current) clearSpot(); }, { once: true });
    try { node.scrollIntoView({ block: "nearest", behavior: "smooth" }); } catch { /* старый движок */ }
    (function follow() {
      if (spot !== current) return;
      const rect = node.getBoundingClientRect();
      if (!node.isConnected || Date.now() > current.until) { clearSpot(); return; }
      const visible = rect.width > 0 && rect.height > 0;
      ring.hidden = tag.hidden = !visible;
      if (visible) {
        Object.assign(ring.style, { left: `${rect.left - 6}px`, top: `${rect.top - 6}px`,
          width: `${rect.width + 12}px`, height: `${rect.height + 12}px` });
        const above = rect.top > 60;
        Object.assign(tag.style, { left: `${Math.max(8, rect.left)}px`,
          top: `${above ? rect.top - 40 : rect.bottom + 12}px` });
      }
      requestAnimationFrame(follow);
    })();
  }

  // -- программа для ядра чата ---------------------------------------------------------------------------
  function modelKey() {
    const model = wizard().model();
    return model ? window.DeviceHint.modelPath(model) : "";
  }

  // Оглавление инструкций модели для сервера: номера блоков — те же, что ставит фрейм (Instructions12.reader).
  function outline() {
    const total = wizard().state().stage.total || 0;
    const result = [];
    for (let number = 1; number <= total; number += 1) {
      const doc = wizard().instruction(number);
      if (!doc) continue;
      const blocks = window.Instructions12.outline(doc.html);
      if (blocks.length) result.push({ stage: number, title: doc.title, blocks });
    }
    return result;
  }

  async function openModel(key) {
    const picker = window.mainPicker;
    const hit = picker && picker.findModelByPath(String(key || ""));
    if (!hit) return { ok: false, output: "Такой модели нет в каталоге этой программы." };
    if (window.stageWizard.isBusy()) return { ok: false, output: "Идёт этап — модель сейчас не сменить." };
    await picker.openModel(hit.model);
    const until = Date.now() + 60000;  // этапы модели могут докачиваться
    while (Date.now() < until) {
      const state = wizard().state();
      if (state.model === key && !state.loading) return { ok: true, output: `Открыта модель ${key}.` };
      await sleep(300);
    }
    return { ok: false, output: "Модель не открылась (нужен ранний доступ или нет связи)." };
  }

  const program = {
    modelKey,
    title: () => { const model = wizard().model(); return model ? model.display_label || model.name : ""; },
    outline,
    state: () => wizard().state(),
    focus: (stage, block) => { wizard().focus(stage, block); },
    highlight,
    press: (control) => wizard().press(control),
    label: (control) => wizard().label(control),
    openModel,
    selectApps: (picks, avoid) => wizard().selectApps(picks, avoid),
    photo: (stage, block) => {
      const doc = wizard().instruction(stage);
      return doc ? window.Instructions12.photo(doc.html, block) : null;
    },
  };

  // -- панель ------------------------------------------------------------------------------------------
  function isOpen() { return !!panel && !panel.hidden; }

  // Как ссылки Boosty в программе (<a target=_blank>): pywebview открывает их в браузере системы.
  function openExternal(url) {
    const link = Object.assign(document.createElement("a"), { href: url, target: "_blank", rel: "noopener" });
    document.body.append(link);
    link.click();
    link.remove();
  }

  function setOpen(open) {
    if (!panel) {
      panel = document.createElement("aside");
      panel.id = "ai-panel";
      panel.setAttribute("aria-label", "Установка с ИИ");
      panel.hidden = true;
      document.body.append(panel);
      master = window.AiMaster.create({
        container: panel, bridge, program,
        onClose: () => setOpen(false),
        openUrl: openExternal,
        subscribeUrl: SUBSCRIBE_URL,
      });
    }
    panel.hidden = !open;
    document.body.classList.toggle("ai-open", open);
    if (toggle) toggle.setAttribute("aria-pressed", String(open));
    if (!open) { clearSpot(); return Promise.resolve(null); }
    const queued = pendingEvents;
    pendingEvents = [];
    return master.open().then((status) => {
      queued.forEach((item) => master.event(item.name, item.detail));
      return status;
    });
  }

  function mountButton() {
    const actions = document.querySelector("#global-header .cat-top-actions");
    if (!actions || toggle) return !!toggle;
    toggle = document.createElement("button");
    toggle.type = "button";
    toggle.id = "ai-toggle";
    toggle.className = "header-ai";
    toggle.title = "Установка с ИИ";
    toggle.setAttribute("aria-pressed", "false");
    toggle.append(Object.assign(document.createElement("span"), { className: "header-ai-mark", textContent: "✦" }),
      Object.assign(document.createElement("span"), { textContent: "ИИ" }));
    toggle.hidden = !available;
    toggle.addEventListener("click", () => setOpen(!isOpen()));
    actions.insertBefore(toggle, actions.querySelector(".header-log"));
    return true;
  }

  // Кнопку показываем, если сервер включил функцию этому технику (в т.ч. с замком «Подписаться»); нет связи —
  // не трогаем то, что уже показано.
  async function refreshAvailability() {
    let data;
    try { data = await call("status"); } catch { return; }
    available = !!(data && data.enabled);
    if (toggle) toggle.hidden = !available;
    if (!available && isOpen()) setOpen(false);
    else if (isOpen()) master.refresh();
  }

  function init() {
    if (!window.AiMaster || !window.stageWizard) return;
    if (!mountButton()) {
      const observer = new MutationObserver(() => { if (mountButton()) observer.disconnect(); });
      observer.observe(document.body, { childList: true, subtree: true });
    }
    // События мастера (stage_wizard.js: aiNotify) — ИИ; пока панель закрыта, копим последние.
    window.addEventListener("magicsqd-ai", (event) => {
      const item = event.detail || {};
      if (isOpen()) master.event(item.name, item.detail);
      else pendingEvents = pendingEvents.concat([item]).slice(-12);
    });
    document.addEventListener("magicsqd-auth", () => refreshAvailability());
    if (window.events) window.events.on("auth_subscriber_result", () => refreshAvailability());
    refreshAvailability();
  }

  // «Спросить ИИ» снаружи (пустой поиск каталога): открыть панель и отправить вопрос, когда чат готов.
  async function ask(text) {
    if (!available) return;
    const status = await setOpen(true);
    if (status === "ok" || status === "limit") master.send(text);
  }

  window.aiPanel = { init, open: () => setOpen(true), ask, isAvailable: () => available };
})();
