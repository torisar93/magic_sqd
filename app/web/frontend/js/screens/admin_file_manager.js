// ==================================================================
// Единый файловый менеджер (только admin_mode, открывается из js/app.js) —
// см. app/web/api/admin_api.py: browse_tree/delete_tree_path/move_path,
// server/backend.py: GET/DELETE /admin/api/browse, POST /admin/api/move.
// Заменяет старый диалог "Файлы на сервере" (только просмотр/удаление,
// только cars/) — теперь одно дерево на cars/ И apk/, плюс перенос/
// переименование через drag-and-drop (тот же паттерн, что и в веб-админке
// на сайте, server/admin/index.html — HTML5 DnD внутри pywebview работает
// так же, это чисто DOM-перетаскивание, не файлы ОС). Строки — карточки
// по образцу .app-row (см. css/components.css: .manager-row), с колонками
// имя/размер/изменён/действие, а не голый flex space-between.
// ==================================================================
(() => {
  const { el, clear } = window.dom;

  const adminFileManager = (() => {
    let dialog, breadcrumbEl, listEl, upBtn, rootCarsBtn, rootApkBtn;
    let root = "cars";
    let path = "";
    let draggedName = null;

    function init() {
      dialog = document.getElementById("admin-manager-dialog");
      breadcrumbEl = document.getElementById("admin-manager-breadcrumb");
      listEl = document.getElementById("admin-manager-list");
      upBtn = document.getElementById("admin-manager-up");
      rootCarsBtn = document.getElementById("admin-manager-root-cars");
      rootApkBtn = document.getElementById("admin-manager-root-apk");

      rootCarsBtn.addEventListener("click", () => setRoot("cars"));
      rootApkBtn.addEventListener("click", () => setRoot("apk"));
      upBtn.addEventListener("click", () => {
        const parts = path.split("/").filter(Boolean);
        parts.pop();
        load(parts.join("/"));
      });
      document.getElementById("admin-manager-refresh").addEventListener("click", () => load(path));
      document.getElementById("admin-manager-close").addEventListener("click", () => dialog.close());
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

    function startRename(nameCell, label, item) {
      const input = el("input", { class: "manager-rename-input", value: item.name });
      clear(nameCell);
      nameCell.appendChild(input);
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

    function buildRow(item) {
      const row = el("div", { class: "manager-row", draggable: "true" });
      row.addEventListener("dragstart", () => { draggedName = item.name; });
      row.addEventListener("dragend", () => { draggedName = null; });

      const nameCell = el("span", { class: "manager-row-name" });
      const label = el("span", { text: (item.is_dir ? "▸ " : "") + item.name });
      if (item.is_dir) nameCell.classList.add("is-dir");
      // Одиночный клик (открыть папку) и двойной (переименовать) целятся в
      // один и тот же текст — навигация синхронно перестраивает весь список
      // (см. load), из-за чего вторая половина двойного клика улетает мимо
      // старого узла. Небольшая задержка перед навигацией даёт dblclick шанс
      // отменить её и начать переименование вместо перехода внутрь папки.
      let navTimer = null;
      if (item.is_dir) {
        label.addEventListener("click", () => {
          if (navTimer) return; // переход уже запланирован первым кликом — второй клик двойного клика ничего не переназначает
          navTimer = setTimeout(() => {
            navTimer = null;
            load(joinPath(path, item.name));
          }, 280);
        });
      }
      nameCell.appendChild(label);
      nameCell.addEventListener("dblclick", () => {
        if (navTimer) { clearTimeout(navTimer); navTimer = null; }
        startRename(nameCell, label, item);
      });
      row.appendChild(nameCell);

      row.appendChild(el("span", { class: "manager-row-size", text: item.is_dir ? "папка" : formatSize(item.size) }));
      row.appendChild(el("span", { class: "manager-row-date", text: formatDate(item.mtime) }));
      row.appendChild(el("button", { class: "danger", text: "Удалить", onclick: () => onDelete(item) }));

      if (item.is_dir) addDropTarget(row, joinPath(path, item.name));
      return row;
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
        listEl.appendChild(buildRow(item));
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
