// Минимальный DOM для кольца progress08.js и окна этапа stage_run.js (общие для ПК и Android) — без npm-пакетов.
// Тесты кольца: progress_grant.test.js, ring_flash_write.test.js.
"use strict";

function makeDom() {
  class Node {
    constructor(tag) {
      this.tagName = String(tag).toUpperCase();
      this.children = []; this.parent = null; this.attrs = {}; this.dataset = {}; this._text = "";
      this.className = ""; this.value = undefined; this.max = undefined;
      const self = this;
      this.style = { props: {}, setProperty(k, v) { this.props[k] = v; }, removeProperty(k) { delete this.props[k]; } };
      this.classList = {
        add: (...c) => { self.className = [...new Set([...self.className.split(" ").filter(Boolean), ...c])].join(" "); },
        remove: (...c) => { self.className = self.className.split(" ").filter((x) => x && !c.includes(x)).join(" "); },
        contains: (c) => self.className.split(" ").includes(c),
      };
    }
    set textContent(v) { this.children = []; this._text = String(v); }
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
    set innerHTML(v) { this._html = v; }
    get isConnected() { return true; }
    append(...nodes) { nodes.forEach((node) => { if (node) { node.remove(); node.parent = this; this.children.push(node); } }); }
    prepend(...nodes) { nodes.reverse().forEach((node) => { node.remove(); node.parent = this; this.children.unshift(node); }); }
    remove() { if (this.parent) { this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; } }
    replaceWith(node) { const p = this.parent; const i = p.children.indexOf(this); node.remove(); node.parent = p; p.children[i] = node; this.parent = null; }
    replaceChildren(...nodes) { this.children.forEach((c) => { c.parent = null; }); this.children = []; this._text = ""; this.append(...nodes); }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    removeAttribute(k) { delete this.attrs[k]; if (k === "value") this.value = undefined; }
    addEventListener() {}
    focus() {}
    all() { return this.children.flatMap((c) => [c, ...c.all()]); }
    matches(simple) { return simple.startsWith(".") ? this.classList.contains(simple.slice(1)) : this.tagName === simple.toUpperCase(); }
    // Только формы, что есть в progress08.js и stage_run.js: «.a», «tag», «.a>.b», «.a tag», «:scope > .a».
    querySelectorAll(selector) {
      if (selector.startsWith(":scope > ")) return this.children.filter((c) => c.matches(selector.slice(9)));
      if (selector.includes(">")) {
        const [outer, inner] = selector.split(">");
        return this.querySelectorAll(outer).flatMap((node) => node.children.filter((c) => c.matches(inner)));
      }
      if (selector.includes(" ")) {
        const [outer, inner] = selector.split(" ");
        return this.querySelectorAll(outer).flatMap((node) => node.all().filter((c) => c.matches(inner)));
      }
      return this.all().filter((c) => c.matches(selector));
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  }
  const body = new Node("body");
  const document = {
    body, activeElement: null,
    createElement: (tag) => new Node(tag),
    createTextNode: (text) => { const node = new Node("#text"); node._text = String(text); return node; },
    querySelectorAll: (selector) => body.querySelectorAll(selector),
    querySelector: (selector) => body.querySelector(selector),
    addEventListener() {}, removeEventListener() {},
  };
  const n = (tag, cls, text) => { const node = new Node(tag); if (cls) node.className = cls; if (text != null) node.textContent = text; return node; };
  const LabUI = { n, symbol: () => n("i", "ui-icon"), appIcon: () => n("img", "app-icon") };
  return { document, LabUI, body };
}

module.exports = { makeDom };
