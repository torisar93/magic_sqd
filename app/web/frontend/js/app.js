// Бутстрап главного окна: разделитель, 3-шаговый пикер, панель лога, stage
// wizard, мелкие диалоги (usb/report/admin-upload) и мастер "Добавить/
// Изменить машину" (с редактором инструкции внутри).
let currentModel = null;

function log(text) {
  const el = document.getElementById("log-panel");
  const line = document.createElement("div");
  line.textContent = text;
  el.appendChild(line);
  el.scrollTop = el.scrollHeight;
}

function onModelSelected(model) {
  currentModel = model;
  document.getElementById("report-btn").disabled = false;
  document.getElementById("edit-car-btn").disabled = !model.has_wizard_spec;
  window.stageWizard.open(model);
}

function onCarCreated(brand, modelName, modification) {
  const label = modification ? `${brand} / ${modelName} — ${modification}` : `${brand} / ${modelName}`;
  log(`Сохранена модель: ${label}`);
  window.mainPicker.reload();
}

window.addEventListener("pywebviewready", async () => {
  window.initResizer(document.getElementById("app-shell"), document.getElementById("resizer"));

  window.initDialogs();
  window.carWizard.init(log);
  window.stageWizard.init(document.getElementById("install-content"), log);
  await window.mainPicker.init(document.getElementById("picker"), { onModelSelected });

  const info = await window.pywebview.api.app_get_info();
  if (info.admin_mode) {
    document.getElementById("admin-upload-btn").style.display = "";
  }

  document.getElementById("add-car-btn").addEventListener("click", () =>
    window.carWizard.open(null, window.mainPicker.getBrands(), onCarCreated));
  document.getElementById("edit-car-btn").addEventListener("click", () =>
    window.carWizard.open(currentModel, window.mainPicker.getBrands(), onCarCreated));
  document.getElementById("report-btn").addEventListener("click", () => window.reportDialog.open(currentModel));
  document.getElementById("admin-upload-btn").addEventListener("click", () => window.adminDialog.open());

  window.events.on("log", (event) => log(event.text));
});
