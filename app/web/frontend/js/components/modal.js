// Тематизированная замена нативных alert()/confirm()/prompt() — те
// рендерятся как стандартное белое окно Windows/Chromium поверх тёмного
// интерфейса (см. dialog::backdrop/dialog в css/components.css: обычный
// <dialog> уже стилизован, но alert()/confirm()/prompt() — отдельный
// браузерный UI, который стили не затрагивают). notice()/confirmDialog()/
// promptDialog() — promise-based обёртки над тем же <dialog>.
(function () {
  // Значение <option>, ведущее к ручному вводу в selectDialog() — та же
  // идея, что и MANUAL_CHOICE_VALUE в stage_wizard.js (независимая копия:
  // этот модуль не привязан к этапам установки, используется и для диалогов
  // главного окна вроде кнопки "Подключить Wi-Fi").
  const MANUAL_CHOICE_VALUE = "__manual__";

  let dialog, titleEl, messageEl, inputEl, selectEl, okBtn, cancelBtn, resolveFn;

  function ensureBuilt() {
    if (dialog) return;
    dialog = document.createElement("dialog");
    dialog.id = "app-modal";
    dialog.innerHTML = `
      <h2 id="app-modal-title"></h2>
      <p id="app-modal-message" style="white-space: pre-wrap"></p>
      <select id="app-modal-select" style="display: none"></select>
      <input type="text" id="app-modal-input" style="display: none" />
      <div class="dialog-actions">
        <button id="app-modal-cancel">Отмена</button>
        <button id="app-modal-ok" class="accent">OK</button>
      </div>
    `;
    document.body.appendChild(dialog);
    titleEl = dialog.querySelector("#app-modal-title");
    messageEl = dialog.querySelector("#app-modal-message");
    inputEl = dialog.querySelector("#app-modal-input");
    selectEl = dialog.querySelector("#app-modal-select");
    okBtn = dialog.querySelector("#app-modal-ok");
    cancelBtn = dialog.querySelector("#app-modal-cancel");
    selectEl.addEventListener("change", () => {
      const manual = selectEl.value === MANUAL_CHOICE_VALUE;
      inputEl.style.display = manual ? "" : "none";
      if (manual) inputEl.focus();
    });
    okBtn.addEventListener("click", () => {
      dialog.close();
      if (selectEl.style.display !== "none") {
        resolveFn(selectEl.value === MANUAL_CHOICE_VALUE ? inputEl.value : selectEl.value);
      } else {
        resolveFn(inputEl.style.display === "none" ? true : inputEl.value);
      }
    });
    cancelBtn.addEventListener("click", () => {
      dialog.close();
      resolveFn(inputEl.style.display === "none" && selectEl.style.display === "none" ? false : null);
    });
  }

  function notice(message, { title = "Magic SQD", danger = false } = {}) {
    ensureBuilt();
    titleEl.textContent = title;
    messageEl.textContent = message;
    messageEl.style.color = danger ? "var(--danger)" : "";
    selectEl.style.display = "none";
    inputEl.style.display = "none";
    cancelBtn.style.display = "none";
    okBtn.textContent = "OK";
    dialog.showModal();
    return new Promise((resolve) => { resolveFn = resolve; });
  }

  function confirmDialog(message, { title = "Magic SQD" } = {}) {
    ensureBuilt();
    titleEl.textContent = title;
    messageEl.textContent = message;
    messageEl.style.color = "";
    selectEl.style.display = "none";
    inputEl.style.display = "none";
    cancelBtn.style.display = "";
    cancelBtn.textContent = "Отмена";
    okBtn.textContent = "Да";
    dialog.showModal();
    return new Promise((resolve) => { resolveFn = resolve; });
  }

  function promptDialog(message, { title = "Magic SQD", initialValue = "", password = false } = {}) {
    ensureBuilt();
    titleEl.textContent = title;
    messageEl.textContent = message;
    messageEl.style.color = "";
    selectEl.style.display = "none";
    inputEl.style.display = "";
    inputEl.type = password ? "password" : "text";
    inputEl.value = initialValue;
    cancelBtn.style.display = "";
    cancelBtn.textContent = "Отмена";
    okBtn.textContent = "OK";
    dialog.showModal();
    inputEl.focus();
    return new Promise((resolve) => { resolveFn = resolve; });
  }

  // choices — список готовых вариантов (например IP/COM-порты, найденные
  // сканом, см. app.js:connectAdbWifi) — выбор через <select>. allowManual
  // (по умолчанию true) добавляет пункт "Ввести вручную...", открывающий
  // обычное текстовое поле (та же идея, что MANUAL_CHOICE_VALUE в
  // stage_wizard.js, для диалогов вне этапа установки).
  function selectDialog(message, choices, { title = "Magic SQD", allowManual = true } = {}) {
    ensureBuilt();
    titleEl.textContent = title;
    messageEl.textContent = message;
    messageEl.style.color = "";
    selectEl.innerHTML = "";
    for (const choice of choices) {
      const opt = document.createElement("option");
      opt.value = choice;
      opt.textContent = choice;
      selectEl.appendChild(opt);
    }
    if (allowManual) {
      const manualOpt = document.createElement("option");
      manualOpt.value = MANUAL_CHOICE_VALUE;
      manualOpt.textContent = "Ввести вручную...";
      selectEl.appendChild(manualOpt);
    }
    selectEl.style.display = "";
    selectEl.value = choices[0] || MANUAL_CHOICE_VALUE;
    inputEl.type = "text";
    inputEl.value = "";
    inputEl.style.display = selectEl.value === MANUAL_CHOICE_VALUE ? "" : "none";
    cancelBtn.style.display = "";
    cancelBtn.textContent = "Отмена";
    okBtn.textContent = "OK";
    dialog.showModal();
    return new Promise((resolve) => { resolveFn = resolve; });
  }

  window.notice = notice;
  window.confirmDialog = confirmDialog;
  window.promptDialog = promptDialog;
  window.selectDialog = selectDialog;
})();
