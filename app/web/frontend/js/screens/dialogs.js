// Три мелких диалога, портированные из app/usb_dialog.py, app/report_dialog.py
// и app/admin_upload_dialog.py — нативный <dialog> вместо CTkToplevel даёт
// фокус-трэп/оверлей бесплатно (см. план миграции). Прогресс долгих операций
// (usb-копирование, admin-выгрузка) идёт через те же глобальные события, что
// и install_log в stage_wizard.js — воркер-поток на стороне Python один на
// приложение, слушатель регистрируется один раз здесь, а не при каждом open().
(function () {
  const { clear } = window.dom;

  // ==================================================================
  // USB-флешка (открывается из js/screens/stage_wizard.js: renderUsbStage)
  // ==================================================================
  const usb = (() => {
    let dialog, driveSelect, formatCheckbox, fsRadios, warningEl, progressEl, logEl, startBtn, stopBtn;
    let drives = [];
    let opts = null; // {modelKey, stageIndex, variant, selectedApkPaths, titleSuffix, onFinished}
    let running = false;

    function init() {
      dialog = document.getElementById("usb-dialog");
      driveSelect = document.getElementById("usb-drive");
      formatCheckbox = document.getElementById("usb-format");
      fsRadios = Array.from(document.querySelectorAll('input[name="usb-fs"]'));
      warningEl = document.getElementById("usb-warning");
      progressEl = document.getElementById("usb-progress");
      logEl = document.getElementById("usb-log");
      startBtn = document.getElementById("usb-start");
      stopBtn = document.getElementById("usb-stop");

      document.getElementById("usb-refresh").addEventListener("click", refreshDrives);
      formatCheckbox.addEventListener("change", updateWarning);
      startBtn.addEventListener("click", onStart);
      stopBtn.addEventListener("click", () => {
        window.pywebview.api.usb_cancel();
        log("Останавливаю... (завершится на ближайшей проверке)");
      });
      document.getElementById("usb-close").addEventListener("click", onClose);

      window.events.on("usb_log", (event) => log(event.text));
      window.events.on("usb_finished", onFinished);
    }

    function log(text) {
      const line = document.createElement("div");
      line.textContent = text;
      logEl.appendChild(line);
      logEl.scrollTop = logEl.scrollHeight;
    }

    function updateWarning() {
      warningEl.textContent = formatCheckbox.checked
        ? "ВНИМАНИЕ: при форматировании все данные на выбранной флешке будут удалены безвозвратно!"
        : "Форматирование выключено — файлы будут просто скопированы поверх того, что уже есть на флешке.";
    }

    async function refreshDrives() {
      drives = await window.pywebview.api.usb_list_drives();
      clear(driveSelect);
      for (const d of drives) {
        const option = document.createElement("option");
        option.value = d.letter;
        option.textContent = d.display;
        driveSelect.appendChild(option);
      }
      log(`Найдено съёмных флешек: ${drives.length}`);
    }

    function setRunning(value) {
      running = value;
      startBtn.disabled = value;
      stopBtn.disabled = !value;
      driveSelect.disabled = value;
      progressEl.style.display = value ? "" : "none";
      progressEl.classList.toggle("indeterminate", value);
    }

    async function onStart() {
      const drive = drives.find((d) => d.letter === driveSelect.value);
      if (!drive) {
        await window.notice("Выберите флешку из списка.");
        return;
      }
      const fs = fsRadios.find((r) => r.checked).value;
      if (formatCheckbox.checked) {
        const sizeGb = (drive.total_bytes / 1024 ** 3).toFixed(1);
        const confirmed = await window.confirmDialog(
          `Все данные на флешке ${drive.letter}\\ (${drive.label || "без метки"}, ${sizeGb} ГБ) будут удалены безвозвратно.\n\nПродолжить форматирование в ${fs}?`
        );
        if (!confirmed) return;
      }
      setRunning(true);
      const result = await window.pywebview.api.usb_start(
        opts.modelKey, opts.stageIndex, opts.variant, opts.selectedApkPaths,
        drive.letter, formatCheckbox.checked, fs
      );
      if (!result.ok) {
        setRunning(false);
        log(result.error);
      }
    }

    async function onFinished(event) {
      if (!running) return; // диалог уже закрыт/не для этого запуска
      setRunning(false);
      log(event.message);
      if (opts && opts.onFinished) opts.onFinished(event.success);
      if (event.success) await window.notice(event.message);
      else await window.notice(event.message, { title: "Ошибка", danger: true });
    }

    async function onClose() {
      if (running && !(await window.confirmDialog("Операция ещё выполняется. Закрыть окно?"))) return;
      if (running) window.pywebview.api.usb_cancel();
      dialog.close();
    }

    function open(newOpts) {
      opts = newOpts;
      document.getElementById("usb-dialog-title").textContent = `USB-флешка — ${opts.titleSuffix}`;
      clear(logEl);
      setRunning(false);
      updateWarning();
      refreshDrives();
      dialog.showModal();
    }

    return { init, open };
  })();

  // ==================================================================
  // Сообщить о проблеме (открывается из js/app.js по кнопке report-btn)
  // ==================================================================
  const report = (() => {
    let dialog, reasonSelect, descriptionEl, statusEl, sendBtn;
    let currentModel = null;
    const REASONS = [
      "Появился способ установки", "Инструкция больше не актуальна",
      "Появилась новая версия", "Не работает этап установки", "Другое",
    ];

    function init() {
      dialog = document.getElementById("report-dialog");
      reasonSelect = document.getElementById("report-reason");
      descriptionEl = document.getElementById("report-description");
      statusEl = document.getElementById("report-status");
      sendBtn = document.getElementById("report-send");

      clear(reasonSelect);
      for (const reason of REASONS) {
        const option = document.createElement("option");
        option.value = reason;
        option.textContent = reason;
        reasonSelect.appendChild(option);
      }

      sendBtn.addEventListener("click", onSend);
      document.getElementById("report-cancel").addEventListener("click", () => dialog.close());
    }

    async function open(model) {
      const info = await window.pywebview.api.report_get_info();
      if (!info.available) {
        await window.notice("Отправка обращений не настроена (нет submit.json рядом с программой).");
        return;
      }
      currentModel = model;
      const reportModelName = model.modification ? `${model.name} — ${model.modification}` : model.name;
      document.getElementById("report-dialog-title").textContent = `Сообщить о проблеме — ${model.brand} / ${reportModelName}`;
      reasonSelect.value = model.no_instruction ? "Появился способ установки" : REASONS[0];
      descriptionEl.value = "";
      statusEl.textContent = "";
      sendBtn.disabled = false;
      dialog.showModal();
    }

    async function onSend() {
      const reportModelName = currentModel.modification
        ? `${currentModel.name} — ${currentModel.modification}` : currentModel.name;
      sendBtn.disabled = true;
      statusEl.textContent = "Отправка...";
      const result = await window.pywebview.api.report_send(
        currentModel.brand, reportModelName, reasonSelect.value, descriptionEl.value.trim()
      );
      if (result.ok) {
        await window.notice(result.message);
        dialog.close();
      } else {
        sendBtn.disabled = false;
        statusEl.textContent = result.error;
      }
    }

    return { init, open };
  })();

  // ==================================================================
  // Выгрузить на сервер (только admin_mode, открывается из js/app.js)
  // ==================================================================
  const admin = (() => {
    let dialog, usernameEl, passwordEl, progressEl, logEl, startBtn;
    let running = false;

    function init() {
      dialog = document.getElementById("admin-dialog");
      usernameEl = document.getElementById("admin-username");
      passwordEl = document.getElementById("admin-password");
      progressEl = document.getElementById("admin-progress");
      logEl = document.getElementById("admin-log");
      startBtn = document.getElementById("admin-start");

      startBtn.addEventListener("click", onStart);
      document.getElementById("admin-close").addEventListener("click", onClose);

      window.events.on("admin_log", (event) => log(event.text));
      window.events.on("admin_finished", onFinished);
    }

    function log(text) {
      const line = document.createElement("div");
      line.textContent = text;
      logEl.appendChild(line);
      logEl.scrollTop = logEl.scrollHeight;
    }

    async function open() {
      const info = await window.pywebview.api.admin_get_info();
      if (!info.available) {
        await window.notice('Не найден admin.json рядом с программой — без него неизвестно, куда загружать. Формат: {"base_url": "https://ваш-домен"}');
        return;
      }
      document.getElementById("admin-server-label").textContent = `Сервер: ${info.base_url}`;
      usernameEl.value = "";
      passwordEl.value = "";
      clear(logEl);
      running = false;
      startBtn.disabled = false;
      progressEl.style.display = "none";
      progressEl.classList.remove("indeterminate");
      dialog.showModal();
    }

    async function onStart() {
      const username = usernameEl.value.trim();
      const password = passwordEl.value;
      if (!username || !password) {
        await window.notice("Введите логин и пароль.");
        return;
      }
      startBtn.disabled = true;
      running = true;
      progressEl.style.display = "";
      progressEl.classList.add("indeterminate");
      const result = await window.pywebview.api.admin_start_upload(username, password);
      if (!result.ok) {
        running = false;
        startBtn.disabled = false;
        progressEl.style.display = "none";
        progressEl.classList.remove("indeterminate");
        log(result.error);
      }
    }

    async function onFinished(event) {
      running = false;
      startBtn.disabled = false;
      progressEl.style.display = "none";
      progressEl.classList.remove("indeterminate");
      log(event.message);
      if (event.success) await window.notice(event.message);
      else await window.notice(event.message, { title: "Ошибка", danger: true });
    }

    async function onClose() {
      if (running && !(await window.confirmDialog("Загрузка ещё выполняется. Закрыть окно?"))) return;
      if (running) window.pywebview.api.admin_cancel_upload();
      dialog.close();
    }

    return { init, open };
  })();

  function initDialogs() {
    usb.init();
    report.init();
    admin.init();
  }

  window.usbDialog = usb;
  window.reportDialog = report;
  window.adminDialog = admin;
  window.initDialogs = initDialogs;
})();
