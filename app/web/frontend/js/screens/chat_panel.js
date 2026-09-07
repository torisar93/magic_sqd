// Чат с ИИ — прямо в логе установки (см. app/chat_client.py,
// app/web/api/chat_api.py, server/backend.py: POST /chat). Никакой
// отдельной панели/пузырей/переключателя режима — один и тот же ряд ввода
// под логом, что и у мини-консоли ADB (см. app.js: isChatQuestion/
// sendConsoleInput — кириллица в тексте однозначно определяет вопрос ИИ,
// не команду). Вопрос техника, ответ и предложенная команда появляются
// обычными строками в #log-panel, той же карточке #log-card, что и вывод
// adb. Команда, предложенная моделью, рендерится отдельной строкой с
// кнопками "Выполнить"/"Отклонить" — ничего не выполняется без явного
// клика техника. После подтверждения результат уходит обратно в
// переписку, и чат сам продолжает диалог (агентный цикл) — до
// MAX_AUTO_TURNS автоматических шагов подряд на одно сообщение техника,
// дальше нужно написать заново.
window.chatPanel = (() => {
  const MAX_AUTO_TURNS = 6;
  const HISTORY_SENT_TURNS = 16;
  const RECENT_LOG_LINES = 40;

  let history = [];
  let autoTurnsLeft = 0;
  let panelEl;
  let enabled = true;
  // Принудительный выбор провайдера — команды /deepseek, /qwen, /auto
  // (см. sendMessage ниже). null — обычный автоматический режим (DeepSeek
  // первым, Qwen запасным на сервере, см. backend.py:_handle_chat).
  // "Липкий" — раз выбранный держится для всех следующих сообщений этой
  // сессии чата, пока не сменят явно другой командой.
  let forcedProvider = null;
  const PROVIDER_COMMAND_RE = /^\/(deepseek|qwen|auto)\b\s*(.*)$/is;
  const PROVIDER_LABELS = { deepseek: "DeepSeek", qwen: "Qwen" };

  function addLine(text, extraClass) {
    const line = document.createElement("div");
    line.className = `log-line${extraClass ? ` ${extraClass}` : ""}`;
    line.textContent = text;
    panelEl.appendChild(line);
    panelEl.scrollTop = panelEl.scrollHeight;
    return line;
  }

  function recentLogLines() {
    const lines = Array.from(panelEl.querySelectorAll(".log-line")).map((el) => el.textContent);
    return lines.slice(-RECENT_LOG_LINES);
  }

  // Устройство берём из уже существующего селектора мини-консоли ADB (см.
  // app.js: adbConsoleDeviceByLabel/#adb-console-device) — не заводим
  // отдельный выбор устройства только для чата.
  function selectedDevice() {
    const select = document.getElementById("adb-console-device");
    if (!select || typeof adbConsoleDeviceByLabel === "undefined") return null;
    return adbConsoleDeviceByLabel[select.value] || null;
  }

  function renderCommandLine(command, reason, providerTag) {
    const line = document.createElement("div");
    line.className = "log-line chat-command-line";

    const commandEl = document.createElement("code");
    commandEl.textContent = `❯${providerTag || ""} ${command}`;
    line.appendChild(commandEl);

    if (reason) {
      const reasonEl = document.createElement("span");
      reasonEl.className = "chat-command-reason";
      reasonEl.textContent = reason;
      line.appendChild(reasonEl);
    }

    const actions = document.createElement("div");
    actions.className = "chat-command-actions";
    const confirmBtn = document.createElement("button");
    confirmBtn.className = "accent";
    confirmBtn.textContent = "Выполнить";
    const rejectBtn = document.createElement("button");
    rejectBtn.textContent = "Отклонить";
    actions.append(confirmBtn, rejectBtn);
    line.appendChild(actions);

    panelEl.appendChild(line);
    panelEl.scrollTop = panelEl.scrollHeight;

    confirmBtn.addEventListener("click", () => {
      confirmBtn.disabled = true;
      rejectBtn.disabled = true;
      window.pywebview.api.chat_confirm_command(selectedDevice(), command);
    });
    rejectBtn.addEventListener("click", () => {
      confirmBtn.disabled = true;
      rejectBtn.disabled = true;
      addLine("Команда отклонена — не выполнена.");
      history.push({ role: "tool_result", command, output: "техник отклонил выполнение команды", ok: false });
    });
  }

  function sendTurn() {
    if (autoTurnsLeft <= 0) {
      addLine("ИИ: достигнут лимит автоматических шагов подряд — напишите новое сообщение, чтобы продолжить.");
      return;
    }
    autoTurnsLeft -= 1;
    window.pywebview.api.chat_send(history.slice(-HISTORY_SENT_TURNS), recentLogLines(), forcedProvider).then((result) => {
      if (!result || !result.ok) {
        addLine(`ИИ: ${(result && result.error) || "не удалось отправить сообщение"}`, "log-line-error");
      }
      // При успехе ответ придёт отдельным событием "chat_reply" — сама
      // chat_send() в Python работает в фоновом потоке и возвращается сразу.
    });
  }

  function sendMessage(text) {
    if (!enabled) {
      addLine("ИИ-чат отключён в настройках.", "log-line-error");
      return;
    }
    // /deepseek, /qwen — принудительно закрепить провайдера на все
    // следующие сообщения этой сессии (без автофолбэка на сервере при его
    // ошибке); /auto — вернуть обычный режим. Если после команды есть ещё
    // текст ("/qwen почему упала установка?") — это уже реальный вопрос,
    // отправляется сразу с новым провайдером; если команда одна — просто
    // подтверждение режима, к ИИ ничего не уходит.
    const commandMatch = PROVIDER_COMMAND_RE.exec(text);
    if (commandMatch) {
      const mode = commandMatch[1].toLowerCase();
      const rest = commandMatch[2].trim();
      forcedProvider = mode === "auto" ? null : mode;
      addLine(forcedProvider
        ? `Режим чата: только ${PROVIDER_LABELS[forcedProvider]}, без автопереключения.`
        : "Режим чата: автоматический (DeepSeek, при недоступности — Qwen).");
      if (!rest) return;
      text = rest;
    }
    addLine(`💬 ${text}`, "log-line-command");
    history.push({ role: "user", content: text });
    autoTurnsLeft = MAX_AUTO_TURNS;
    sendTurn();
  }

  function onChatReply(event) {
    const reply = event.reply || {};
    const providerTag = reply.provider && PROVIDER_LABELS[reply.provider]
      ? ` [${PROVIDER_LABELS[reply.provider]}]` : "";
    if (reply.type === "command" && reply.command) {
      const summary = `Предлагаю выполнить: ${reply.command}` + (reply.reason ? ` — ${reply.reason}` : "");
      history.push({ role: "assistant", content: summary });
      renderCommandLine(reply.command, reply.reason, providerTag);
      return;
    }
    const text = reply.content || "";
    addLine(`ИИ${providerTag}: ${text}`);
    history.push({ role: "assistant", content: text });
  }

  function onChatCommandResult(event) {
    addLine(event.output || "(пусто)", event.ok ? undefined : "log-line-error");
    history.push({ role: "tool_result", command: event.command, output: event.output, ok: !!event.ok });
    // Замыкаем агентный цикл — модель видит результат и может предложить
    // следующий шаг или завершить обычным текстовым ответом.
    sendTurn();
  }

  function init(chatEnabled) {
    enabled = chatEnabled;
    panelEl = document.getElementById("log-panel");
    window.events.on("chat_reply", onChatReply);
    window.events.on("chat_command_result", onChatCommandResult);
  }

  function setEnabled(chatEnabled) {
    enabled = chatEnabled;
  }

  return { init, sendMessage, setEnabled };
})();
