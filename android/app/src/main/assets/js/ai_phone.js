// ИИ-мастер на телефоне («Установка с ИИ», платная функция для подписчиков Boosty) — утверждённый вариант Б
// (_previews/ai-master, телефон): чат на весь экран под шапкой и вкладки «✦ ИИ / Инструкция» в самой шапке.
// Кнопку «✦ ИИ» видят только те, кому сервер включил функцию (status.enabled). Чат и протокол — общее с ПК ядро
// js/ai_master.js; программа для ИИ — window.__aiWizard (app.js); мост — WebBridge.kt ai_call/ai_shell, ответы
// событиями ai_reply/ai_shell_result. Подсветку и прокрутку инструкции, сделанные ИИ, пока открыт чат, программа
// показывает, когда техник откроет вкладку «Инструкция» (на ней — метка).
(function () {
  "use strict";
  const SUBSCRIBE_URL = "https://boosty.to/magic_sqd?locale=ru_RU";
  const CALL_TIMEOUT_MS = 70000;  // ход сервера — до ~35 с, плюс сеть
  const HIGHLIGHT_MS = 12000;
  let master = null;
  let sheet = null;
  let seg = null;
  let toggle = null;
  let available = false;
  let mode = false;   // чат ИИ включён (вкладки в шапке)
  let tab = "ai";     // "ai" — чат, "program" — экран программы
  let pending = [];   // подсветка/прокрутка, ждущие вкладку «Инструкция»
  let queued = [];    // события мастера, пока чат выключен
  let spot = null;
  const waiting = new Map();

  const wizard = () => window.__aiWizard;
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const make = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };

  // -- мост: долгий запрос — в фоне Kotlin, ответ событием с тем же id -----------------------------------------
  function request(method, args) {
    return new Promise((resolve) => {
      const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
      const timer = setTimeout(() => { waiting.delete(id); resolve(null); }, CALL_TIMEOUT_MS);
      waiting.set(id, (value) => { clearTimeout(timer); resolve(value); });
      try {
        window.Bridge.call(method, Object.assign({ id }, args));
      } catch (e) {
        waiting.delete(id);
        clearTimeout(timer);
        resolve(null);
      }
    });
  }

  function answer(event, value) {
    const done = waiting.get(event.id);
    if (!done) return;
    waiting.delete(event.id);
    done(value);
  }

  async function call(path, payload) {
    const body = await request("ai_call", { path, payload: payload ? JSON.stringify(payload) : "" });
    if (!body || body.network_error) throw new Error((body && body.network_error) || "нет ответа");
    return body;
  }

  const bridge = {
    status: () => call("status"),
    start: (payload) => call("start", payload),
    turn: (payload) => call("turn", payload),
    shell: async (cmd, modeName) => (await request("ai_shell", { cmd, mode: modeName, busy: !!wizard().state().busy }))
      || { ok: false, output: "Магнитола не ответила." },
  };

  // -- подсветка и прокрутка: сразу, если видна программа, иначе — когда техник откроет «Инструкцию» ----------
  function clearSpot() {
    if (!spot) return;
    spot.ring.remove();
    spot.tag.remove();
    spot = null;
  }

  async function showSpot(target) {
    clearSpot();
    let node = wizard().element(target);
    for (let tries = 0; !node && tries < 15; tries += 1) {
      await sleep(200);
      node = wizard().element(target);
    }
    if (!node || spot || tab !== "program") return;
    const ring = make("div", "ai-ring");
    const tag = make("div", "ai-tag", /^app:|^tab:/.test(String(target)) ? "ИИ: здесь" : "ИИ предлагает нажать");
    document.body.append(ring, tag);
    spot = { ring, tag, until: Date.now() + HIGHLIGHT_MS };
    const current = spot;
    node.addEventListener("click", () => { if (spot === current) clearSpot(); }, { once: true });
    try { node.scrollIntoView({ block: "nearest", behavior: "smooth" }); } catch (e) { /* старый WebView */ }
    (function follow() {
      if (spot !== current) return;
      if (!node.isConnected || Date.now() > current.until || tab !== "program") { clearSpot(); return; }
      const rect = node.getBoundingClientRect();
      const visible = rect.width > 0 && rect.height > 0;
      ring.hidden = tag.hidden = !visible;
      if (visible) {
        Object.assign(ring.style, { left: `${rect.left - 5}px`, top: `${rect.top - 5}px`,
          width: `${rect.width + 10}px`, height: `${rect.height + 10}px` });
        Object.assign(tag.style, { left: `${Math.max(6, Math.min(rect.left, window.innerWidth - 220))}px`,
          top: `${rect.top > 70 ? rect.top - 38 : rect.bottom + 10}px` });
      }
      requestAnimationFrame(follow);
    })();
  }

  // Фрейм инструкции на телефоне без своей прокрутки — блок к середине экрана подводит страница мастера.
  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || data.type !== "magicsqd-ai-focus-at" || tab !== "program") return;
    const content = wizard().contentEl();
    const frame = [...content.querySelectorAll("iframe[data-ai-stage]")].find((f) => f.contentWindow === event.source);
    if (!frame) return;
    const box = content.getBoundingClientRect();
    const top = frame.getBoundingClientRect().top + Number(data.top || 0) - box.top;
    const goal = Math.max(0, content.scrollTop + top - Math.max(0, (box.height - Number(data.height || 0)) / 2));
    const calm = document.documentElement.classList.contains("reduce-motion");
    content.scrollTo({ top: goal, behavior: calm ? "auto" : "smooth" });
    // плавная прокрутка бывает выключена в WebView — тогда просто ставим на место
    setTimeout(() => { if (Math.abs(content.scrollTop - goal) > 40) content.scrollTop = goal; }, 650);
  });

  function runPending() {
    const list = pending;
    pending = [];
    list.forEach((item) => {
      if (item.kind === "focus") wizard().focus(item.stage, item.block);
      else showSpot(item.target);
    });
  }

  function later(item) {
    pending = pending.filter((old) => old.kind !== item.kind).concat([item]);
    if (seg) seg.classList.add("has-mark");
  }

  // -- программа для ядра чата ---------------------------------------------------------------------------
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

  const program = {
    modelKey: () => { const m = wizard().model(); return m ? m.key : ""; },
    title: () => { const m = wizard().model(); return m ? m.title : ""; },
    outline,
    state: () => Object.assign(wizard().state(), { ai_tab: tab === "ai" ? "чат ИИ" : "экран программы" }),
    focus: (stage, block) => {
      if (tab === "program") { wizard().focus(stage, block); return ""; }
      later({ kind: "focus", stage, block });
      return "ИИ отметил это место во вкладке «Инструкция».";
    },
    highlight: (target) => {
      if (tab === "program") { showSpot(target); return ""; }
      later({ kind: "spot", target });
      return "";
    },
    reveal: (stage, block) => { setTab("program"); wizard().focus(stage, block); },
    press: (control) => wizard().press(control),
    label: (control) => wizard().label(control),
    openModel: (key) => wizard().openModel(key),
    selectApps: (picks, avoid) => wizard().selectApps(picks, avoid),
    photo: (stage, block) => {
      const doc = wizard().instruction(stage);
      return doc ? window.Instructions12.photo(doc.html, block) : null;
    },
  };

  // -- экран: кнопка «✦ ИИ», вкладки в шапке, чат под шапкой --------------------------------------------------
  function openExternal(url) {
    const link = Object.assign(document.createElement("a"), { href: url, target: "_blank", rel: "noopener" });
    document.body.append(link);
    link.click();
    link.remove();
  }

  function programLabel() {
    return wizard().onWizard() ? "Инструкция" : "Каталог";
  }

  function setTab(next) {
    tab = next;
    document.body.classList.toggle("ai-chat-shown", mode && tab === "ai");
    if (seg) {
      seg.querySelector(".ai-seg-ai").classList.toggle("on", tab === "ai");
      const other = seg.querySelector(".ai-seg-program");
      other.classList.toggle("on", tab === "program");
      other.textContent = programLabel();
      if (tab === "program") seg.classList.remove("has-mark");
    }
    if (tab === "program") { clearSpot(); runPending(); } else clearSpot();
  }

  function ensureUi() {
    if (sheet) return;
    const topbar = document.querySelector(".topbar");
    sheet = make("div", "ai-sheet");
    sheet.id = "ai-sheet";  // виден, пока открыта вкладка «✦ ИИ» (body.ai-chat-shown, css/ai_phone.css)
    document.getElementById("app").append(sheet);
    seg = make("div", "ai-seg");
    seg.setAttribute("role", "tablist");
    const aiTab = make("button", "ai-seg-ai on", "✦ ИИ");
    const programTab = make("button", "ai-seg-program", "Инструкция");
    [aiTab, programTab].forEach((b) => { b.type = "button"; b.setAttribute("role", "tab"); });
    aiTab.addEventListener("click", () => setTab("ai"));
    programTab.addEventListener("click", () => setTab("program"));
    seg.append(aiTab, programTab);
    seg.hidden = true;
    topbar.insertBefore(seg, document.getElementById("top-title").nextSibling);
    master = window.AiMaster.create({
      container: sheet, bridge, program, onClose: () => setMode(false),
      openUrl: openExternal, subscribeUrl: SUBSCRIBE_URL,
    });
  }

  function setMode(on) {
    ensureUi();
    mode = on;
    document.body.classList.toggle("ai-mode", on);
    seg.hidden = !on;
    if (toggle) toggle.hidden = on || !available;
    if (!on) { clearSpot(); pending = []; document.body.classList.remove("ai-chat-shown"); return Promise.resolve(null); }
    setTab("ai");
    const events = queued;
    queued = [];
    return master.open().then((status) => {
      events.forEach((item) => master.event(item.name, item.detail));
      return status;
    });
  }

  function mountButton() {
    const log = document.getElementById("top-log");
    if (!log || toggle) return;
    toggle = make("button", "ai-top-btn");
    toggle.type = "button";
    toggle.id = "ai-toggle";
    toggle.setAttribute("aria-label", "Установка с ИИ");
    toggle.append(make("span", "ai-top-mark", "✦"), make("span", "", "ИИ"));
    toggle.hidden = true;
    toggle.addEventListener("click", () => setMode(true));
    log.parentElement.insertBefore(toggle, log);
  }

  async function refreshAvailability() {
    let data;
    try { data = await call("status"); } catch (e) { return; }
    available = !!(data && data.enabled);
    if (toggle) toggle.hidden = mode || !available;
    if (!available && mode) setMode(false);
    else if (mode) master.refresh();
  }

  // Системная «Назад» (app.js: __handleBackPress): открыт чат — показать программу; дальше — обычная «Назад».
  function back() {
    if (!mode || tab !== "ai") return false;
    setTab("program");
    return true;
  }

  function init() {
    if (!window.AiMaster || !window.__aiWizard) return;
    mountButton();
    window.events.on("ai_reply", (event) => answer(event, event.body || null));
    window.events.on("ai_shell_result", (event) => answer(event, { ok: !!event.ok, output: String(event.output || "") }));
    ["auth_login_result", "auth_logout_result", "auth_subscriber_result"].forEach((kind) => {
      window.events.on(kind, () => refreshAvailability());
    });
    // События мастера (app.js: aiNotify) — ИИ; пока чат выключен, копим последние.
    window.addEventListener("magicsqd-ai", (event) => {
      const item = event.detail || {};
      if (seg) seg.querySelector(".ai-seg-program").textContent = programLabel();
      if (mode) master.event(item.name, item.detail);
      else queued = queued.concat([item]).slice(-12);
    });
    refreshAvailability();
  }

  window.aiPhone = { back, open: () => setMode(true), isAvailable: () => available };
  document.addEventListener("DOMContentLoaded", init);
})();
