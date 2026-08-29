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
    let drives = [];
    let opts = null; // {modelKey, stageIndex, variant, selectedApkPaths, titleSuffix, onFinished}
    let running = false;

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

      document.getElementById("usb-refresh").addEventListener("click", refreshDrives);
      showAllCheckbox.addEventListener("change", refreshDrives);
      formatCheckbox.addEventListener("change", updateWarning);
      startBtn.addEventListener("click", onStart);
      stopBtn.addEventListener("click", () => {
        window.pywebview.api.usb_cancel();
        log("Останавливаю... (завершится на ближайшей проверке)");
      });
      closeBtn.addEventListener("click", onClose);
      // Esc на нативном <dialog> закрывает его в обход кнопки "Закрыть"
      // (событие "cancel" срабатывает до закрытия) — без этого блокировка
      // кнопки ниже (см. setRunning) можно было бы обойти одной клавишей.
      dialog.addEventListener("cancel", (event) => {
        if (running) event.preventDefault();
      });

      window.events.on("usb_log", (event) => log(event.text));
      window.events.on("usb_finished", onFinished);
    }

    function log(text) {
      const line = document.createElement("div");
      line.className = `log-line log-line-${window.classifyLogLevel(text)}`;
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
      const showAll = showAllCheckbox.checked;
      drives = await window.pywebview.api.usb_list_drives(showAll);
      clear(driveSelect);
      for (const d of drives) {
        const option = document.createElement("option");
        option.value = d.letter;
        option.textContent = d.display;
        driveSelect.appendChild(option);
      }
      driveHintEl.textContent = showAll
        ? "Показаны все локальные диски, кроме системного — будьте внимательны при выборе, чтобы не отформатировать не тот диск."
        : "Показаны только съёмные USB-накопители — системный и внутренние диски в списке не появятся.";
      log(`Найдено дисков: ${drives.length}`);
    }

    function setRunning(value) {
      running = value;
      startBtn.disabled = value;
      stopBtn.disabled = !value;
      driveSelect.disabled = value;
      showAllCheckbox.disabled = value;
      closeBtn.disabled = value;
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

    function onClose() {
      // Пока идёт запись (running), кнопка отключена (см. setRunning) и
      // Esc перехвачен (см. init) — сюда попадаем, только когда процесс уже
      // не выполняется, спрашивать подтверждение не о чем.
      dialog.close();
    }

    function open(newOpts) {
      opts = newOpts;
      document.getElementById("usb-dialog-title").textContent = `USB-флешка — ${opts.titleSuffix}`;
      clear(logEl);
      setRunning(false);
      showAllCheckbox.checked = false; // безопасный дефолт при каждом открытии — не наследуем выбор с прошлого раза
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
      document.getElementById("admin-apk-delete-folder").addEventListener("click", onDeleteFolder);
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

    async function onDeleteFolder() {
      const name = categorySelect.value;
      if (!name) {
        await window.notice("Корневая папка apk/ не удаляется — выберите созданную вами папку.");
        return;
      }
      if (!(await window.confirmDialog(`Удалить папку «${name}» вместе со всеми APK внутри неё? Это только локально — с сервера уже опубликованные файлы не удаляются автоматически.`))) return;
      const result = await window.pywebview.api.admin_delete_apk_category(name);
      if (!result.ok) {
        await window.notice(result.error, { title: "Удаление папки", danger: true });
        return;
      }
      await reloadCategories("");
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
  // Файлы на сервере (только admin_mode, открывается из js/app.js) — см.
  // app/web/api/admin_api.py: browse_server_cars/delete_server_cars_path,
  // server/backend.py: /admin/api/cars/list, /admin/api/cars. Простой
  // файловый браузер по content/cars/ на сервере (модели + _shared/) с
  // удалением — единственный способ убрать то, что уже опубликовано
  // (upload_dir/upload_model только сливают, никогда не удаляют).
  // ==================================================================
  const adminBrowse = (() => {
    let dialog, pathLabel, upBtn, listEl;
    let currentPath = "";

    function init() {
      dialog = document.getElementById("admin-browse-dialog");
      pathLabel = document.getElementById("admin-browse-path");
      upBtn = document.getElementById("admin-browse-up");
      listEl = document.getElementById("admin-browse-list");

      upBtn.addEventListener("click", () => {
        const parts = currentPath.split("/").filter(Boolean);
        parts.pop();
        load(parts.join("/"));
      });
      document.getElementById("admin-browse-refresh").addEventListener("click", () => load(currentPath));
      document.getElementById("admin-browse-close").addEventListener("click", () => dialog.close());
    }

    function formatSize(bytes) {
      if (bytes == null) return "папка";
      if (bytes < 1024) return `${bytes} Б`;
      if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} КБ`;
      return `${(bytes / 1024 ** 2).toFixed(1)} МБ`;
    }

    function renderList(items) {
      clear(listEl);
      if (!items.length) {
        listEl.appendChild(el("p", { class: "app-desc", text: "Пусто." }));
        return;
      }
      for (const item of items) {
        const row = el("div", { class: "row", style: "justify-content: space-between; align-items: center; padding: 4px 0" });
        const label = el("span", {
          text: (item.is_dir ? "▸ " : "") + item.name + `  (${formatSize(item.size)})`,
          style: item.is_dir ? "cursor: pointer; text-decoration: underline" : "",
        });
        if (item.is_dir) {
          label.addEventListener("click", () => load(currentPath ? `${currentPath}/${item.name}` : item.name));
        }
        row.appendChild(label);
        row.appendChild(el("button", { class: "danger", text: "Удалить", onclick: () => onDelete(item) }));
        listEl.appendChild(row);
      }
    }

    async function onDelete(item) {
      const fullPath = currentPath ? `${currentPath}/${item.name}` : item.name;
      const what = item.is_dir ? "папку со всем содержимым" : "файл";
      if (!(await window.confirmDialog(`Удалить ${what} «${item.name}» с сервера? Это необратимо.`))) return;
      const result = await window.pywebview.api.admin_delete_server_cars_path(fullPath);
      if (!result.ok) {
        await window.notice(result.error, { title: "Удаление", danger: true });
        return;
      }
      load(currentPath);
    }

    async function load(path) {
      const result = await window.pywebview.api.admin_browse_server_cars(path);
      if (!result.ok) {
        await window.notice(result.error, { title: "Файлы на сервере", danger: true });
        return;
      }
      currentPath = path;
      pathLabel.textContent = "cars/" + (path ? "/" + path.split("/").join(" / ") : "");
      upBtn.disabled = !path;
      renderList(result.items);
    }

    async function open() {
      const result = await window.pywebview.api.admin_browse_server_cars("");
      if (!result.ok) {
        await window.notice(result.error, { title: "Файлы на сервере", danger: true });
        return;
      }
      currentPath = "";
      pathLabel.textContent = "cars/";
      upBtn.disabled = true;
      renderList(result.items);
      dialog.showModal();
    }

    return { init, open };
  })();

  function initDialogs() {
    usb.init();
    report.init();
    adminLogin.init();
    admin.init();
    adminApk.init();
    adminBrowse.init();
  }

  window.usbDialog = usb;
  window.reportDialog = report;
  window.adminLoginDialog = adminLogin;
  window.adminDialog = admin;
  window.adminApkDialog = adminApk;
  window.adminBrowseDialog = adminBrowse;
  window.initDialogs = initDialogs;
})();
