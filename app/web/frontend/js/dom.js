// Полифилл Element.prototype.replaceChildren (DOM, Chromium 86+) — старый
// Chromium в Qt5/PyQt5 (Win7-сборка, см. installer_win7_x86.iss) его не
// знает и падает TypeError'ом на каждом вызове. Тот же класс проблемы, что
// и с ||=/&&=/??= (см. events.js) — новый интерфейс использует его
// повсеместно, полифилл проще и безопаснее, чем переписывать каждый вызов.
if (typeof Element !== "undefined" && !Element.prototype.replaceChildren) {
  Element.prototype.replaceChildren = function (...nodes) {
    while (this.firstChild) this.removeChild(this.firstChild);
    if (nodes.length) this.append(...nodes);
  };
}

// Фолбэк для :has() (Chromium 105+) — старый Chromium в Qt5/PyQt5 (Win7)
// его не понимает вообще, поэтому все правила вида
// "#picker:has(.cat-grid[data-level=model])" (ui08.css/catalog11.css)
// молча не применяются: цепочка #picker/.cat-screen никогда не получает
// height:100%, и .model-hero (flex:1) раздувается по контенту вместо
// того чтобы вписаться в высоту окна — это и есть тот самый "рендерится
// вне экрана" баг. Раз :has() не тянется, даём тот же сигнал явным
// классом на тех же узлах, что проверяет :has(); правила в CSS матчат
// и по :has(), и по этому классу — на современных движках класс просто
// не появляется и ничего не меняет.
if (!(window.CSS?.supports?.('selector(:has(*))'))) {
  // Аварийный режим для того же старого движка (см. css/win7-safe-mode.css):
  // анимации/переходы отключены целиком, диалоги центрируются жёстко,
  // иконки марок показываются сразу без reveal/metallic-эффекта. По
  // требованию владельца — на этом движке "работает" важнее "красиво".
  document.documentElement.classList.add("win7-safe-mode");

  const syncHasModelGrid = () => {
    const grid = document.querySelector('.cat-grid[data-level="model"]');
    document.querySelectorAll("#left-panel, #picker, .cat-screen").forEach((el) => {
      el.classList.toggle("has-model-grid", !!(grid && el.contains(grid)));
    });
  };
  new MutationObserver(syncHasModelGrid).observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["data-level"],
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", syncHasModelGrid);
  else syncHasModelGrid();
}

// Минимальный DOM-хелпер — весь "фреймворк" фронтенда (см. план миграции:
// осознанно без React/Svelte/сборки, один поддерживающий разработчик).
function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined) node.setAttribute(key, value);
  }
  for (const child of [].concat(children)) {
    if (child === null || child === undefined) continue;
    node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

function clear(node) {
  node.innerHTML = "";
}

window.dom = { el, clear };
