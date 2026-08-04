// Тематизированная замена нативных alert()/confirm()/prompt() — те
// рендерятся как стандартное белое окно Windows/Chromium поверх тёмного
// интерфейса (см. dialog::backdrop/dialog в css/components.css: обычный
// <dialog> уже стилизован, но alert()/confirm()/prompt() — отдельный
// браузерный UI, который стили не затрагивают). notice()/confirmDialog()/
// promptDialog() — promise-based обёртки над тем же <dialog>.
(function () {
  let dialog, titleEl, messageEl, inputEl, okBtn, cancelBtn, resolveFn;

  function ensureBuilt() {
    if (dialog) return;
    dialog = document.createElement("dialog");
    dialog.id = "app-modal";
    dialog.innerHTML = `
      <h2 id="app-modal-title"></h2>
      <p id="app-modal-message" style="white-space: pre-wrap"></p>
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
    okBtn = dialog.querySelector("#app-modal-ok");
    cancelBtn = dialog.querySelector("#app-modal-cancel");
    okBtn.addEventListener("click", () => {
      dialog.close();
      resolveFn(inputEl.style.display === "none" ? true : inputEl.value);
    });
    cancelBtn.addEventListener("click", () => {
      dialog.close();
      resolveFn(inputEl.style.display === "none" ? false : null);
    });
  }

  function notice(message, { title = "Magic SQD", danger = false } = {}) {
    ensureBuilt();
    titleEl.textContent = title;
    messageEl.textContent = message;
    messageEl.style.color = danger ? "var(--danger)" : "";
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

  window.notice = notice;
  window.confirmDialog = confirmDialog;
  window.promptDialog = promptDialog;
})();
