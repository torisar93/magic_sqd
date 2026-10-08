// ИИ-мастер — общее ядро чата (app/web/frontend/js/ai_master.js = Android-копия): протокол с сервером, действия и
// карточки, обрывы связи. Свой маленький DOM — без npm-пакетов, как остальные JS-тесты.
"use strict";
const { read, assert, run, sleep } = require("./_util");

class Text {
  constructor(t) { this.nodeType = 3; this._t = String(t); this.parentNode = null; }
  get textContent() { return this._t; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
}

class El {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null; this.className = "";
    this.hidden = false; this.disabled = false; this.listeners = {}; this.scrollTop = 0; this.attrs = {};
    const self = this;
    this.classList = {
      add: (c) => { if (!self.classList.contains(c)) self.className = (self.className + " " + c).trim(); },
      remove: (c) => { self.className = self.className.split(" ").filter((x) => x !== c).join(" "); },
      contains: (c) => self.className.split(" ").includes(c),
      toggle: (c, on) => { (on === undefined ? !self.classList.contains(c) : on) ? self.classList.add(c) : self.classList.remove(c); },
    };
  }
  get scrollHeight() { return 1000; }
  set textContent(v) { this.children = []; if (v !== "") this.appendChild(new Text(v)); }
  get textContent() { return this.children.map((c) => c.textContent).join(""); }
  set innerHTML(v) { this.children.forEach((c) => { c.parentNode = null; }); this.children = []; }
  appendChild(n) { if (n.parentNode) n.parentNode.removeChild(n); n.parentNode = this; this.children.push(n); return n; }
  insertBefore(n, ref) {
    if (n.parentNode) n.parentNode.removeChild(n);
    const i = this.children.indexOf(ref); n.parentNode = this;
    if (i < 0) this.children.push(n); else this.children.splice(i, 0, n);
    return n;
  }
  removeChild(n) { this.children = this.children.filter((c) => c !== n); n.parentNode = null; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  dispatch(type, extra) { (this.listeners[type] || []).forEach((fn) => fn(Object.assign({ preventDefault() {} }, extra || {}))); }
  click() { if (!this.disabled) this.dispatch("click"); }
  focus() {}
  all() { return this.children.filter((c) => c instanceof El).flatMap((c) => [c, ...c.all()]); }
  querySelectorAll(tag) { return this.all().filter((c) => c.tagName === tag.toUpperCase()); }
  find(cls) { return this.all().filter((c) => c.classList.contains(cls)); }
  buttons(label) { return this.all().filter((c) => c.tagName === "BUTTON" && c.textContent === label); }
}

function setup(responses, programOverrides) {
  const calls = { status: 0, start: [], turn: [], shell: [], focus: [], highlight: [], press: [], open: [], apps: [], urls: [] };
  let stage = 1;
  const bridge = {
    status: async () => { calls.status += 1; return responses.status || { status: "ok", used: 0, limit: 10 }; },
    start: async (p) => { calls.start.push(p); return responses.start || { status: "ok", session: "S1", resumed: false, usage: { used: 1, limit: 10 } }; },
    turn: async (p) => {
      calls.turn.push(JSON.parse(JSON.stringify(p)));
      const next = responses.turns.shift();
      if (next instanceof Error) throw next;
      return next || { status: "ok", messages: [], actions: [] };
    },
    shell: async (cmd, mode) => { calls.shell.push([cmd, mode]); return { ok: true, output: "28" }; },
  };
  const program = Object.assign({
    modelKey: () => "Haval/H3", title: () => "Haval H3", outline: () => [{ stage: 1, blocks: [{ b: "3", text: "x" }] }],
    state: () => ({ stage: { index: stage }, busy: false }),
    focus: (s, b) => calls.focus.push([s, b]), highlight: (t) => calls.highlight.push(t),
    press: async (c) => { calls.press.push(c); return { ok: true, output: "" }; },
    openModel: async (k) => { calls.open.push(k); return { ok: true }; },
    selectApps: (p, a) => calls.apps.push([p, a]), photo: (s, b) => `img/${s}-${b}.jpg`,
  }, programOverrides || {});
  const document = { createElement: (t) => new El(t), createTextNode: (t) => new Text(t) };
  const ctx = run(read("app/web/frontend/js/ai_master.js"), { window: {}, document, setTimeout, clearTimeout, console });
  const container = new El("div");
  const ai = ctx.window.AiMaster.create({ container, bridge, program, subscribeUrl: "https://boosty.to/x",
                                          openUrl: (u) => calls.urls.push(u) });
  return { ai, container, calls, setStage: (v) => { stage = v; } };
}

const tick = () => sleep(5);

async function testSubscribeLock() {
  const { ai, container, calls } = setup({ status: { status: "subscribe", limit: 10 }, turns: [] });
  await ai.open();
  const sub = container.buttons("Подписаться");
  assert(sub.length === 1, "замок с одной кнопкой «Подписаться»");
  assert(container.textContent.includes("10 установок с ИИ в день"), "лимит в тексте замка");
  sub[0].click();
  assert(calls.urls[0] === "https://boosty.to/x", "открыли Boosty");
  assert(container.find("ai-input")[0].hidden, "без ввода");
}

async function testStartTurnAndActions() {
  const { ai, container, calls } = setup({ turns: [
    { status: "ok", messages: [{ text: "Смотрите **блок 3** [[фото:1/3]]", chips: ["Готово"] }],
      actions: [{ id: "a1", tool: "focus_block", mode: "auto", args: { stage: 1, block: "3" } },
                { id: "a2", tool: "select_apps", mode: "auto", args: { picks: [{ path: "apk/x.apk", why: "y" }], avoid: [] } },
                { id: "a3", tool: "shell", mode: "auto", args: { cmd: "getprop ro.build.version.sdk", why: "версия" } }],
      usage: { used: 1, limit: 10 } },
    { status: "ok", messages: [{ text: "Android 9." }], actions: [] },
  ] });
  await ai.open();
  container.buttons("Начать установку")[0].click();
  await tick();
  assert(calls.start.length === 1 && calls.start[0].model_key === "Haval/H3" && calls.start[0].outline.length === 1, "старт с оглавлением");
  assert(calls.turn[0].input[0].text === "Начать установку" && calls.turn[0].session === "S1", "первый ход — текст");
  assert(calls.focus[0][0] === 1 && calls.focus[0][1] === "3", "инструкция прокручена");
  assert(calls.apps.length === 1, "совет по приложениям");
  assert(calls.shell[0][1] === "auto", "читающая команда — сама");
  assert(calls.turn.length === 2 && calls.turn[1].input[0].type === "result" && calls.turn[1].input[0].id === "a3"
         && calls.turn[1].input[0].output === "28", "результат команды ушёл следующим ходом");
  assert(container.find("ai-photo").length === 1 && container.textContent.includes("Сегодня 1 из 10"), "фото и лимит");
  assert(container.all().some((n) => n.tagName === "B" && n.textContent === "блок 3"), "жирный текст");
}

async function testPressCardYesAndSelf() {
  const { ai, container, calls, setStage } = setup({ turns: [
    { status: "ok", messages: [], actions: [{ id: "p1", tool: "press", mode: "confirm", args: { control: "next" } }] },
    { status: "ok", messages: [], actions: [{ id: "p2", tool: "press", mode: "confirm", args: { control: "connect" } }] },
    { status: "ok", messages: [], actions: [] },
  ] });
  await ai.open();
  ai.send("дальше");
  await tick();
  assert(calls.highlight.includes("next"), "кнопка подсвечена под карточкой");
  container.buttons("Да, нажми")[0].click();
  await tick();
  assert(calls.press[0] === "next", "нажали по «Да»");
  const yes = calls.turn[1].input[0];
  assert(yes.type === "card" && yes.id === "p1" && yes.answer === "yes" && yes.ok === true, "ответ карточки ушёл");
  container.buttons("Нажму сам").slice(-1)[0].click();
  await tick();
  assert(calls.turn.length === 2, "«Нажму сам» ждёт события программы");
  setStage(2);
  ai.event("stage_changed", "этап 2");
  await tick();
  assert(calls.turn.length === 3 && calls.turn[2].input.map((i) => i.answer || i.name).join() === "self,stage_changed",
         "ответ «сам» ушёл вместе с событием");
}

async function testTypedYesOpensModelAndStaleCard() {
  // «да» текстом — ответ на последнюю карточку; модель открылась по карточке — ИИ продолжает в установке новой модели
  const ref = { key: "Haval/H3" };
  const { ai, calls, setStage } = setup({ turns: [
    { status: "ok", messages: [], actions: [{ id: "c1", tool: "open_model", mode: "confirm", args: { model: "Haval/H7" } }] },
    { status: "ok", messages: [], actions: [{ id: "c2", tool: "press", mode: "confirm", args: { control: "next" } }] },
    { status: "ok", messages: [], actions: [] },
  ] }, {
    modelKey: () => ref.key,
    openModel: async (k) => { ref.opened = k; ref.key = k; ref.ai.event("model_opened", "Haval H7"); return { ok: true, output: "" }; },
  });
  ref.ai = ai;
  await ai.open();
  ai.send("это h7");
  await tick();
  ai.send("да");
  await tick();
  assert(ref.opened === "Haval/H7", "модель открыта по «да» текстом");
  assert(calls.start.length === 2 && calls.start[1].model_key === "Haval/H7", "установка новой модели — новый start");
  assert(calls.turn.length === 2 && calls.turn[1].input.length === 1 && calls.turn[1].input[0].name === "model_opened",
         "ИИ продолжил по событию, без лишнего ответа карточки: " + JSON.stringify(calls.turn[1] && calls.turn[1].input));
  setStage(3);  // этап сменился до ответа на карточку «Далее»
  ai.event("stage_changed", "этап 3");
  await tick();
  const sent = calls.turn[2].input.find((i) => i.type === "card");
  assert(sent && sent.id === "c2" && sent.answer === "stale" && calls.press.length === 0, "устаревшая карточка не нажимает");
}

async function testOwnModelSwitchWaitsForTechnician() {
  // другую модель техник открыл сам — новую установку с ИИ (одну из дневных) не начинаем без его слова
  const ref = { key: "Haval/H3" };
  const { ai, container, calls } = setup({ turns: [
    { status: "ok", messages: [], actions: [{ id: "s1", tool: "shell", mode: "confirm", args: { cmd: "settings put global x 1" } }] },
    { status: "ok", messages: [{ text: "Начнём с H7." }], actions: [] },
  ] }, { modelKey: () => ref.key, title: () => (ref.key === "Haval/H7" ? "Haval H7" : "Haval H3") });
  await ai.open();
  ai.send("привет");
  await tick();
  ref.key = "Haval/H7";
  ai.event("model_opened", "Haval H7");
  await tick();
  assert(calls.start.length === 1 && calls.turn.length === 1, "сама ничего не отправила");
  assert(container.textContent.includes("Устарело — открыта другая модель."), "карточка прежней модели снята");
  container.buttons("Начать установку с ИИ")[0].click();
  await tick();
  assert(calls.start.length === 2 && calls.start[1].model_key === "Haval/H7", "по слову техника — start новой модели");
  assert(calls.turn[1].input.length === 1 && calls.turn[1].input[0].text === "Начать установку с ИИ", "в ходе — только его текст");
}

async function testPressThatChangesStageIsYes() {
  // «Далее» сам сменил этап — это не «устарело», а «Да»; обычное событие ждёт следующего хода
  const ref = {};
  const { ai, container, calls, setStage } = setup({ turns: [
    { status: "ok", messages: [], actions: [{ id: "p1", tool: "press", mode: "confirm", args: { control: "next" } }] },
    { status: "ok", messages: [], actions: [] },
    { status: "ok", messages: [], actions: [] },
  ] }, { press: async (c) => { ref.setStage(2); ref.ai.event("stage_changed", "этап 2"); return { ok: true, output: "Открыт этап 2" }; } });
  ref.ai = ai;
  ref.setStage = setStage;
  await ai.open();
  ai.send("дальше");
  await tick();
  container.buttons("Да, нажми")[0].click();
  await tick();
  const inputs = calls.turn[1].input;
  assert(inputs.some((i) => i.type === "card" && i.answer === "yes") && !inputs.some((i) => i.answer === "stale"),
         "нажатие засчитано: " + JSON.stringify(inputs));
  assert(inputs.some((i) => i.type === "event" && i.name === "stage_changed") && calls.turn.length === 2, "одним ходом");
  ai.event("stage_changed", "этап 3");
  await tick();
  assert(calls.turn.length === 2, "обычное событие само ход не запускает");
  ai.send("ок");
  await tick();
  assert(calls.turn[2].input.map((i) => i.name || i.text).join() === "stage_changed,ок", "ушло со словом техника");
}

async function testOfflineKeepsInputAndErrorRetry() {
  const { ai, container, calls } = setup({ turns: [
    new Error("сеть"),
    { status: "error", error: "ИИ сейчас недоступен" },
    { status: "ok", messages: [{ text: "Снова на связи." }], actions: [] },
  ] });
  await ai.open();
  ai.send("алло");
  await tick();
  assert(container.textContent.includes("Нет интернета"), "предупреждение о связи");
  await sleep(3200);  // первый повтор через 3 с
  assert(calls.turn.length === 2 && calls.turn[1].input[0].text === "алло", "ввод не потерян — ушёл повтором");
  const retry = container.buttons("Повторить");
  assert(retry.length === 1, "после ошибки ИИ — кнопка «Повторить»");
  retry[0].click();
  await tick();
  assert(calls.turn.length === 3 && calls.turn[2].input.length === 0, "повтор без ввода (сервер его сохранил)");
  assert(container.textContent.includes("Снова на связи."), "ответ после повтора");
}

function testRenderText() {
  const document = { createElement: (t) => new El(t), createTextNode: (t) => new Text(t) };
  const ctx = run(read("app/web/frontend/js/ai_master.js"), { window: {}, document, setTimeout, clearTimeout, console });
  const box = new El("div");
  const photos = [];
  ctx.window.AiMaster.renderText(box, "Шаги:\n- первый `pm list`\n- второй [[фото:2/7.3]]\n\nКонец <script>", (s, b) => {
    photos.push(`${s}/${b}`); return new El("figure");
  });
  assert(box.querySelectorAll("li").length === 2 && box.querySelectorAll("code")[0].textContent === "pm list", "список и код");
  assert(photos[0] === "2/7.3" && !box.textContent.includes("[[фото"), "метка фото → фото");
  assert(box.textContent.includes("Конец <script>") && box.querySelectorAll("script").length === 0, "HTML не исполняется");
}

module.exports = async function () {
  testRenderText();
  await testSubscribeLock();
  await testStartTurnAndActions();
  await testPressCardYesAndSelf();
  await testTypedYesOpensModelAndStaleCard();
  await testOwnModelSwitchWaitsForTechnician();
  await testPressThatChangesStageIsYes();
  await testOfflineKeepsInputAndErrorRetry();
};
