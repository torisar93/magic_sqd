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
      <section class="settings-section"><h3>Диагностика</h3><p>Лог помогает найти проблему с подключением или установкой.</p><button type="button" data-copy-log>Скопировать лог</button></section>
      <section class="settings-section"><h3>О приложении</h3><a href="https://github.com/torisar93/magic_sqd" target="_blank" rel="noopener">GitHub проекта</a></section>`;
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
      if (!window.confirm("Очистить скачанные файлы и кэш? Их можно будет скачать снова.")) return;
      event.currentTarget.disabled = true;
      const result = await window.pywebview.api.settings_clear_cache();
      dialog.querySelector("[data-cache-size]").textContent = formatBytes(result.remaining_bytes);
      event.currentTarget.textContent = `Освобождено: ${formatBytes(result.freed_bytes)}`;
      await window.mainPicker.reload();
    });
    dialog.querySelector("[data-sync]").addEventListener("click", async (event) => {
      event.currentTarget.disabled = true;
      event.currentTarget.textContent = "Проверяем…";
      await window.pywebview.api.sync_startup();
      await window.mainPicker.reload();
      event.currentTarget.textContent = "Обновления проверены";
    });
    dialog.querySelector("[data-copy-log]").addEventListener("click", async (event) => {
      const text = Array.from(document.querySelectorAll("#log-panel .log-line")).map((line) => line.textContent).join("\n");
      try { await navigator.clipboard.writeText(text); event.currentTarget.textContent = "Лог скопирован"; } catch (_) { window.notice(text || "Лог пока пуст.", { title: "Лог" }); }
    });
    document.documentElement.classList.toggle("reduce-motion", info.preferences.reduced_motion);
    dialog.showModal();
  }

  window.settingsDialog = { open };
})();
