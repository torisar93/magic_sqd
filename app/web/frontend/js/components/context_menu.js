// Общее контекстное меню правой кнопкой — вынесено из admin_file_manager.js
// (там был долгий бой за правильное позиционирование), теперь переиспользуется
// и логом установки (см. app.js: log-panel contextmenu). Стили — .context-menu*
// в css/components.css.
window.contextMenu = (() => {
  const { el } = window.dom;

  // container — куда добавлять узел меню. Если открыт <dialog> (showModal()
  // рисуется в отдельном top layer браузера, всегда поверх обычного body) —
  // ОБЯЗАТЕЛЬНО передавать сам этот <dialog>, иначе меню, добавленное в body,
  // физически оказывается ПОД диалогом и невидимым (реальный баг — обработчики
  // срабатывали, меню создавалось, просто не показывалось). Для обычного
  // содержимого главного окна (не в диалоге) передавайте document.body.
  function show(x, y, items, container) {
    close();
    const menu = el("div", { class: "context-menu" });
    for (const item of items) {
      if (item === "sep") {
        menu.appendChild(el("div", { class: "context-menu-sep" }));
        continue;
      }
      menu.appendChild(el("div", {
        class: "context-menu-item" + (item.danger ? " danger" : ""),
        text: item.label,
        onclick: () => { close(); item.onclick(); },
      }));
    }
    container.appendChild(menu);
    // <dialog>/любой контейнер с transform/filter сам становится containing
    // block для position:fixed потомков — координаты x/y (clientX/clientY, от
    // viewport) нужно пересчитать относительно РАМКИ КОНТЕЙНЕРА, а не окна,
    // иначе меню уезжает далеко от курсора (реальный баг). Для document.body
    // (обычный поток, не top layer) containerRect эквивалентен viewport —
    // поправка просто ничего не меняет, безопасно использовать всегда.
    const containerRect = container.getBoundingClientRect();
    const localX = x - containerRect.left;
    const localY = y - containerRect.top;
    const rect = menu.getBoundingClientRect();
    const maxX = containerRect.width - rect.width - 8;
    const maxY = containerRect.height - rect.height - 8;
    menu.style.left = `${Math.min(localX, maxX)}px`;
    menu.style.top = `${Math.min(localY, maxY)}px`;
    window._activeContextMenu = menu;
    // Только "клик мимо" закрывает меню — НЕ "следующий contextmenu": этот же
    // обработчик, зарегистрированный предыдущим открытием, до этого закрывал
    // уже ОТКРЫВШЕЕСЯ здесь новое меню (правый клик всегда сам создаёт
    // contextmenu, событие бы бублилось до document и мгновенно сносило
    // меню, которое только что открыл этот же клик). Новый contextmenu и так
    // уже закрывает старое меню — close() в начале этой функции.
    setTimeout(() => {
      document.addEventListener("click", close, { once: true });
    }, 0);
  }

  function close() {
    if (window._activeContextMenu) {
      window._activeContextMenu.remove();
      window._activeContextMenu = null;
    }
  }

  return { show, close };
})();
