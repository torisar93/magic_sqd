(function () {
  let opening = false;
  function formatBytes(value) {
    if (!value) return "0 Б";
    const units = ["Б", "КБ", "МБ", "ГБ"];
    let size = value, index = 0;
    while (size >= 1024 && index < units.length - 1) { size /= 1024; index++; }
    return `${size >= 100 || index === 0 ? Math.round(size) : size.toFixed(1)} ${units[index]}`;
  }
  function checkbox(label, hint, key, checked, save, report) {
    const row = document.createElement("label");
    row.className = "settings-toggle settings13-toggle";
    const copy = document.createElement("span");
    copy.className = "settings13-toggle-copy";
    const title = document.createElement("strong"); title.textContent = label; copy.append(title);
    if (hint) { const detail = document.createElement("small"); detail.textContent = hint; copy.append(detail); }
    const input = document.createElement("input");
    input.type = "checkbox"; input.setAttribute("role", "switch"); input.dataset.preference = key; input.checked = !!checked;
    input.addEventListener("change", async () => {
      const previous = !input.checked;
      input.disabled = true;
      try { await save(key, input.checked); report(""); }
      catch (error) { input.checked = previous; report(error.message || "Не удалось сохранить настройку.", true); }
      finally { input.disabled = false; }
    });
    row.append(copy, input); return row;
  }
  async function open() {
    if (opening || document.querySelector(".settings-dialog[open]")) return;
    opening = true;
    let info;
    try { info = await window.pywebview.api.settings_info(); }
    catch (_) { window.notice("Не удалось загрузить настройки. Попробуйте открыть их ещё раз.", { title: "Настройки", danger: true }); return; }
    finally { opening = false; }
    const preferences = info.preferences || {};
    const dialog = document.createElement("dialog");
    dialog.className = "settings-dialog settings13-dialog";
    dialog.setAttribute("aria-labelledby", "settings13-title");
    dialog.innerHTML = `<header class="settings13-heading"><span class="menus13-symbol" data-icon="settings"></span><div><h2 id="settings13-title">Настройки</h2><p>Ваш Magic SQD</p></div><button class="settings-close" type="button" aria-label="Закрыть настройки" data-icon="close"></button></header>
      <div class="settings13-body">
        <section class="settings-section settings13-storage"><div class="settings13-section-title"><span data-icon="folder"></span><h3>Хранилище</h3></div><div class="settings13-metrics"><div><span>Приложение</span><strong data-app-size></strong></div><div><span>Загруженные файлы</span><strong data-cache-size></strong></div></div><div class="settings13-storage-action"><p>APK, файлы моделей и временные логи. Сценарии и настройки сохранятся.</p><button type="button" data-clear><span data-icon="trash"></span><span data-label>Очистить кэш</span></button></div></section>
        <section class="settings-section"><div class="settings13-section-title"><span data-icon="refresh"></span><h3>Каталог</h3><span class="settings13-connection" data-connection></span></div><div data-sync-toggle></div><button class="settings13-wide-action" type="button" data-sync><span data-icon="refresh"></span><span data-label>Проверить обновления</span><span class="settings13-action-tail" data-icon="chevron"></span></button></section>
        <section class="settings-section"><div class="settings13-section-title"><span data-icon="apps"></span><h3>Интерфейс и лог</h3></div><div data-toggles></div></section>
        <section class="settings-section"><div class="settings13-section-title"><span data-icon="terminal"></span><h3>Диагностика</h3></div><div data-debug-toggle></div><button class="settings13-wide-action" type="button" data-copy-log><span data-icon="copy"></span><span data-label>Скопировать лог</span><span class="settings13-action-tail" data-icon="chevron"></span></button></section>
        <p class="settings13-status" data-settings-status role="status" aria-live="polite" hidden></p>
      </div><footer class="settings13-footer"><div><strong data-version></strong><span data-admin-status></span></div><a href="https://github.com/torisar93/magic_sqd" target="_blank" rel="noopener"><span data-icon="link"></span>GitHub проекта</a></footer>`;
    dialog.querySelectorAll("[data-icon]").forEach(node => node.append(window.AppIcons.icon(node.dataset.icon)));
    dialog.querySelector("[data-app-size]").textContent = formatBytes(info.app_bytes);
    dialog.querySelector("[data-cache-size]").textContent = formatBytes(info.cache_bytes);
    dialog.querySelector("[data-version]").textContent = `Magic SQD · v${info.app_version}`;
    dialog.querySelector("[data-admin-status]").textContent = info.admin_mode ? "Режим администратора" : "Приложения для вашей магнитолы";
    const connection = dialog.querySelector("[data-connection]");
    connection.textContent = info.server_configured ? "Сервер подключён" : "Сервер не настроен";
    connection.classList.toggle("is-connected", !!info.server_configured);
    const report = (message, error = false) => {
      const status = dialog.querySelector("[data-settings-status]");
      status.textContent = message; status.hidden = !message; status.dataset.state = error ? "error" : "success";
      if (message) status.scrollIntoView({ block: "nearest" });
    };
    document.body.appendChild(dialog);
    const previousFocus = document.activeElement;
    dialog.addEventListener("close", () => { dialog.remove(); previousFocus?.focus({ preventScroll: true }); }, { once: true });
    dialog.querySelector(".settings-close").addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", event => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
    });
    async function savePreference(key, value) {
      const result = await window.pywebview.api.settings_set_preferences({ [key]: value });
      document.documentElement.classList.toggle("reduce-motion", result.reduced_motion);
      window.chatPanel.setEnabled(result.chat_enabled);
    }
    const toggle = (label, hint, key, checked, save = savePreference) => checkbox(label, hint, key, checked, save, report);
    dialog.querySelector("[data-sync-toggle]").append(toggle("Обновлять при запуске", "Свежие модели и инструкции автоматически", "auto_sync", preferences.auto_sync));
    dialog.querySelector("[data-toggles]").append(
      toggle("Уменьшить анимации", "Спокойные переходы без лишнего движения", "reduced_motion", preferences.reduced_motion),
      toggle("Компактный лог", "Больше событий в одном окне", "compact_log", preferences.compact_log),
      toggle("Чат с ИИ", "Вопросы помощнику прямо в окне лога", "chat_enabled", preferences.chat_enabled));
    async function action(button, busy, success, perform) {
      const label = button.querySelector("[data-label]"), original = label.textContent;
      button.disabled = true; button.setAttribute("aria-busy", "true"); label.textContent = busy; report("");
      try { await perform(); report(success); }
      catch (error) { report(error.message || "Не удалось выполнить действие. Попробуйте ещё раз.", true); }
      finally { button.disabled = false; button.removeAttribute("aria-busy"); label.textContent = original; }
    }
    dialog.querySelector("[data-clear]").addEventListener("click", async event => {
      const button = event.currentTarget;
      if (!(await window.confirmDialog("Очистить скачанные файлы и кэш? Их можно будет скачать снова."))) return;
      await action(button, "Очищаем…", "Кэш очищен.", async () => {
        const result = await window.pywebview.api.settings_clear_cache();
        dialog.querySelector("[data-cache-size]").textContent = formatBytes(result.remaining_bytes);
        await window.mainPicker.reload();
      });
    });
    dialog.querySelector("[data-sync]").addEventListener("click", event => action(event.currentTarget, "Проверяем…", "Каталог обновлён. Проверка завершена.", async () => {
      let timeout;
      try {
        await Promise.race([window.pywebview.api.sync_startup(), new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error("Сервер не ответил. Проверьте подключение и повторите проверку.")), 45000); })]);
        await window.mainPicker.reload();
      } finally { clearTimeout(timeout); }
    }));
    dialog.querySelector("[data-copy-log]").addEventListener("click", event => action(event.currentTarget, "Копируем…", "Лог скопирован.", async () => {
      const text = Array.from(document.querySelectorAll("#log-panel .log-line")).map(line => line.textContent).join("\n");
      if (!text) throw new Error("Лог пока пуст.");
      try { await navigator.clipboard.writeText(text); }
      catch (_) { window.notice(text, { title: "Лог" }); throw new Error("Копирование недоступно. Лог открыт в отдельном окне."); }
    }));
    dialog.querySelector("[data-debug-toggle]").append(toggle("Подробное логирование", "Для диагностики · со следующего запуска", "debug_mode", info.debug_mode, async (_key, value) => {
      const result = await window.pywebview.api.settings_set_debug_mode(value);
      if (result.write_failed) {
        const hint = info.under_program_files ? " Программа установлена в Program Files. Запустите её один раз от имени администратора или установите в AppData." : " Запустите программу от имени администратора или установите в папку с доступом на запись.";
        throw new Error("Не удалось сохранить настройку: нет прав на запись в папку программы." + hint);
      }
    }));
    document.documentElement.classList.toggle("reduce-motion", !!preferences.reduced_motion);
    dialog.showModal();
  }
  window.settingsDialog = { open };
})();
