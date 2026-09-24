// «Откатить в сток» (владелец, 2026-09-25, вариант 1 макета): в итоге этапа, поставившего приложения, — жёлтая
// плашка «Не отключайте телефон/компьютер…» и кнопка отката; подтверждение в том же окне со списком; окно
// удаления и его итог. Общий stage_run.js (копии ПК/Android сверяет tests/test_shared_frontend_copies.py).
"use strict";
const { read, assert, run } = require("./_util");

function makeDocument() {
  class Node {
    constructor(tag) {
      this.tagName = String(tag).toUpperCase();
      this.children = []; this.parent = null; this.attrs = {}; this.dataset = {}; this._text = "";
      this.className = ""; this.style = {}; this.disabled = false;
      const self = this;
      this.classList = {
        add: (...c) => { self.className = [...new Set([...self.className.split(" ").filter(Boolean), ...c])].join(" "); },
        remove: (...c) => { self.className = self.className.split(" ").filter((x) => x && !c.includes(x)).join(" "); },
        contains: (c) => self.className.split(" ").includes(c),
      };
    }
    set textContent(v) { this.children = []; this._text = String(v); }
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
    append(...nodes) { nodes.forEach((node) => { if (node) { node.remove(); node.parent = this; this.children.push(node); } }); }
    prepend(...nodes) { nodes.reverse().forEach((node) => { node.remove(); node.parent = this; this.children.unshift(node); }); }
    before(node) { node.remove(); const i = this.parent.children.indexOf(this); node.parent = this.parent; this.parent.children.splice(i, 0, node); }
    replaceWith(node) { const parent = this.parent; const i = parent.children.indexOf(this); node.remove(); node.parent = parent; parent.children[i] = node; this.parent = null; }
    replaceChildren(...nodes) { this.children.forEach((c) => { c.parent = null; }); this.children = []; this._text = ""; this.append(...nodes); }
    remove() { if (this.parent) { this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; } }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    addEventListener() {}
    focus() {}
    get isConnected() { return true; }
    all() { return this.children.flatMap((c) => [c, ...c.all()]); }
    querySelectorAll(selector) {
      if (selector.startsWith(":scope > .")) return this.children.filter((c) => c.classList.contains(selector.slice(10)));
      if (selector.startsWith(".") && !selector.includes(" ")) return this.all().filter((c) => c.classList.contains(selector.slice(1)));
      if (selector.startsWith("button")) return this.all().filter((c) => c.tagName === "BUTTON" && !c.disabled);
      return [];
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  }
  const body = new Node("body");
  return {
    body, activeElement: null,
    createElement: (tag) => new Node(tag),
    createTextNode: (text) => { const node = new Node("#text"); node._text = String(text); return node; },
    addEventListener() {}, removeEventListener() {}, querySelector: () => null,
  };
}

function load() {
  const document = makeDocument();
  const window = { LabUI: { busy: (host) => { const status = document.createElement("div"); host.append(status); return status; } } };
  const ctx = run(read("app/web/frontend/js/components/user_errors.js") + "\n" +
    read("app/web/frontend/js/components/stage_run.js"), { window, document, navigator: {}, setTimeout });
  return { StageRun: ctx.window.StageRun, document };
}

const find = (node, cls) => node.all().find((c) => c.classList.contains(cls));
const buttons = (node) => node.all().filter((c) => c.tagName === "BUTTON");
const APPS = [{ package: "ru.mehanik88.gmarket", name: "GStore 2.0.0", path: "/apk/GStore.apk" },
              { package: "org.jarvis", name: "Jarvis", path: "/apk/jarvis.apk" }];

module.exports = async function () {
  // 1) Итог этапа с поставленными приложениями: плашка над списком, «Откатить в сток» перед «Продолжить».
  {
    const { StageRun, document } = load();
    let asked = null;
    const w = StageRun.open({ title: "Установка приложений" });
    w.finish({ success: true, message: "Все выбранные приложения установлены.",
      rollback: { apps: APPS, device: "телефон", onRollback: (apps) => { asked = apps; } } });
    const view = find(document.body, "stage-run-result");
    const note = find(view, "stage-run-rollback-note");
    assert(note && note.textContent.startsWith("Не отключайте телефон.") && /магнитола работает/.test(note.textContent),
      "плашка: " + (note && note.textContent));
    assert(JSON.stringify(buttons(view).map((b) => b._text)) === JSON.stringify(["Откатить в сток", "Продолжить"]), "кнопки");

    buttons(view)[0].onclick();
    const confirm = find(view, "stage-run-rollback-confirm");
    assert(confirm && /GStore 2\.0\.0, Jarvis/.test(confirm.textContent) && !find(view, "stage-run-rollback-note"), "подтверждение со списком");
    assert(JSON.stringify(buttons(view).map((b) => b._text)) === JSON.stringify(["Удалить 2 приложения", "Отмена"]), "кнопки подтверждения");
    buttons(view)[1].onclick();  // «Отмена» — как было
    assert(find(view, "stage-run-rollback-note") && !find(view, "stage-run-rollback-confirm"), "«Отмена» вернула плашку");
    assert(buttons(view).map((b) => b._text).join() === "Откатить в сток,Продолжить", "и кнопки");
    buttons(view)[0].onclick();
    buttons(view)[0].onclick();  // «Удалить 2 приложения»
    assert(asked === APPS, "откат получил ровно поставленное");
  }
  // 2) Нечего откатывать — окно как раньше; на ПК — «компьютер»; и при ошибке этапа кнопка тоже есть.
  {
    const { StageRun, document } = load();
    StageRun.open({ title: "Установка приложений" }).finish({ success: true, rollback: { apps: [], onRollback() {} } });
    assert(!find(document.body, "stage-run-rollback-note"), "без поставленного — без плашки");
    const again = load();
    again.StageRun.open({ title: "Установка приложений" }).finish({ success: false, message: "Ошибка установки: x",
      rollback: { apps: APPS, device: "компьютер", onRollback() {} } });
    const note = find(again.document.body, "stage-run-rollback-note");
    assert(note && note.textContent.startsWith("Не отключайте компьютер."), "ПК и сбой этапа: плашка есть");
    assert(buttons(find(again.document.body, "stage-run-result"))[0]._text === "Откатить в сток", "кнопка первой");
  }
  // 3) Итог отката — тексты для техника.
  {
    const { StageRun } = load();
    const ok = StageRun.rollbackOutcome({ removed: ["org.jarvis", "ru.mehanik88.gmarket"], manual: [], failed: [] }, APPS);
    assert(ok.success && !ok.partial && ok.message === "Удалено: Jarvis, GStore 2.0.0.", ok.message);
    const hand = StageRun.rollbackOutcome({ removed: [], manual: ["org.jarvis"], failed: [] }, APPS);
    assert(hand.success && hand.partial && /не даёт удалять/.test(hand.message) && /Настройки → Приложения\): Jarvis\./.test(hand.message), hand.message);
    const gone = StageRun.rollbackOutcome({ removed: ["org.jarvis"], manual: [], failed: ["ru.mehanik88.gmarket"], not_connected: true }, APPS);
    assert(!gone.success && gone.plain && /не удалены: GStore 2\.0\.0/.test(gone.message) && /«Повторить»/.test(gone.message)
      && /Удалено: Jarvis\./.test(gone.message), gone.message);
  }
  // 4) Окно удаления: свои заголовки итога, ошибка отката — не окно «что сделать» и с «Повторить».
  {
    const { StageRun, document } = load();
    let retried = 0;
    const w = StageRun.openRollback(APPS, { stageIndex: 3, retry: () => { retried++; } });
    w.finish(StageRun.rollbackOutcome({ removed: [], manual: [], failed: ["org.jarvis"], not_connected: true }, APPS));
    const view = find(document.body, "stage-run-result");
    assert(view.dataset.state === "error" && find(view, "stage-run-title")._text === "Откат не завершён", "заголовок ошибки отката");
    assert(buttons(view).some((b) => b._text === "Повторить"), "«Повторить» есть");
    buttons(view).find((b) => b._text === "Повторить").onclick();
    await new Promise((resolve) => setTimeout(resolve, 5));
    assert(retried === 1, "«Повторить» запускает откат заново");
    const done = load();
    done.StageRun.openRollback(APPS, {}).finish(done.StageRun.rollbackOutcome({ removed: ["org.jarvis"], manual: [], failed: [] }, APPS));
    assert(find(done.document.body, "stage-run-title")._text === "Откат выполнен", "заголовок успеха");
    assert(buttons(find(done.document.body, "stage-run-result")).map((b) => b._text).join() === "Закрыть", "итог отката — «Закрыть»");
    const hand = load();
    hand.StageRun.openRollback(APPS, {}).finish(hand.StageRun.rollbackOutcome({ removed: [], manual: ["org.jarvis"], failed: [] }, APPS));
    assert(find(hand.document.body, "stage-run-title")._text === "Удалите вручную", "заголовок «вручную»");
  }
};
