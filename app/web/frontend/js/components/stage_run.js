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
   про лог и разработчика, кнопки «Открыть лог» и «Скопировать лог» — кроме
   ошибок на стороне техника (user_errors.js: нет магнитолы, флешки, интернета…):
   для них — что случилось и что сделать, без «отправьте разработчику». */
(() => {
  const n = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null && text !== "") node.textContent = text;
    return node;
  };

  const FAILURE_HINT = "Если не понимаете, что произошло, откройте лог, скопируйте его и отправьте разработчику.";
  const USER_ERROR_NOTE = "Это не сбой программы: выполните шаги выше и повторите.";
  // onUserError(правило, текст ошибки) — платформа пишет в лог сессии, какое окно увидел техник.
  const config = { openLog: null, getLogText: null, onUserError: null };
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

  // Ошибка на стороне техника? id — явное правило (проверка до запуска этапа), иначе — по тексту ошибки.
  function userErrorFor(id, message) {
    const catalog = window.UserErrors;
    if (!catalog) return null;
    return (id && catalog.byId(id)) || catalog.classify(message);
  }

  function reportUserError(rule, message) {
    if (typeof config.onUserError !== "function") return;
    try { config.onUserError(rule, message); } catch { /* запись в лог не должна ломать окно */ }
  }

  function stepsNode(rule) {
    const box = n("div", "stage-run-steps");
    box.append(n("p", "stage-run-steps-title", "Что сделать"));
    const list = n("ol");
    rule.steps.forEach((step) => list.append(n("li", "", step)));
    box.append(list);
    return box;
  }

  function userErrorView(rule, message) {
    const view = n("div", "stage-run-result");
    view.dataset.state = "user";
    view.dataset.userError = rule.id;
    view.setAttribute("role", "alert");
    const symbol = n("div", "stage-run-symbol");
    const glyph = rule.icon ? ringIcon(rule.icon) : null;
    if (glyph) symbol.append(glyph);
    else symbol.textContent = "!";
    view.append(symbol, n("h2", "stage-run-title", rule.title));
    const lead = rule.keepMessage && message ? message : rule.text;
    if (lead) view.append(n("p", "stage-run-message", lead));
    if (rule.steps.length) view.append(stepsNode(rule));
    view.append(n("p", "stage-run-note", USER_ERROR_NOTE));
    if (message && message !== lead) {
      const details = n("details", "stage-run-details");
      details.append(n("summary", "", "Технические подробности"), n("pre", "", message));
      view.append(details);
    }
    return view;
  }

  // Для окон со своей разметкой (диалог записи на флешку на ПК): «что сделать» вместо hintBlock.
  function userErrorBlock(message) {
    const rule = userErrorFor(null, message);
    if (!rule) return null;
    const block = n("div", "stage-run-hint-block stage-run-user-block");
    if (rule.steps.length) block.append(stepsNode(rule));
    block.append(n("p", "stage-run-note", USER_ERROR_NOTE));
    reportUserError(rule, message);
    return { rule, node: block };
  }

  // Окно «что сделать» без запуска этапа — ошибка ясна ещё до старта (не выбрана магнитола, нет ADB).
  // Идущий этап не трогает: поверх незавершённого окна ничего не открываем.
  function showUserError(input = {}, options = {}) {
    if (active && !active.finished) return null;
    const message = input.message ? String(input.message) : "";
    const rule = userErrorFor(input.id, message);
    if (!rule) return null;
    const run = open({ title: rule.title, retry: options.retry, onClose: options.onClose });
    run.finish({ success: false, message, userError: rule.id });
    return run;
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
      const message = result.message.trim();
      const userError = state === "error" ? userErrorFor(outcome.userError, message) : null;
      let view;
      if (userError) {
        view = userErrorView(userError, message);
        box.setAttribute("aria-label", userError.title);
        reportUserError(userError, message);
      } else {
        view = n("div", "stage-run-result");
        view.dataset.state = state;
        view.setAttribute("role", state === "error" ? "alert" : "status");
        view.append(n("div", "stage-run-symbol", { success: "✓", cancelled: "■", error: "!" }[state]));
        view.append(n("h2", "stage-run-title",
          { success: "Готово", cancelled: "Остановлено", error: "Не удалось завершить этап" }[state]));
      }

      const shortMessage = message && message.length <= 200 && !message.includes("\n");
      if (userError) {
        // Текст, шаги и подробности уже собраны в userErrorView.
      } else if (state === "success") {
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
        // Ошибке на стороне техника лог не нужен — всё сказано в окне; лог по-прежнему открывается из программы.
        if (state === "error" && !userError) actions.append(...logButtons());
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
      // Без прокрутки к кнопке: на узком экране длинный итог (шаги «что сделать») иначе открывался бы
      // уже прокрученным вниз, без заголовка.
      primary.focus({ preventScroll: true });
      box.scrollTop = 0;
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
    showUserError,
    userErrorBlock,
    failureHint: FAILURE_HINT,
    current: () => active,
    configure: (options) => Object.assign(config, options || {}),
  };
})();
