// Три мелких диалога, портированные из app/usb_dialog.py, app/report_dialog.py
// и app/admin_upload_dialog.py — нативный <dialog> вместо CTkToplevel даёт
// фокус-трэп/оверлей бесплатно (см. план миграции). Прогресс долгих операций
// (usb-копирование, admin-выгрузка) идёт через те же глобальные события, что
// и install_log в stage_wizard.js — воркер-поток на стороне Python один на
// приложение, слушатель регистрируется один раз здесь, а не при каждом open().
(function () {
  const { el, clear } = window.dom;

  // ==================================================================
  // USB-флешка (открывается из js/screens/stage_wizard.js: renderUsbStage)
  // ==================================================================
  const usb = (() => {
    let dialog, driveSelect, showAllCheckbox, driveHintEl, formatCheckbox, fsRadios, warningEl, progressEl, logEl, startBtn, stopBtn, closeBtn;
    let refreshBtn, statusEl, statusDetailEl, logDetails, advancedDetails;
    let drives = [];
    let opts = null;
    let running = false, preparing = false, refreshing = false;
    let cancelRequested = false, finishDelivered = false;
    let openRevision = 0, refreshRevision = 0, runRevision = 0;

    function init() {
      dialog = document.getElementById("usb-dialog");
      driveSelect = document.getElementById("usb-drive");
      showAllCheckbox = document.getElementById("usb-show-all");
      driveHintEl = document.getElementById("usb-drive-hint");
      formatCheckbox = document.getElementById("usb-format");
      fsRadios = Array.from(document.querySelectorAll('input[name="usb-fs"]'));
      warningEl = document.getElementById("usb-warning");
      progressEl = document.getElementById("usb-progress");
      logEl = document.getElementById("usb-log");
      startBtn = document.getElementById("usb-start");
      stopBtn = document.getElementById("usb-stop");
      closeBtn = document.getElementById("usb-close");
      refreshBtn = document.getElementById("usb-refresh");
      statusEl = document.getElementById("usb06-status");
      statusDetailEl = document.getElementById("usb06-status-detail");
      logDetails = document.getElementById("usb06-log-details");
      advancedDetails = document.getElementById("usb06-advanced");
      refreshBtn.addEventListener("click", refreshDrives);
      showAllCheckbox.addEventListener("change", refreshDrives);
      formatCheckbox.addEventListener("change", updateWarning);
      driveSelect.addEventListener("change", () => {
        updateControls();
        setStatus("ready", driveSelect.value ? "Всё готово к записи" : "Выберите USB-накопитель",
          driveSelect.value ? "Файлы будут записаны на выбранную флешку." : "Подключите флешку и выберите её в списке выше.");
      });
      startBtn.addEventListener("click", onStart);
      stopBtn.addEventListener("click", onStop);
      closeBtn.addEventListener("click", onClose);
      dialog.addEventListener("cancel", (event) => {
        if (running || preparing) event.preventDefault();
      });
      dialog.addEventListener("close", () => {
        openRevision += 1;
        refreshRevision += 1;
        refreshing = false;
      });
      window.events.on("usb_log", (event) => {
        if (!dialog.open || !running) return;
        log(event.text);
        if (!cancelRequested && event.text) statusDetailEl.textContent = event.text;
      });
      window.events.on("usb_finished", onFinished);
    }

    function log(text) {
      if (!text) return;
      const line = document.createElement("div");
      line.className = `usb06-log-line log-line-${window.classifyLogLevel(text)}`;
      line.textContent = text;
      logEl.appendChild(line);
      logEl.scrollTop = logEl.scrollHeight;
    }

    function updateWarning() {
      warningEl.classList.toggle("danger", formatCheckbox.checked);
      warningEl.textContent = formatCheckbox.checked
        ? "Все данные на выбранном накопителе будут удалены. Перед началом попросим подтвердить форматирование."
        : "Без форматирования. Существующие папки сохранятся; файлы с совпадающими именами могут быть заменены.";
      updateControls();
    }

    // При ошибке — та же подсказка «откройте лог, скопируйте, отправьте
    // разработчику» и те же кнопки, что в окне остальных этапов (StageRun).
    let hintNode = null;
    function setStatus(state, title, detail) {
      dialog.dataset.usbState = state;
      statusEl.textContent = title;
      statusDetailEl.textContent = detail || "";
      hintNode?.remove();
      hintNode = null;
      if (state === "error" && window.StageRun) {
        hintNode = window.StageRun.hintBlock();
        statusDetailEl.closest('.usb06-transfer').after(hintNode);
      }
    }

    function updateControls() {
      const locked = running || preparing || refreshing;
      startBtn.disabled = locked || !drives.some((drive) => drive.letter === driveSelect.value);
      stopBtn.disabled = !running || cancelRequested;
      stopBtn.hidden = !running;
      driveSelect.disabled = locked;
      refreshBtn.disabled = locked;
      refreshBtn.setAttribute("aria-busy", String(refreshing));
      showAllCheckbox.disabled = locked;
      formatCheckbox.disabled = locked;
      fsRadios.forEach((radio) => { radio.disabled = locked || !formatCheckbox.checked; });
      closeBtn.disabled = running || preparing;
      progressEl.hidden = !running;
      progressEl.style.display = running ? "" : "none";
      progressEl.classList.toggle("indeterminate", running);
      progressEl.setAttribute("aria-busy", String(running));
      dialog.setAttribute("aria-busy", String(running || refreshing));
      startBtn.querySelector("span").textContent = preparing ? "Подтверждение…" : running ? "Идёт запись…" : "Записать на флешку";
      stopBtn.textContent = cancelRequested ? "Останавливаем…" : "Остановить";
    }

    function populateDrives(selectedLetter) {
      clear(driveSelect);
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = drives.length ? "Выберите накопитель" : "Накопители не найдены";
      driveSelect.appendChild(placeholder);
      for (const drive of drives) {
        const option = document.createElement("option");
        option.value = drive.letter;
        option.textContent = drive.display || drive.letter;
        driveSelect.appendChild(option);
      }
      driveSelect.value = drives.some((drive) => drive.letter === selectedLetter) ? selectedLetter : "";
    }

    async function refreshDrives() {
      if (running || preparing) return;
      const revision = ++refreshRevision;
      const session = openRevision;
      const selectedLetter = driveSelect.value;
      const showAll = showAllCheckbox.checked;
      refreshing = true;
      updateControls();
      driveHintEl.textContent = "Ищем подключённые накопители…";
      try {
        const result = await window.pywebview.api.usb_list_drives(showAll);
        if (revision !== refreshRevision || session !== openRevision) return;
        if (!Array.isArray(result)) throw new Error(result?.error || "Не удалось получить список накопителей.");
        drives = result;
        populateDrives(selectedLetter);
        driveHintEl.textContent = showAll
          ? "Показаны также внутренние диски, кроме системного. Проверьте накопитель перед записью."
          : "Только съёмные USB-накопители. Внутренние и системный диски скрыты.";
        if (selectedLetter && !driveSelect.value) {
          setStatus("ready", "Накопитель отключён", "Выберите флешку повторно. Другой диск не будет выбран автоматически.");
        } else {
          setStatus("ready", driveSelect.value ? "Всё готово к записи" : "Выберите USB-накопитель",
            drives.length ? "Проверьте выбранную флешку перед началом записи." : "Подключите флешку и обновите список.");
        }
        log(`Найдено накопителей: ${drives.length}`);
      } catch (error) {
        if (revision !== refreshRevision || session !== openRevision) return;
        drives = [];
        populateDrives("");
        driveHintEl.textContent = "Список не обновлён. Проверьте подключение и попробуйте ещё раз.";
        setStatus("error", "Не удалось найти накопители", error?.message || String(error));
        log(error?.message || String(error));
        logDetails.open = true;
      } finally {
        if (revision === refreshRevision && session === openRevision) {
          refreshing = false;
          updateControls();
        }
      }
    }

    async function onStart() {
      if (running || preparing || refreshing || !dialog.open || !opts) return;
      const drive = drives.find((d) => d.letter === driveSelect.value);
      if (!drive) {
        setStatus("ready", "Выберите USB-накопитель", "Перед записью нужно выбрать флешку из списка.");
        return;
      }
      const fs = fsRadios.find((r) => r.checked)?.value || "FAT32";
      const shouldFormat = formatCheckbox.checked;
      const launchOpts = opts;
      const revision = ++runRevision;
      preparing = true;
      cancelRequested = false;
      finishDelivered = false;
      updateControls();
      try {
        if (shouldFormat) {
          const sizeGb = Number.isFinite(Number(drive.total_bytes)) && Number(drive.total_bytes) > 0
            ? `${(Number(drive.total_bytes) / 1024 ** 3).toFixed(1)} ГБ` : "объём не указан";
          const confirmed = await window.confirmDialog(
            `Накопитель: ${drive.display || drive.letter}\nДиск: ${drive.letter} · ${drive.label || "без метки"} · ${sizeGb}\nФайловая система: ${fs}\n\nВсе данные на этом диске будут удалены безвозвратно, после чего на него будут записаны файлы установки.\n\nФорматировать именно этот накопитель?`,
            { title: "Форматирование накопителя" }
          );
          if (!confirmed || revision !== runRevision || !dialog.open) return;
        }
        running = true;
        preparing = false;
        updateControls();
        setStatus("writing", "Записываем файлы на флешку", "Не отключайте накопитель. Время зависит от скорости флешки и размера файлов.");
        const result = await window.pywebview.api.usb_start(
          launchOpts.modelKey, launchOpts.stageIndex, launchOpts.variant, launchOpts.selectedApkPaths,
          drive.letter, shouldFormat, fs
        );
        if (revision !== runRevision || finishDelivered) return;
        if (!result?.ok) throw new Error(result?.error || "Не удалось начать запись на флешку.");
      } catch (error) {
        if (revision !== runRevision || finishDelivered) return;
        running = false;
        const message = error?.message || String(error);
        setStatus("error", "Запись не началась", message);
        log(message);
        logDetails.open = true;
      } finally {
        if (revision === runRevision) {
          preparing = false;
          updateControls();
        }
      }
    }

    async function onStop() {
      if (!running || cancelRequested) return;
      const revision = runRevision;
      cancelRequested = true;
      updateControls();
      setStatus("stopping", "Останавливаем запись", "Дождитесь завершения текущей операции. Не отключайте флешку.");
      log("Запрошена остановка. Дожидаемся завершения текущей операции.");
      try {
        const result = await window.pywebview.api.usb_cancel();
        if (result?.ok === false) throw new Error(result.error || "Не удалось отправить команду остановки.");
      } catch (error) {
        if (revision !== runRevision || !running) return;
        cancelRequested = false;
        log(error?.message || String(error));
        logDetails.open = true;
        setStatus("writing", "Запись продолжается", "Команда остановки не отправлена. Попробуйте остановить ещё раз.");
        updateControls();
      }
    }

    async function onFinished(event) {
      if (!running || finishDelivered) return;
      finishDelivered = true;
      running = false;
      preparing = false;
      const wasCancelled = cancelRequested;
      const success = !!event.success && !wasCancelled;
      const onComplete = opts?.onFinished;
      updateControls();
      log(event.message);
      if (wasCancelled) {
        setStatus("cancelled", "Запись остановлена", "Этап не завершён. Проверьте содержимое флешки перед повторной записью.");
      } else if (success) {
        setStatus("success", "Флешка готова", event.message || "Файлы записаны. Можно перейти к следующему шагу.");
      } else {
        setStatus("error", "Не удалось завершить запись", event.message || "Подробности доступны в журнале записи.");
        logDetails.open = true;
      }
      if (typeof onComplete === "function") {
        try { await onComplete(success); }
        catch (error) { log(error?.message || String(error)); logDetails.open = true; }
      }
    }

    function onClose() {
      if (running || preparing) return;
      dialog.close();
    }

    function open(newOpts) {
      if (running || preparing) return false;
      openRevision += 1;
      refreshRevision += 1;
      opts = newOpts;
      document.getElementById("usb-dialog-title").textContent = "Запись на флешку";
      document.getElementById("usb06-model").textContent = opts.titleSuffix || "Подготовьте USB-накопитель для установки";
      clear(logEl);
      running = false;
      preparing = false;
      refreshing = false;
      cancelRequested = false;
      finishDelivered = false;
      drives = [];
      populateDrives("");
      showAllCheckbox.checked = false;
      formatCheckbox.checked = false;
      fsRadios.forEach((radio) => { radio.checked = radio.value === "FAT32"; });
      advancedDetails.open = false;
      logDetails.open = false;
      setStatus("ready", "Выберите USB-накопитель", "Подключите флешку и выберите её в списке выше.");
      updateWarning();
      if (!dialog.open) dialog.showModal();
      refreshDrives();
      return true;
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
  // Войти в админку — либо необязательно (open(), просто логин без выгрузки
  // cars/apk, см. app/web/api/admin_api.py:login_only, чтобы получить
  // кешированную сессию для "Добавить APK.../Файлы на сервере..." без
  // похода в тяжёлую "Выгрузить на сервер..." — сейчас вызывается только
  // из pending_list.js, когда список заявок сам сообщает "Сначала
  // войдите..." из-за истёкшей сессии; отдельной постоянной кнопки для
  // этого больше нет — только 10 тапов), либо через openUnlock() —
  // разблокировка функций администратора из "Настроек" (10 тапов по версии
  // в "О приложении", см. settings.js). Раньше это была отдельная
  // admin-сборка (admin_main_web.py) с обязательным входом до показа
  // остального интерфейса — теперь одна программа, отдельного "жёсткого"
  // режима больше нет.
  // ==================================================================
  const adminLogin = (() => {
    let dialog, usernameEl, passwordEl, rememberEl, rememberRow, statusEl, startBtn, closeBtn, forgetBtn, titleEl, hintEl;
    let onUnlocked = null;

    function init() {
      dialog = document.getElementById("admin-login-dialog");
      usernameEl = document.getElementById("admin-login-username");
      passwordEl = document.getElementById("admin-login-password");
      rememberEl = document.getElementById("admin-login-remember");
      rememberRow = document.getElementById("admin-login-remember-row");
      statusEl = document.getElementById("admin-login-status");
      startBtn = document.getElementById("admin-login-start");
      closeBtn = document.getElementById("admin-login-close");
      forgetBtn = document.getElementById("admin-login-forget");
      titleEl = document.getElementById("admin-login-title");
      hintEl = document.getElementById("admin-login-hint");

      startBtn.addEventListener("click", onStart);
      closeBtn.addEventListener("click", () => dialog.close());
      forgetBtn.addEventListener("click", onForget);
    }

    async function open() {
      onUnlocked = null;
      const info = await window.pywebview.api.admin_get_info();
      if (!info.available) {
        await window.notice("Не найден admin.json рядом с программой — без него неизвестно, куда входить.");
        return;
      }
      titleEl.textContent = "Войти в админку";
      hintEl.textContent = "Только вход — ничего не выгружает. Сессия переиспользуется другими кнопками "
        + '("Добавить APK...", "Файлы на сервере..."), пока открыта программа.';
      hintEl.style.display = "";
      rememberRow.style.display = "";
      closeBtn.style.display = "";
      forgetBtn.style.display = "";
      document.getElementById("admin-login-server-label").textContent = `Сервер: ${info.base_url}`;
      usernameEl.value = "";
      passwordEl.value = "";
      rememberEl.checked = false;
      statusEl.textContent = "";
      startBtn.disabled = false;
      dialog.showModal();
    }

    // Разблокировка функций администратора (см. settings.js) — успешный
    // вход сразу включает admin_mode на весь текущий сеанс (см.
    // app/web/bridge.py: admin_login) И запоминается для следующих запусков
    // (см. admin_config.save_saved_login, WebApi.__init__: try_saved_login)
    // — здесь это не опционально, чекбокс "Запомнить меня" скрыт и всегда
    // считается включённым, иначе разблокировка не переживала бы перезапуск
    // и теряла бы смысл. onSuccess вызывается сразу после закрытия диалога.
    function openUnlock(onSuccess) {
      (async () => {
        const info = await window.pywebview.api.admin_get_info();
        if (!info.available) {
          await window.notice("Не найден admin.json рядом с программой — без него неизвестно, куда входить.");
          return;
        }
        onUnlocked = onSuccess;
        titleEl.textContent = "Разблокировать функции администратора";
        hintEl.textContent = "Вход сохранится на этом компьютере — при следующих запусках функции "
          + "администратора будут видны сразу, без повторного входа.";
        hintEl.style.display = "";
        rememberRow.style.display = "none";
        closeBtn.style.display = "";
        forgetBtn.style.display = "none";
        document.getElementById("admin-login-server-label").textContent = `Сервер: ${info.base_url}`;
        usernameEl.value = "";
        passwordEl.value = "";
        rememberEl.checked = true;
        statusEl.textContent = "";
        startBtn.disabled = false;
        dialog.showModal();
      })();
    }

    async function onStart() {
      const username = usernameEl.value.trim();
      const password = passwordEl.value;
      if (!username || !password) {
        await window.notice("Введите логин и пароль.");
        return;
      }
      startBtn.disabled = true;
      statusEl.textContent = "Вхожу...";
      const result = await window.pywebview.api.admin_login(username, password, rememberEl.checked);
      startBtn.disabled = false;
      if (!result.ok) {
        statusEl.textContent = result.error;
        return;
      }
      statusEl.textContent = "Вход выполнен.";
      dialog.close();
      if (onUnlocked) {
        const callback = onUnlocked;
        onUnlocked = null;
        callback();
      }
    }

    async function onForget() {
      await window.pywebview.api.admin_forget_saved_login();
      rememberEl.checked = false;
      statusEl.textContent = "Сохранённый вход забыт.";
    }

    return { init, open, openUnlock };
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
      line.className = `log-line log-line-${window.classifyLogLevel(text)}`;
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

  // ==================================================================
  // Добавить APK в общую библиотеку (только admin_mode, открывается из
  // js/app.js) — см. app/web/api/admin_api.py: add_apk/list_apk_categories/
  // create_apk_category/delete_apk_category. Диалог не закрывается после
  // "Добавить" — удобно закинуть сразу несколько APK подряд в одну сессию.
  // ==================================================================
  const adminApk = (() => {
    let dialog, categorySelect, fileLabel, nameInput, descriptionInput, progressEl, logEl, addBtn;
    let pickedFile = null;
    let publishing = false;

    function init() {
      dialog = document.getElementById("admin-apk-dialog");
      categorySelect = document.getElementById("admin-apk-category");
      fileLabel = document.getElementById("admin-apk-file-label");
      nameInput = document.getElementById("admin-apk-name");
      descriptionInput = document.getElementById("admin-apk-description");
      progressEl = document.getElementById("admin-apk-progress");
      logEl = document.getElementById("admin-apk-log");
      addBtn = document.getElementById("admin-apk-add");

      document.getElementById("admin-apk-pick-file").addEventListener("click", onPickFile);
      document.getElementById("admin-apk-new-folder").addEventListener("click", onNewFolder);
      addBtn.addEventListener("click", onAdd);
      document.getElementById("admin-apk-close").addEventListener("click", onClose);

      window.events.on("apk_upload_log", (event) => log(event.text));
      window.events.on("apk_upload_finished", onPublishFinished);
    }

    function log(text) {
      const line = document.createElement("div");
      line.className = `log-line log-line-${window.classifyLogLevel(text)}`;
      line.textContent = text;
      logEl.appendChild(line);
      logEl.scrollTop = logEl.scrollHeight;
    }

    async function reloadCategories(selectName) {
      const categories = await window.pywebview.api.admin_list_apk_categories();
      clear(categorySelect);
      const rootOption = document.createElement("option");
      rootOption.value = "";
      rootOption.textContent = "Без категории (корень apk/)";
      categorySelect.appendChild(rootOption);
      for (const name of categories) {
        const option = document.createElement("option");
        option.value = name;
        option.textContent = name;
        categorySelect.appendChild(option);
      }
      if (selectName !== undefined) categorySelect.value = selectName;
    }

    async function onPickFile() {
      const picked = await window.pywebview.api.car_pick_files("apk", false);
      if (!picked.length) return;
      pickedFile = picked[0];
      fileLabel.textContent = pickedFile.name;
      if (!nameInput.value.trim()) {
        nameInput.value = pickedFile.name.replace(/\.apk$/i, "");
      }
    }

    async function onNewFolder() {
      const name = (await window.promptDialog("Название новой папки:"))?.trim();
      if (!name) return;
      const result = await window.pywebview.api.admin_create_apk_category(name);
      if (!result.ok) {
        await window.notice(result.error, { title: "Новая папка", danger: true });
        return;
      }
      await reloadCategories(result.name);
    }

    function setPublishing(value) {
      publishing = value;
      progressEl.style.display = value ? "" : "none";
      progressEl.classList.toggle("indeterminate", value);
    }

    async function onAdd() {
      if (!pickedFile) {
        await window.notice("Сначала выберите файл APK.");
        return;
      }
      const name = nameInput.value.trim();
      if (!name) {
        await window.notice("Введите название приложения.");
        return;
      }
      addBtn.disabled = true;
      setPublishing(true);
      const result = await window.pywebview.api.admin_add_apk(
        pickedFile.path, name, descriptionInput.value.trim(), categorySelect.value
      );
      addBtn.disabled = false;
      if (!result.ok) {
        setPublishing(false);
        log(result.error);
        return;
      }
      log(`Добавлено: ${pickedFile.name} — «${name}».`);
      pickedFile = null;
      fileLabel.textContent = "(не выбрано)";
      nameInput.value = "";
      descriptionInput.value = "";
      // progress/publishing гасится по событию apk_upload_finished (см. onPublishFinished) —
      // публикация идёт в фоне на стороне Python и может занять время (сеть).
    }

    function onPublishFinished() {
      setPublishing(false);
    }

    function onClose() {
      if (publishing) {
        window.notice("Публикация на сервере ещё идёт в фоне — она завершится сама, окно можно закрыть.");
      }
      dialog.close();
    }

    async function open() {
      pickedFile = null;
      fileLabel.textContent = "(не выбрано)";
      nameInput.value = "";
      descriptionInput.value = "";
      clear(logEl);
      setPublishing(false);
      await reloadCategories("");
      dialog.showModal();
    }

    return { init, open };
  })();

  // ==================================================================
  // Обновление программы (открывается из js/app.js: checkForUpdate) — один
  // диалог и на предложение обновиться, и на сам процесс скачивания:
  // раньше это было textarea-подтверждение (window.confirmDialog) без
  // всякой обратной связи дальше — после клика "ОК" пользователь не видел
  // НИЧЕГО (лог шёл в общую панель лога, которую в этот момент никто не
  // открывал) до внезапного закрытия программы, что выглядело как зависание/
  // краш. Теперь тот же прогресс-бар с процентом, что и на стартовом экране
  // каталога (см. main_picker.js: showStartupLoading — тот же CSS-класс
  // catalog-startup-progress-track), плюс лог здесь же в диалоге.
  // ==================================================================
  const update = (() => {
    let dialog, versionEl, changelogEl, progressTrack, progressFill, progressLabel, logEl, installBtn, laterBtn;
    let downloadUrl = null;
    let installing = false;

    function init() {
      dialog = document.getElementById("update-dialog");
      versionEl = document.getElementById("update-version-label");
      changelogEl = document.getElementById("update-changelog");
      progressTrack = document.getElementById("update-progress-track");
      progressFill = document.getElementById("update-progress-fill");
      progressLabel = document.getElementById("update-progress-label");
      logEl = document.getElementById("update-log");
      installBtn = document.getElementById("update-install-btn");
      laterBtn = document.getElementById("update-later-btn");

      installBtn.addEventListener("click", onInstall);
      laterBtn.addEventListener("click", () => dialog.close());
      // Пока идёт скачивание, Esc не должен незаметно "закрыть" диалог —
      // сама программа всё равно скоро закроется сама (см. update_api.py:
      // _close_app), просто пользователь перестанет видеть, что происходит.
      dialog.addEventListener("cancel", (event) => { if (installing) event.preventDefault(); });

      window.events.on("update_log", (event) => log(event.text));
      window.events.on("update_progress", (event) => setProgress(event.done, event.total));
      window.events.on("update_finished", onFinished);
    }

    function log(text) {
      const line = document.createElement("div");
      line.className = `log-line log-line-${window.classifyLogLevel(text)}`;
      line.textContent = text;
      logEl.appendChild(line);
      logEl.scrollTop = logEl.scrollHeight;
    }

    function formatMb(bytes) {
      return (bytes / 1024 / 1024).toFixed(1);
    }

    function setProgress(done, total) {
      if (total > 0) {
        const percent = Math.min(100, Math.round((done / total) * 100));
        progressFill.style.width = `${percent}%`;
        progressLabel.textContent = `${percent}% · ${formatMb(done)} из ${formatMb(total)} МБ`;
      } else {
        // Content-Length не пришёл (см. update_api.py:_download) — бар
        // остаётся пустым, но подпись всё равно показывает, что процесс
        // не завис, а реально качает.
        progressLabel.textContent = `${formatMb(done)} МБ скачано...`;
      }
    }

    function open(info) {
      installing = false;
      downloadUrl = info.download_url;
      versionEl.textContent = `Версия ${info.version}`;
      changelogEl.textContent = info.changelog || "—";
      progressTrack.style.display = "none";
      progressFill.style.width = "0";
      progressLabel.style.display = "none";
      logEl.style.display = "none";
      logEl.innerHTML = "";
      installBtn.disabled = false;
      installBtn.textContent = "Установить";
      installBtn.style.display = "";
      laterBtn.style.display = "";
      dialog.showModal();
    }

    async function onInstall() {
      installing = true;
      installBtn.disabled = true;
      installBtn.textContent = "Устанавливаю...";
      laterBtn.style.display = "none";
      progressTrack.style.display = "";
      progressLabel.style.display = "";
      logEl.style.display = "";
      const result = await window.pywebview.api.update_install(downloadUrl);
      if (result.ok && result.manual) {
        // macOS (см. update_api.py:install) — тихой переустановки нет,
        // update_install просто открыл .dmg в браузере и на этом всё:
        // update_progress/update_finished тут не придут вообще (это не
        // ошибка, скачивание и правда не начиналось на стороне программы),
        // поэтому не ждём их — сразу показываем итог как есть.
        installing = false;
        installBtn.disabled = false;
        installBtn.textContent = "Установить";
        laterBtn.style.display = "";
        progressTrack.style.display = "none";
        progressLabel.style.display = "none";
        log("Открыл страницу загрузки в браузере — скачайте и замените приложение в Программах вручную.");
        return;
      }
      if (!result.ok) {
        installing = false;
        installBtn.disabled = false;
        installBtn.textContent = "Установить";
        laterBtn.style.display = "";
        log(result.error || "Не удалось начать обновление.");
      }
    }

    function onFinished(event) {
      if (!event.success) {
        installing = false;
        installBtn.disabled = false;
        installBtn.textContent = "Установить";
        laterBtn.style.display = "";
        log(event.message || "Не удалось установить обновление.");
      }
      // При успехе окно программы скоро само закроется (см. update_api.py:
      // _close_app) — показывать тут больше нечего.
    }

    return { init, open };
  })();

  function initDialogs() {
    usb.init();
    report.init();
    adminLogin.init();
    admin.init();
    adminApk.init();
    update.init();
  }

  window.usbDialog = usb;
  window.reportDialog = report;
  window.adminLoginDialog = adminLogin;
  window.adminDialog = admin;
  window.adminApkDialog = adminApk;
  window.updateDialog = update;
  window.initDialogs = initDialogs;
})();
