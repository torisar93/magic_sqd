/* Общее окно «этап выполняется → результат». Файл ОДИНАКОВЫЙ на десктопе
   (app/web/frontend/js/components/stage_run.js) и на Android
   (android/app/src/main/assets/js/stage_run.js) — правьте оба сразу
   (cmp должен молчать). Зависит только от LabUI.busy из progress08.js
   (кольцо-спиннер и очередь приложений); события прогресса по-прежнему
   обрабатывает LabUI.progress — он находит окно по .is-running и
   dataset.stageIndex.

   Пока этап идёт — крутится спиннер и (по желанию) видна «Остановить».
   Когда закончился — то же окно превращается в итог человеческим языком:
   успешно / с ошибкой / остановлено. При ошибке всегда показываем подсказку
   про лог и разработчика, кнопки «Открыть лог» и «Скопировать лог». */
(() => {
  const n = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null && text !== "") node.textContent = text;
    return node;
  };

  const FAILURE_HINT = "Если не понимаете, что произошло, откройте лог, скопируйте его и отправьте разработчику.";
  const config = { openLog: null, getLogText: null };
  let active = null;

  function openLog() {
    if (typeof config.openLog === "function") return config.openLog();
    if (typeof window.setLogExpanded === "function") window.setLogExpanded(true);
  }

  function logText() {
    if (typeof config.getLogText === "function") return config.getLogText();
    return document.getElementById("log-panel")?.innerText || "";
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // WebView без прав на clipboard API — старый способ через выделение.
      const area = n("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.cssText = "position:fixed;left:-9999px;top:0;opacity:0";
      document.body.append(area);
      area.select();
      let copied = false;
      try { copied = document.execCommand("copy"); } catch { copied = false; }
      area.remove();
      return copied;
    }
  }

  function logButtons() {
    const openBtn = n("button", "", "Открыть лог");
    openBtn.type = "button";
    openBtn.onclick = openLog;
    const copyBtn = n("button", "", "Скопировать лог");
    copyBtn.type = "button";
    copyBtn.onclick = async () => {
      const ok = await copyText(logText());
      copyBtn.textContent = ok ? "Лог скопирован" : "Не удалось скопировать";
      setTimeout(() => { copyBtn.textContent = "Скопировать лог"; }, 2500);
    };
    return [openBtn, copyBtn];
  }

  // Блок «что делать, если непонятно» — тот же текст и кнопки, что и в итоге
  // окна этапа; используется и там, где окно своё (диалог записи на флешку).
  function hintBlock() {
    const block = n("div", "stage-run-hint-block");
    block.append(n("p", "stage-run-hint", FAILURE_HINT));
    const actions = n("div", "stage-run-actions");
    actions.append(...logButtons());
    block.append(actions);
    return block;
  }

  function ringIcon(name) {
    try {
      return window.AppIcons ? window.AppIcons.icon(name) : window.LabUI.symbol(name);
    } catch {
      return null;
    }
  }

  function open(options = {}) {
    if (active) active.dispose();
    const previousFocus = document.activeElement;
    const overlay = n("div", "stage-run-overlay");
    const box = n("div", "stage-run-box");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-modal", "true");
    box.setAttribute("aria-label", options.title || "Выполняется этап");
    box.tabIndex = -1;
    const host = n("div", "stage-run-host");
    host.dataset.stageIndex = options.stageIndex == null ? "" : String(options.stageIndex);
    box.append(host);
    overlay.append(box);
    document.body.append(overlay);

    const items = options.items || [];
    const status = window.LabUI.busy(host, options.title || "Выполняется этап", items);
    const liveDetail = !items.length;
    if (!items.length) {
      // Без очереди приложений это простой «идёт процесс»: счётчик не нужен,
      // подпись фазы — только если её задали.
      status.querySelector(".install-count")?.remove();
      const glyph = status.querySelector(".install-center>.ui-icon");
      const custom = options.icon ? ringIcon(options.icon) : null;
      if (glyph && custom) glyph.replaceWith(custom);
      const phase = status.querySelector(".install-phase-detail");
      if (phase) phase.textContent = options.subtitle || "";
    }
    const eventLine = status.querySelector(".run-event");
    if (eventLine) eventLine.textContent = options.detail || "Ожидаем ответ устройства";

    let finished = false;
    let result = null;
    let closed = false;
    let stopBtn = null;
    let cancelRequested = false;

    if (options.cancellable) {
      const actions = n("div", "stage-run-actions");
      stopBtn = n("button", "danger", "Остановить");
      stopBtn.type = "button";
      stopBtn.onclick = () => {
        cancelRequested = true;
        stopBtn.disabled = true;
        stopBtn.textContent = "Останавливаем…";
        if (typeof options.onCancel === "function") options.onCancel();
      };
      actions.append(stopBtn);
      box.append(actions);
    }

    function focusables() {
      return [...box.querySelectorAll("button:not([disabled]),summary,a[href]")];
    }

    function close() {
      if (closed) return;
      closed = true;
      overlay.remove();
      document.removeEventListener("keydown", onKey, true);
      if (active === run) active = null;
      if (previousFocus && previousFocus.isConnected && typeof previousFocus.focus === "function") {
        try { previousFocus.focus(); } catch { /* элемент мог исчезнуть */ }
      }
      if (result && typeof options.onClose === "function") options.onClose(result);
    }

    function onKey(event) {
      // Поверх окна может лежать родной <dialog> (лог, вопрос этапа ask_input) —
      // клавиши тогда его, а не наши: иначе Esc не закрыл бы лог.
      if (document.querySelector("dialog[open]")) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        if (finished) close();
        return;
      }
      if (event.key === "Tab") {
        const list = focusables();
        if (!list.length) { event.preventDefault(); return; }
        const first = list[0];
        const last = list[list.length - 1];
        if (event.shiftKey && (document.activeElement === first || !box.contains(document.activeElement))) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && (document.activeElement === last || !box.contains(document.activeElement))) {
          event.preventDefault(); first.focus();
        }
      }
    }
    document.addEventListener("keydown", onKey, true);
    overlay.addEventListener("mousedown", (event) => {
      if (event.target === overlay && finished) close();
    });

    function finish(outcome = {}) {
      if (finished) return;
      finished = true;
      // Бэкенд сообщает об остановке обычным «неуспехом» (см. app/runner.py:
      // InstallCancelled) — отличаем по тому, что техник сам нажал «Остановить».
      const cancelled = !!outcome.cancelled || (!outcome.success && cancelRequested);
      result = {
        success: !!outcome.success && !cancelled,
        cancelled,
        message: outcome.message ? String(outcome.message) : "",
      };
      host.classList.remove("is-running");
      const queue = host.querySelector(".run-queue");
      const summary = host.querySelector(".queue-summary");
      host.replaceChildren();
      box.querySelectorAll(":scope > .stage-run-actions").forEach((node) => node.remove());
      const state = result.success ? "success" : result.cancelled ? "cancelled" : "error";
      const view = n("div", "stage-run-result");
      view.dataset.state = state;
      view.setAttribute("role", state === "error" ? "alert" : "status");
      view.append(n("div", "stage-run-symbol", { success: "✓", cancelled: "■", error: "!" }[state]));
      view.append(n("h2", "stage-run-title",
        { success: "Готово", cancelled: "Остановлено", error: "Не удалось завершить этап" }[state]));

      const message = result.message.trim();
      const shortMessage = message && message.length <= 200 && !message.includes("\n");
      if (state === "success") {
        view.append(n("p", "stage-run-message", shortMessage ? message : "Этап выполнен успешно."));
      } else if (state === "cancelled") {
        view.append(n("p", "stage-run-message", "Этап остановлен, не завершив работу. Можно запустить его заново."));
      } else {
        view.append(n("p", "stage-run-message", shortMessage ? message : "Этап не выполнен."));
        view.append(n("p", "stage-run-hint", FAILURE_HINT));
        if (message && !shortMessage) {
          const details = n("details", "stage-run-details");
          details.append(n("summary", "", "Технические подробности"), n("pre", "", message));
          view.append(details);
        }
      }
      if (queue) {
        const wrap = n("div", "progress08 stage-run-queue");
        if (summary) wrap.append(summary);
        wrap.append(queue);
        view.append(wrap);
      }

      const actions = n("div", "stage-run-actions");
      let primary;
      if (state === "success") {
        primary = n("button", "accent", options.continueLabel || "Продолжить");
        primary.type = "button";
        primary.onclick = close;
        actions.append(primary);
      } else {
        if (state === "error") actions.append(...logButtons());
        if (state === "error" && typeof options.retry === "function") {
          const retry = n("button", "accent", "Повторить");
          retry.type = "button";
          retry.onclick = () => { close(); setTimeout(options.retry, 0); };
          actions.append(retry);
          primary = retry;
        }
        const closeBtn = n("button", primary ? "" : "accent", "Закрыть");
        closeBtn.type = "button";
        closeBtn.onclick = close;
        actions.append(closeBtn);
        primary = primary || closeBtn;
      }
      view.append(actions);
      host.append(view);
      primary.focus();
    }

    const run = {
      host,
      // Одна «живая» строка под заголовком — последнее событие этапа. Для
      // очереди приложений подпись ведёт сам LabUI.progress (имя приложения).
      detail(text) {
        if (finished || !liveDetail || !text) return;
        const line = host.querySelector(".run-event");
        if (line) line.textContent = String(text).slice(0, 300);
      },
      finish,
      close,
      dispose() { finished = true; result = null; close(); },
      get finished() { return finished; },
      get stopButton() { return stopBtn; },
    };
    active = run;
    box.focus();
    return run;
  }

  window.StageRun = {
    open,
    hintBlock,
    failureHint: FAILURE_HINT,
    current: () => active,
    configure: (options) => Object.assign(config, options || {}),
  };
})();
