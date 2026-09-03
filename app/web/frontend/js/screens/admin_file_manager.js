// ==================================================================
// Единый файловый менеджер (только admin_mode, открывается из js/app.js) —
// см. app/web/api/admin_api.py: browse_tree/delete_tree_path/move_path,
// server/backend.py: GET/DELETE /admin/api/browse, POST /admin/api/move.
// Заменяет старый диалог "Файлы на сервере" (только просмотр/удаление,
// только cars/) — теперь одно дерево на cars/ И apk/, плюс перенос/
// переименование через drag-and-drop (тот же паттерн, что и в веб-админке
// на сайте, server/admin/index.html — HTML5 DnD внутри pywebview работает
// так же, это чисто DOM-перетаскивание, не файлы ОС).
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
      rootCarsBtn.className = root === "cars" ? "accent" : "";
      rootApkBtn.className = root === "apk" ? "accent" : "";
      load("");
    }

    function formatSize(bytes) {
      if (bytes == null) return "папка";
      if (bytes < 1024) return `${bytes} Б`;
      if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} КБ`;
      return `${(bytes / 1024 ** 2).toFixed(1)} МБ`;
    }

    function joinPath(base, name) {
      return base ? `${base}/${name}` : name;
    }

    function addDropTarget(node, targetPath) {
      node.addEventListener("dragover", (e) => {
        e.preventDefault();
        node.style.background = "var(--accent)";
      });
      node.addEventListener("dragleave", () => { node.style.background = ""; });
      node.addEventListener("drop", (e) => {
        e.preventDefault();
        node.style.background = "";
        if (draggedName === null) return;
        moveItem(joinPath(path, draggedName), joinPath(targetPath, draggedName));
      });
    }

    function renderBreadcrumb() {
      clear(breadcrumbEl);
      const rootCrumb = el("span", {
        text: root + "/",
        style: "cursor: pointer; text-decoration: underline; color: var(--accent)",
        onclick: () => load(""),
      });
      addDropTarget(rootCrumb, "");
      breadcrumbEl.appendChild(rootCrumb);

      let accum = "";
      for (const part of path ? path.split("/") : []) {
        accum = accum ? `${accum}/${part}` : part;
        const crumbPath = accum;
        breadcrumbEl.appendChild(el("span", { text: " / ", style: "color: var(--text-dim)" }));
        const crumb = el("span", {
          text: part,
          style: "cursor: pointer; text-decoration: underline; color: var(--accent)",
          onclick: () => load(crumbPath),
        });
        addDropTarget(crumb, crumbPath);
        breadcrumbEl.appendChild(crumb);
      }
    }

    function startRename(nameCell, label, item) {
      const input = el("input", { value: item.name, style: "width: 100%" });
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
      const row = el("div", {
        class: "row",
        style: "justify-content: space-between; align-items: center; padding: 4px 0",
        draggable: "true",
      });
      row.addEventListener("dragstart", () => { draggedName = item.name; });
      row.addEventListener("dragend", () => { draggedName = null; });

      const nameCell = el("span", { style: "flex: 1; min-width: 0" });
      const label = el("span", {
        text: (item.is_dir ? "▸ " : "") + item.name + `  (${formatSize(item.size)})`,
        style: item.is_dir ? "cursor: pointer; text-decoration: underline" : "",
      });
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
        listEl.appendChild(el("p", { class: "app-desc", text: "Пусто." }));
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
