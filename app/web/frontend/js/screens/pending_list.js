// Раздел "На модерации" (только admin_mode, см. app.js) — заявки клиентов
// с сайта, см. app/web/api/submissions_api.py. Не запрашивает список сама
// по себе при старте — до первого явного входа в админку (10 тапов по
// версии в Настройках, или "Выгрузить на сервер...") submissions_list()
// почти всегда вернёт "Сначала войдите...", и дёргать это молча на каждом
// запуске приложения было бы просто раздражающим шумом — ждём клика
// "Обновить" (или успешного логина где-то ещё в программе).
(function () {
  const { el, clear } = window.dom;

  let listEl, countEl, progressEl;
  let filesDialog, filesLabelEl, filesListEl;
  let currentFilesModel = null; // {key, submission_name, brand, name, modification}

  function init() {
    listEl = document.getElementById("pending-list");
    countEl = document.getElementById("pending-count");
    progressEl = document.getElementById("pending-progress");
    document.getElementById("pending-refresh-btn").addEventListener("click", reload);

    filesDialog = document.getElementById("submission-files-dialog");
    filesLabelEl = document.getElementById("submission-files-label");
    filesListEl = document.getElementById("submission-files-list");
    document.getElementById("submission-files-close").addEventListener("click", () => filesDialog.close());
    document.getElementById("submission-files-publish").addEventListener("click", onFilesPublish);
    document.getElementById("submission-files-reject").addEventListener("click", onFilesReject);

    renderEmpty("Нажмите «Обновить», чтобы загрузить список.");
  }

  function setBusy(busy) {
    progressEl.style.display = busy ? "" : "none";
    progressEl.classList.toggle("indeterminate", busy);
  }

  function renderEmpty(text, showLoginLink) {
    clear(listEl);
    const li = el("li", { class: "empty", text });
    listEl.appendChild(li);
    if (showLoginLink) {
      const link = el("li", { class: "empty" });
      const btn = el("button", { text: "Войти в админку...", onclick: () => window.adminLoginDialog.open() });
      link.appendChild(btn);
      listEl.appendChild(link);
    }
    countEl.textContent = "";
  }

  async function reload() {
    setBusy(true);
    const result = await window.pywebview.api.submissions_list();
    setBusy(false);
    if (!result.ok) {
      renderEmpty(result.error, /войдите/i.test(result.error || ""));
      return;
    }
    countEl.textContent = result.items.length ? `(${result.items.length})` : "";
    if (result.items.length === 0) {
      renderEmpty("Пусто.");
      return;
    }
    renderList(result.items);
  }

  function itemLabel(item) {
    if (item.brand && item.model) {
      return item.modification ? `${item.brand} / ${item.model} — ${item.modification}` : `${item.brand} / ${item.model}`;
    }
    return item.label || item.name;
  }

  function renderList(items) {
    clear(listEl);
    for (const item of items) {
      const li = el("li", { style: "flex-direction: column; align-items: stretch; gap: 4px" });
      const top = el("div", { style: "display: flex; align-items: center; gap: 6px" });
      top.appendChild(el("span", { style: "flex: 1", text: itemLabel(item) }));
      top.appendChild(el("span", {
        style: "color: var(--text-dim); font-size: 11px",
        text: `${(item.size / 1024).toFixed(0)} КБ`,
      }));
      li.appendChild(top);
      if (!item.brand || !item.model) {
        li.appendChild(el("div", {
          style: "color: var(--text-dim); font-size: 11px",
          text: "Нет данных о марке/модели (старая заявка) — только просмотр/отклонение.",
        }));
      }
      const actions = el("div", { style: "display: flex; gap: 6px" });
      actions.appendChild(el("button", { text: "Открыть", onclick: () => openItem(item) }));
      actions.appendChild(el("button", { class: "danger", text: "Отклонить", onclick: () => rejectFromList(item) }));
      li.appendChild(actions);
      listEl.appendChild(li);
    }
  }

  function waitForEvent(kind) {
    return new Promise((resolve) => {
      function handler(event) {
        window.events.off(kind, handler);
        resolve(event);
      }
      window.events.on(kind, handler);
    });
  }

  async function openItem(item) {
    setBusy(true);
    const result = await window.pywebview.api.submissions_stage(
      item.name, item.brand, item.model, item.modification);
    if (!result.ok) {
      setBusy(false);
      await window.notice(result.error, { title: "Заявка", danger: true });
      return;
    }
    const event = await waitForEvent("submissions_finished");
    setBusy(false);
    if (!event.success) {
      await window.notice(event.message, { title: "Заявка", danger: true });
      return;
    }
    const model = event.model;
    if (model.has_wizard_spec) {
      window.graphWizard.open(model, window.mainPicker.getBrands(), null);
    } else {
      openFilesDialog(model);
    }
  }

  async function openFilesDialog(model) {
    currentFilesModel = model;
    filesLabelEl.textContent = model.brand && model.name
      ? (model.modification ? `${model.brand} / ${model.name} — ${model.modification}` : `${model.brand} / ${model.name}`)
      : model.submission_name;
    clear(filesListEl);
    filesListEl.appendChild(el("li", { class: "empty", text: "Загружаю список файлов..." }));
    filesDialog.showModal();
    const result = await window.pywebview.api.submissions_peek(model.submission_name);
    clear(filesListEl);
    if (!result.ok) {
      filesListEl.appendChild(el("li", { class: "empty", text: result.error }));
      return;
    }
    for (const entry of result.items) {
      const li = el("li");
      li.appendChild(el("span", { style: "flex: 1", text: entry.path }));
      li.appendChild(el("span", {
        style: "color: var(--text-dim); font-size: 11px",
        text: `${(entry.size / 1024).toFixed(1)} КБ`,
      }));
      filesListEl.appendChild(li);
    }
  }

  async function onFilesPublish() {
    if (!currentFilesModel) return;
    if (!currentFilesModel.brand || !currentFilesModel.name) {
      await window.notice(
        "У этой заявки нет данных о марке/модели — опубликовать одной кнопкой нельзя, " +
        "только вручную (скачайте и добавьте через мастер).",
        { title: "Публикация", danger: true });
      return;
    }
    const result = await window.pywebview.api.submissions_publish(currentFilesModel.key);
    if (!result.ok) {
      await window.notice(result.error, { title: "Публикация", danger: true });
      return;
    }
    const event = await waitForEvent("submissions_finished");
    if (!event.success) {
      await window.notice(event.message, { title: "Публикация", danger: true });
      return;
    }
    filesDialog.close();
    reload();
    window.mainPicker.reload();
  }

  async function onFilesReject() {
    if (!currentFilesModel) return;
    const ok = await window.confirmDialog("Отклонить эту заявку? Это необратимо.", { title: "Отклонить заявку" });
    if (!ok) return;
    const result = await window.pywebview.api.submissions_reject(currentFilesModel.submission_name);
    if (!result.ok) {
      await window.notice(result.error, { title: "Отклонение", danger: true });
      return;
    }
    const event = await waitForEvent("submissions_finished");
    if (!event.success) {
      await window.notice(event.message, { title: "Отклонение", danger: true });
      return;
    }
    filesDialog.close();
    reload();
  }

  async function rejectFromList(item) {
    const ok = await window.confirmDialog(`Отклонить заявку «${itemLabel(item)}»? Это необратимо.`,
      { title: "Отклонить заявку" });
    if (!ok) return;
    setBusy(true);
    const result = await window.pywebview.api.submissions_reject(item.name);
    if (!result.ok) {
      setBusy(false);
      await window.notice(result.error, { title: "Отклонение", danger: true });
      return;
    }
    const event = await waitForEvent("submissions_finished");
    setBusy(false);
    if (!event.success) {
      await window.notice(event.message, { title: "Отклонение", danger: true });
      return;
    }
    reload();
  }

  window.events.on("submissions_log", (event) => console.log("[submissions]", event.text));

  window.pendingList = { init, reload };
})();
