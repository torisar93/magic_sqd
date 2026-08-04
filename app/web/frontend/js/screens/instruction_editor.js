// Блочный редактор instruction.html — портировано из app/instruction_editor.py.
// Открывается из car_wizard.js для этапов типа "instruction". Блоки —
// обычные dict {"type":..., "text"|"path"/"caption":...}, тот же формат,
// что и app/instruction_html.py (никакой отдельной модели на JS-стороне).
(function () {
  const { el, clear } = window.dom;

  const BLOCK_TYPE_LABELS = {
    h1: "Заголовок", h2: "Подзаголовок", p: "Текст", steps: "Шаги",
    warn: "Важно", danger: "Осторожно", photo: "Фото",
  };

  let dialog, listEl, previewFrame;
  let blocks = [];
  let onSaveCb = null;

  function init() {
    dialog = document.getElementById("instruction-editor-dialog");
    listEl = document.getElementById("instruction-block-list");
    previewFrame = document.getElementById("instruction-preview-frame");

    document.querySelectorAll("#instruction-editor-dialog [data-block-type]").forEach((btn) => {
      btn.addEventListener("click", () => addBlock(btn.dataset.blockType));
    });
    document.getElementById("instruction-refresh-preview").addEventListener("click", refreshPreview);
    document.getElementById("instruction-editor-save").addEventListener("click", onSave);
    document.getElementById("instruction-editor-cancel").addEventListener("click", () => dialog.close());
  }

  function open(initialBlocks, onSaveCallback) {
    blocks = (initialBlocks || []).map((b) => ({ ...b }));
    onSaveCb = onSaveCallback;
    renderRows();
    refreshPreview();
    dialog.showModal();
  }

  function addBlock(type) {
    blocks.push(type === "photo" ? { type: "photo", path: "", caption: "" } : { type, text: "" });
    renderRows();
  }

  function deleteBlock(index) {
    blocks.splice(index, 1);
    renderRows();
  }

  function moveBlock(index, delta) {
    const target = index + delta;
    if (target < 0 || target >= blocks.length) return;
    [blocks[index], blocks[target]] = [blocks[target], blocks[index]];
    renderRows();
  }

  function renderRows() {
    clear(listEl);
    if (!blocks.length) {
      listEl.appendChild(el("p", { class: "app-desc", text: "Добавьте блок кнопками выше." }));
      return;
    }
    blocks.forEach((block, index) => listEl.appendChild(buildRow(block, index)));
  }

  function buildRow(block, index) {
    const row = el("div", { class: "instruction-block-row" });
    row.appendChild(el("div", { class: "block-row-header" }, [
      el("span", { "data-block-type": block.type, text: BLOCK_TYPE_LABELS[block.type] || block.type }),
      el("div", {}, [
        el("button", { class: "icon-btn", text: "▲", onclick: () => moveBlock(index, -1) }),
        el("button", { class: "icon-btn", text: "▼", onclick: () => moveBlock(index, 1) }),
        el("button", { class: "danger icon-btn", text: "✕", onclick: () => deleteBlock(index) }),
      ]),
    ]));

    if (block.type === "h1" || block.type === "h2") {
      const input = el("input", { type: "text" });
      input.value = block.text || "";
      input.addEventListener("input", () => { block.text = input.value; });
      row.appendChild(input);
    } else if (block.type === "steps") {
      row.appendChild(el("div", { class: "app-desc", text: "Каждый шаг — отдельная строка" }));
      const textarea = el("textarea");
      textarea.value = block.text || "";
      textarea.addEventListener("input", () => { block.text = textarea.value; });
      row.appendChild(textarea);
    } else if (block.type === "p" || block.type === "warn" || block.type === "danger") {
      const textarea = el("textarea");
      textarea.value = block.text || "";
      textarea.addEventListener("input", () => { block.text = textarea.value; });
      row.appendChild(textarea);
    } else if (block.type === "photo") {
      const photoRow = el("div", { class: "row" });
      const nameLabel = el("span", {
        class: "app-desc",
        text: block.path ? block.path.split(/[\\/]/).pop() : "(не выбрано)",
      });
      const pickBtn = el("button", {
        text: "Выбрать фото...",
        onclick: async () => {
          const files = await window.pywebview.api.car_pick_files("image", false);
          if (!files.length) return;
          block.path = files[0].path;
          renderRows();
        },
      });
      photoRow.appendChild(pickBtn);
      photoRow.appendChild(nameLabel);
      row.appendChild(photoRow);

      const captionInput = el("input", { type: "text", placeholder: "Подпись под фото (необязательно)", style: "margin-top: 6px" });
      captionInput.value = block.caption || "";
      captionInput.addEventListener("input", () => { block.caption = captionInput.value; });
      row.appendChild(captionInput);
    }
    return row;
  }

  async function refreshPreview() {
    const withPhotos = blocks.filter((b) => b.type !== "photo" || b.path);
    previewFrame.srcdoc = await window.pywebview.api.car_instruction_render_preview(withPhotos);
  }

  function onSave() {
    if (onSaveCb) onSaveCb(blocks);
    dialog.close();
  }

  window.instructionEditor = { init, open };
})();
