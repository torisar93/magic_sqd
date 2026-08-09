// SVG-аналог el() из dom.js — тот же API (tag, attrs, children), но через
// document.createElementNS (dom.js's el() создаёт только через
// document.createElement, что не работает для SVG-элементов — атрибуты
// вроде "d"/"cx"/"r" ставятся, но сам узел не рендерится как SVG-примитив).
// Нужен только графу проводов (graph_wizard.js) — единственное место в
// проекте, где сейчас используется SVG.
(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";

  function svgEl(tag, attrs = {}, children = []) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (key === "class") node.setAttribute("class", value);
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else if (value !== null && value !== undefined) node.setAttribute(key, value);
    }
    for (const child of [].concat(children)) {
      if (child === null || child === undefined) continue;
      node.appendChild(child);
    }
    return node;
  }

  window.svgDom = { svgEl };
})();
