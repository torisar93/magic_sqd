(function () {
  function formatBytes(value) {
    if (!value) return "0 Б";
    const units = ["Б", "КБ", "МБ", "ГБ"];
    let size = value;
    let index = 0;
    while (size >= 1024 && index < units.length - 1) { size /= 1024; index += 1; }
    return `${size >= 100 || index === 0 ? Math.round(size) : size.toFixed(1)} ${units[index]}`;
  }

  function checkbox(label, key, checked, save) {
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = checked;
    input.addEventListener("change", () => save(key, input.checked));
    const row = document.createElement("label");
    row.className = "settings-toggle";
    row.append(input, document.createTextNode(label));
    return row;
  }

  async function open() {
    const info = await window.pywebview.api.settings_info();
    const dialog = document.createElement("dialog");
    dialog.className = "settings-dialog";
    dialog.innerHTML = `<header><button class="settings-close" type="button" aria-label="Закрыть">×</button><h2>Настройки</h2></header>
      <section class="settings-section"><h3>Хранилище</h3><p>Приложение: <strong>${formatBytes(info.app_bytes)}</strong> · кэш: <strong data-cache-size>${formatBytes(info.cache_bytes)}</strong></p><button class="danger" type="button" data-clear>Очистить кэш</button><small>Удаляются загруженные APK, файлы моделей и временные логи. Сценарии и настройки останутся на месте.</small></section>
      <section class="settings-section"><h3>Синхронизация</h3><p>${info.server_configured ? "Сервер подключён" : "Сервер не настроен"}</p><button type="button" data-sync>Проверить обновления сейчас</button><div data-toggles></div></section>
      <section class="settings-section"><h3>Диагностика</h3><p>Лог помогает найти проблему с подключением или установкой.</p><button type="button" data-copy-log>Скопировать лог</button><div data-debug-toggle></div></section>
      <section class="settings-section"><h3>О приложении</h3><p data-version class="settings-version">Magic SQD v${info.app_version}</p><p data-admin-status class="settings-admin-status"></p><div data-admin-logout-row></div><a href="https://github.com/torisar93/magic_sqd" target="_blank" rel="noopener">GitHub проекта</a></section>`;
    document.body.appendChild(dialog);
    const close = () => { dialog.close(); dialog.remove(); };
    dialog.querySelector(".settings-close").addEventListener("click", close);
    dialog.addEventListener("click", (event) => { if (event.target === dialog) close(); });
    dialog.querySelector("[data-toggles]").append(
      checkbox("Обновлять каталог при запуске", "auto_sync", info.preferences.auto_sync, savePreference),
      checkbox("Уменьшить анимации", "reduced_motion", info.preferences.reduced_motion, savePreference),
      checkbox("Компактный лог", "compact_log", info.preferences.compact_log, savePreference),
    );
    async function savePreference(key, value) {
      const preferences = await window.pywebview.api.settings_set_preferences({ [key]: value });
      document.documentElement.classList.toggle("reduce-motion", preferences.reduced_motion);
    }
    dialog.querySelector("[data-clear]").addEventListener("click", async (event) => {
      if (!(await window.confirmDialog("Очистить скачанные файлы и кэш? Их можно будет скачать снова."))) return;
      event.currentTarget.disabled = true;
      const result = await window.pywebview.api.settings_clear_cache();
      dialog.querySelector("[data-cache-size]").textContent = formatBytes(result.remaining_bytes);
      event.currentTarget.textContent = `Освобождено: ${formatBytes(result.freed_bytes)}`;
      await window.mainPicker.reload();
    });
    dialog.querySelector("[data-sync]").addEventListener("click", async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.textContent = "Проверяем…";
      let timeout;
      try {
        await Promise.race([
          window.pywebview.api.sync_startup(),
          new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error("Превышено время ожидания сервера")), 45000); }),
        ]);
        await window.mainPicker.reload();
        button.textContent = "Обновления проверены";
      } catch (error) {
        console.error("Не удалось проверить обновления:", error);
        button.textContent = "Не удалось проверить";
      } finally {
        clearTimeout(timeout);
        button.disabled = false;
      }
    });
    dialog.querySelector("[data-copy-log]").addEventListener("click", async (event) => {
      const text = Array.from(document.querySelectorAll("#log-panel .log-line")).map((line) => line.textContent).join("\n");
      try { await navigator.clipboard.writeText(text); event.currentTarget.textContent = "Лог скопирован"; } catch (_) { window.notice(text || "Лог пока пуст.", { title: "Лог" }); }
    });

    // Подробное логирование (см. main_web.py:_enable_debug_log_all) —
    // маркер-файл читается только при старте программы, поэтому изменение
    // здесь применяется со следующего запуска, не сразу.
    const debugCheckbox = checkbox("Подробное логирование (для диагностики, со следующего запуска)",
      "debug_mode", info.debug_mode, async (_key, value) => {
        await window.pywebview.api.settings_set_debug_mode(value);
      });
    dialog.querySelector("[data-debug-toggle]").append(debugCheckbox);

    // Разблокировка функций администратора — 10 тапов подряд по версии (см.
    // dialogs.js: adminLogin.openUnlock). Раньше для этого ставилась
    // отдельная admin-сборка — теперь одна программа для всех.
    const adminStatusEl = dialog.querySelector("[data-admin-status]");
    adminStatusEl.textContent = info.admin_mode ? "Функции администратора включены." : "";
    if (info.admin_mode) {
      const logoutBtn = document.createElement("button");
      logoutBtn.type = "button";
      logoutBtn.className = "danger";
      logoutBtn.textContent = "Выйти из режима администратора";
      logoutBtn.addEventListener("click", async () => {
        if (!(await window.confirmDialog(
          "Выключить функции администратора на этой машине? Сохранённый вход будет забыт — "
          + "чтобы включить снова, потребуется войти заново через 10 тапов по версии."))) return;
        logoutBtn.disabled = true;
        await window.pywebview.api.admin_logout();
        window.applyAdminMode(false);
        close();
        window.notice("Функции администратора выключены.");
      });
      dialog.querySelector("[data-admin-logout-row]").append(logoutBtn);
    }
    let tapCount = 0;
    let tapTimer = null;
    dialog.querySelector("[data-version]").addEventListener("click", () => {
      tapCount += 1;
      clearTimeout(tapTimer);
      tapTimer = setTimeout(() => { tapCount = 0; }, 2000);
      if (tapCount < 10) return;
      tapCount = 0;
      close();
      window.adminLoginDialog.openUnlock(() => {
        window.applyAdminMode(true);
        window.notice("Функции администратора включены.");
      });
    });

    document.documentElement.classList.toggle("reduce-motion", info.preferences.reduced_motion);
    dialog.showModal();
  }

  window.settingsDialog = { open };
})();
