// ==================================================================
// Единый файловый менеджер (только admin_mode, открывается из js/app.js) —
// см. app/web/api/admin_api.py: browse_tree/delete_tree_path/move_path,
// server/backend.py: GET/DELETE /admin/api/browse, POST /admin/api/move.
// Заменяет старый диалог "Файлы на сервере" (только просмотр/удаление,
// только cars/) — теперь одно дерево на cars/ И apk/, плюс перенос/
// переименование через drag-and-drop (тот же паттерн, что и в веб-админке
// на сайте, server/admin/index.html — HTML5 DnD внутри pywebview работает
// так же, это чисто DOM-перетаскивание, не файлы ОС). Сетка иконок вместо
// табличных строк (пользователь явно попросил вид как в обычном файловом
// менеджере ОС) — все действия с файлом/папкой только через контекстное
// меню правой кнопкой (см. .context-menu ниже), а не колонку с кнопками.
// ==================================================================
(() => {
  const { el, clear } = window.dom;

  const FOLDER_ICON_SVG =
    '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18a2 2 0 0 0 2 2h16a2 2 0 0 0 ' +
    '2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/></svg>';
  const FILE_ICON_SVG =
    '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 2c-1.1 0-1.99.9-1.99 2L4 20a2 2 0 0 0 2 2h12a2 2 0 0 0 ' +
    '2-2V8l-6-6H6zm7 7V3.5L18.5 9H13z"/></svg>';

  // Простое контекстное меню общего назначения — позиционируется под
  // курсором, закрывается кликом мимо/Escape/скроллом диалога. container —
  // куда добавлять узел меню: ОБЯЗАТЕЛЬНО сам открытый <dialog>, а не
  // document.body — модальный showModal() рисуется в отдельном top layer
  // браузера, который всегда поверх обычного body; меню, добавленное в
  // body, было бы физически ПОД диалогом и невидимым (реальный баг —
  // обработчики срабатывали, меню создавалось, но не показывалось).
  function showContextMenu(x, y, items, container) {
    closeContextMenu();
    const menu = el("div", { class: "context-menu" });
    for (const item of items) {
      if (item === "sep") {
        menu.appendChild(el("div", { class: "context-menu-sep" }));
        continue;
      }
      menu.appendChild(el("div", {
        class: "context-menu-item" + (item.danger ? " danger" : ""),
        text: item.label,
        onclick: () => { closeContextMenu(); item.onclick(); },
      }));
    }
    container.appendChild(menu);
    // <dialog> с backdrop-filter (см. css: dialog {}) сам становится
    // containing block для position:fixed/absolute потомков (то же самое,
    // что и transform/filter) — координаты x/y (clientX/clientY, от
    // viewport) нужно пересчитать относительно РАМКИ ДИАЛОГА, а не окна,
    // иначе меню уезжает далеко от курсора (реальный баг — обработчики
    // срабатывали и позиционировали правильно математически, просто не в
    // той системе координат).
    const containerRect = container.getBoundingClientRect();
    const localX = x - containerRect.left;
    const localY = y - containerRect.top;
    const rect = menu.getBoundingClientRect();
    const maxX = containerRect.width - rect.width - 8;
    const maxY = containerRect.height - rect.height - 8;
    menu.style.left = `${Math.min(localX, maxX)}px`;
    menu.style.top = `${Math.min(localY, maxY)}px`;
    window._activeContextMenu = menu;
    // Только "клик мимо" закрывает меню — НЕ "следующий contextmenu": этот
    // же самый обработчик, зарегистрированный предыдущим открытием меню, до
    // этого закрывал уже ОТКРЫВШЕЕСЯ здесь новое меню (правый клик всегда
    // сам создаёт contextmenu, событие бы бублилось до document и мгновенно
    // сносило меню, которое только что открыл этот же клик — второе правое
    // нажатие подряд визуально выглядело как "меню вообще не показалось").
    // Новый contextmenu и так уже закрывает старое меню — closeContextMenu()
    // в начале этой функции.
    setTimeout(() => {
      document.addEventListener("click", closeContextMenu, { once: true });
    }, 0);
  }

  function closeContextMenu() {
    if (window._activeContextMenu) {
      window._activeContextMenu.remove();
      window._activeContextMenu = null;
    }
  }

  const adminFileManager = (() => {
    let dialog, breadcrumbEl, listEl, upBtn, rootCarsBtn, rootApkBtn, clipboardStatusEl;
    let root = "cars";
    let path = "";
    let draggedName = null;
    // Буфер "Копировать"/"Переместить" -> "Вставить" (Explorer-стиль) —
    // { root, relPath, name, mode: "copy"|"move" }. Вставка доступна только
    // внутри того же root (cars/apk — структурно разные деревья, сервер и
    // так отказал бы, см. server/backend.py:_handle_copy/_handle_move).
    let clipboard = null;

    function init() {
      dialog = document.getElementById("admin-manager-dialog");
      breadcrumbEl = document.getElementById("admin-manager-breadcrumb");
      listEl = document.getElementById("admin-manager-list");
      upBtn = document.getElementById("admin-manager-up");
      rootCarsBtn = document.getElementById("admin-manager-root-cars");
      rootApkBtn = document.getElementById("admin-manager-root-apk");
      clipboardStatusEl = document.getElementById("admin-manager-clipboard-status");

      rootCarsBtn.addEventListener("click", () => setRoot("cars"));
      rootApkBtn.addEventListener("click", () => setRoot("apk"));
      upBtn.addEventListener("click", () => {
        const parts = path.split("/").filter(Boolean);
        parts.pop();
        load(parts.join("/"));
      });
      document.getElementById("admin-manager-refresh").addEventListener("click", () => load(path));
      document.getElementById("admin-manager-close").addEventListener("click", () => dialog.close());

      // Правый клик по пустому месту сетки (не по карточке) — контекстное
      // меню с "Создать папку" и, если что-то скопировано/вырезано в этом
      // же root, "Вставить" (в текущую папку).
      listEl.addEventListener("contextmenu", (e) => {
        // Клик по самой карточке уже обработан её собственным обработчиком
        // (см. buildCard) и остановлен stopPropagation — сюда доходят только
        // клики мимо карточек. closest(), а не строгое сравнение с listEl:
        // e.target может быть текстовым узлом/промежуточным элементом
        // разметки сетки, а не обязательно самим listEl.
        if (e.target.closest(".manager-card")) return;
        e.preventDefault();
        const menuItems = [{ label: "Создать папку", onclick: onCreateFolder }];
        if (clipboard && clipboard.root === root) {
          menuItems.push("sep");
          menuItems.push({ label: "Вставить", onclick: () => doPaste(path) });
        }
        menuItems.push("sep");
        menuItems.push({ label: "Обновить", onclick: () => load(path) });
        showContextMenu(e.clientX, e.clientY, menuItems, dialog);
      });
    }

    function setClipboard(item, mode) {
      clipboard = { root, relPath: joinPath(path, item.name), name: item.name, mode };
      updateClipboardStatus();
    }

    function clearClipboard() {
      clipboard = null;
      updateClipboardStatus();
    }

    function updateClipboardStatus() {
      clear(clipboardStatusEl);
      if (!clipboard) {
        clipboardStatusEl.hidden = true;
        return;
      }
      clipboardStatusEl.hidden = false;
      const verb = clipboard.mode === "copy" ? "Копирование" : "Перемещение";
      clipboardStatusEl.appendChild(el("span", { text: `${verb}: ${clipboard.name}` }));
      clipboardStatusEl.appendChild(el("span", {
        class: "manager-clipboard-cancel", text: "✕", title: "Отменить", onclick: clearClipboard,
      }));
    }

    async function doPaste(destRelPath) {
      if (!clipboard) return;
      const toRel = joinPath(destRelPath, clipboard.name);
      const apiFn = clipboard.mode === "copy"
        ? window.pywebview.api.admin_copy_path
        : window.pywebview.api.admin_move_path;
      const result = await apiFn(clipboard.root, clipboard.relPath, toRel);
      if (!result.ok) {
        await window.notice(result.error, { title: "Вставить", danger: true });
        return;
      }
      clearClipboard();
      load(path);
    }

    async function onCreateFolder() {
      const name = (await window.promptDialog("Название новой папки:"))?.trim();
      if (!name) return;
      const result = await window.pywebview.api.admin_create_folder(root, joinPath(path, name));
      if (!result.ok) {
        await window.notice(result.error, { title: "Создать папку", danger: true });
        return;
      }
      load(path);
    }

    function setRoot(newRoot) {
      root = newRoot;
      rootCarsBtn.classList.toggle("active", root === "cars");
      rootApkBtn.classList.toggle("active", root === "apk");
      load("");
    }

    function formatSize(bytes) {
      if (bytes == null) return "—";
      if (bytes < 1024) return `${bytes} Б`;
      if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} КБ`;
      return `${(bytes / 1024 ** 2).toFixed(1)} МБ`;
    }

    function formatDate(mtime) {
      if (!mtime) return "—";
      const d = new Date(mtime * 1000);
      const pad = (n) => String(n).padStart(2, "0");
      return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
    }

    function joinPath(base, name) {
      return base ? `${base}/${name}` : name;
    }

    function addDropTarget(node, targetPath) {
      node.addEventListener("dragover", (e) => {
        e.preventDefault();
        node.classList.add("drag-over");
      });
      node.addEventListener("dragleave", () => node.classList.remove("drag-over"));
      node.addEventListener("drop", (e) => {
        e.preventDefault();
        node.classList.remove("drag-over");
        if (draggedName === null) return;
        moveItem(joinPath(path, draggedName), joinPath(targetPath, draggedName));
      });
    }

    function renderBreadcrumb() {
      clear(breadcrumbEl);
      const rootCrumb = el("span", { class: "manager-crumb", text: root + "/", onclick: () => load("") });
      addDropTarget(rootCrumb, "");
      breadcrumbEl.appendChild(rootCrumb);

      let accum = "";
      for (const part of path ? path.split("/") : []) {
        accum = accum ? `${accum}/${part}` : part;
        const crumbPath = accum;
        breadcrumbEl.appendChild(el("span", { class: "manager-crumb-sep", text: "/" }));
        const crumb = el("span", { class: "manager-crumb", text: part, onclick: () => load(crumbPath) });
        addDropTarget(crumb, crumbPath);
        breadcrumbEl.appendChild(crumb);
      }
    }

    function startRename(card, nameEl, item) {
      const input = el("input", { class: "manager-rename-input", value: item.name });
      clear(nameEl);
      nameEl.appendChild(input);
      input.focus();
      input.select();
      let done = false;
      const finish = async (commit) => {
        if (done) return;
        done = true;
        const newName = input.value.trim();
        if (commit && newName && newName !== item.name) {
          await moveItem(joinPath(path, item.name), joinPath(path, newName));
        } else {
          load(path);
        }
      };
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") finish(true);
        if (e.key === "Escape") finish(false);
      });
      input.addEventListener("blur", () => finish(true));
    }

    function buildCard(item) {
      // Размер/дата ушли из видимой строки вместе с табличными колонками —
      // остаются подсказкой при наведении, чтобы совсем не терять эту
      // информацию.
      const title = `${item.name}\n${item.is_dir ? "папка" : formatSize(item.size)} · ${formatDate(item.mtime)}`;
      const card = el("div", { class: "manager-card" + (item.is_dir ? " is-dir" : ""), draggable: "true", title });
      card.addEventListener("dragstart", () => { draggedName = item.name; });
      card.addEventListener("dragend", () => { draggedName = null; });

      card.appendChild(el("div", { class: "manager-card-icon", html: item.is_dir ? FOLDER_ICON_SVG : FILE_ICON_SVG }));
      const nameEl = el("div", { class: "manager-card-name", text: item.name });
      card.appendChild(nameEl);

      if (item.is_dir) {
        card.addEventListener("click", () => load(joinPath(path, item.name)));
      }

      // Все действия с файлом/папкой — только контекстным меню (правая
      // кнопка), пользователь явно попросил убрать отдельные
      // кнопки/двойной клик ради этого.
      const openCardMenu = (e) => {
        e.preventDefault();
        e.stopPropagation();
        const menuItems = [];
        if (item.is_dir) {
          menuItems.push({ label: "Открыть", onclick: () => load(joinPath(path, item.name)) });
          menuItems.push("sep");
        }
        menuItems.push({ label: "Копировать", onclick: () => setClipboard(item, "copy") });
        menuItems.push({ label: "Переместить", onclick: () => setClipboard(item, "move") });
        if (item.is_dir && clipboard && clipboard.root === root) {
          menuItems.push({ label: "Вставить сюда", onclick: () => doPaste(joinPath(path, item.name)) });
        }
        menuItems.push("sep");
        menuItems.push({ label: "Переименовать", onclick: () => startRename(card, nameEl, item) });
        menuItems.push("sep");
        menuItems.push({ label: "Удалить", danger: true, onclick: () => onDelete(item) });
        showContextMenu(e.clientX, e.clientY, menuItems, dialog);
      };
      card.addEventListener("contextmenu", openCardMenu);

      if (item.is_dir) addDropTarget(card, joinPath(path, item.name));
      return card;
    }

    async function onDelete(item) {
      const what = item.is_dir ? "папку со всем содержимым" : "файл";
      if (!(await window.confirmDialog(`Удалить ${what} «${item.name}»? Это необратимо.`))) return;
      const result = await window.pywebview.api.admin_delete_tree_path(root, joinPath(path, item.name));
      if (!result.ok) {
        await window.notice(result.error, { title: "Удаление", danger: true });
        return;
      }
      load(path);
    }

    async function moveItem(fromRel, toRel) {
      if (fromRel === toRel) { load(path); return; }
      const result = await window.pywebview.api.admin_move_path(root, fromRel, toRel);
      if (!result.ok) {
        await window.notice(result.error, { title: "Перенос", danger: true });
      }
      load(path);
    }

    async function load(newPath) {
      const result = await window.pywebview.api.admin_browse_tree(root, newPath);
      if (!result.ok) {
        await window.notice(result.error, { title: "Файловый менеджер", danger: true });
        return;
      }
      path = newPath;
      upBtn.disabled = !path;
      renderBreadcrumb();
      clear(listEl);
      if (!result.items.length) {
        listEl.appendChild(el("p", { class: "manager-empty", text: "Пусто." }));
        return;
      }
      for (const item of result.items) {
        listEl.appendChild(buildCard(item));
      }
    }

    async function open() {
      dialog.showModal();
      setRoot("cars");
    }

    return { init, open };
  })();

  window.adminFileManagerDialog = adminFileManager;
})();
