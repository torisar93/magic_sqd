// ИИ-мастер («Установка с ИИ», платная функция для подписчиков Boosty) — общее ядро чата ПК и Android.
//
// Ядро рисует чат в данном ему контейнере и ведёт протокол с сервером (/chat/ai/*, см. server/ai_master.py);
// платформа даёт мост (status/start/turn/shell) и доступ к программе (состояние мастера, оглавление инструкции,
// подсветка, нажатия, открытие модели, совет по приложениям, фото блоков). Каждое действие с магнитолой — только
// после «Да» техника; читающие команды выполняются сами, но программа проверяет их ещё раз (ai_policy.py).
//
// Файл ОДИНАКОВЫЙ на ПК (app/web/frontend/js/ai_master.js) и Android (android/app/src/main/assets/js/ai_master.js) —
// tests/test_shared_frontend_copies.py. Без ?. ?? ||= и т.п.: на телефонах бывают старые WebView.
(function () {
  "use strict";

  var PHOTO = /\[\[фото:(\d{1,2})\/(\d{1,3}(?:\.\d{1,3}){0,3})\]\]/g;
  var CONTROL_NAMES = { next: "Далее", back: "Назад", start_install: "Начать установку", connect: "Подключить",
                        disconnect: "Отключить", stop: "Остановить" };
  var RETRY_MS = [3000, 6000, 12000, 20000, 30000, 60000];

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function button(label, cls, onClick) {
    var node = el("button", "ai-btn" + (cls ? " " + cls : ""), label);
    node.type = "button";
    node.addEventListener("click", onClick);
    return node;
  }

  function newId() {
    return Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10);
  }

  function controlName(control) {
    if (CONTROL_NAMES[control]) return CONTROL_NAMES[control];
    var m = /^action:(\d+)$/.exec(control || "");
    return m ? "Доп. действие №" + (Number(m[1]) + 1) : control;
  }

  // Мини-разметка ответа модели: **жирный**, `код`, строки «- » — список, остальное — абзацы. HTML не пропускаем.
  function inline(parent, text) {
    var re = /(\*\*[^*\n]+\*\*|`[^`\n]+`)/g;
    var last = 0;
    var m;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) parent.appendChild(document.createTextNode(text.slice(last, m.index)));
      var token = m[0];
      if (token.charAt(0) === "*") parent.appendChild(el("b", "", token.slice(2, -2)));
      else parent.appendChild(el("code", "", token.slice(1, -1)));
      last = m.index + token.length;
    }
    if (last < text.length) parent.appendChild(document.createTextNode(text.slice(last)));
  }

  function renderText(container, text, photo) {
    var lines = String(text || "").split("\n");
    var list = null;
    var para = null;
    lines.forEach(function (raw) {
      var line = raw.replace(/\s+$/, "");
      PHOTO.lastIndex = 0;
      var photos = [];
      var m;
      while ((m = PHOTO.exec(line)) !== null) photos.push([Number(m[1]), m[2]]);
      line = line.replace(PHOTO, "").replace(/\s+$/, "");
      var bullet = /^\s*[-•*]\s+(.*)$/.exec(line);
      if (bullet) {
        if (!list) { list = el("ul"); container.appendChild(list); }
        var li = el("li");
        inline(li, bullet[1]);
        list.appendChild(li);
        para = null;
      } else if (line.trim()) {
        list = null;
        if (!para) { para = el("p"); container.appendChild(para); } else para.appendChild(el("br"));
        inline(para, line);
      } else {
        list = null;
        para = null;
      }
      photos.forEach(function (p) { container.appendChild(photo(p[0], p[1])); });
    });
  }

  function create(options) {
    var bridge = options.bridge;
    var program = options.program;
    var root = options.container;
    var s = { session: null, modelKey: null, status: null, usage: null, sending: false, outbox: [], cards: [],
              paused: false, retry: 0, retryTimer: null, started: false, resumeNeeded: false, openingModel: false };

    root.classList.add("ai-master");
    root.innerHTML = "";
    var head = el("div", "ai-head");
    head.appendChild(el("div", "ai-logo", "✦"));
    var titles = el("div", "ai-titles");
    titles.appendChild(el("div", "ai-title", "Установка с ИИ"));
    var sub = el("div", "ai-sub", "");
    titles.appendChild(sub);
    head.appendChild(titles);
    var usage = el("div", "ai-limit", "");
    usage.hidden = true;
    head.appendChild(usage);
    var pause = button("❚❚", "ai-small", function () { setPaused(!s.paused); });
    pause.title = "Пауза: ИИ не будет сам продолжать и отвечать на события программы";
    head.appendChild(pause);
    if (options.onClose) head.appendChild(button("×", "ai-small ai-close", options.onClose));
    var body = el("div", "ai-body");
    var notice = el("div", "ai-notice");
    notice.hidden = true;
    var view = el("div", "ai-view");
    var inputWrap = el("div", "ai-input");
    var box = el("div", "ai-input-box");
    var input = el("textarea");
    input.rows = 1;
    input.placeholder = "Спросите ИИ или опишите, что на экране…";
    input.maxLength = 2000;
    var send = button("↑", "ai-send", function () { submitText(); });
    send.title = "Отправить";
    box.appendChild(input);
    box.appendChild(send);
    inputWrap.appendChild(box);
    var foot = el("div", "ai-foot");
    foot.appendChild(el("span", "", "Видит инструкцию модели и лог"));
    foot.appendChild(el("span", "", "Действия — после «Да»"));
    inputWrap.appendChild(foot);
    body.appendChild(view);
    root.appendChild(head);
    root.appendChild(notice);
    root.appendChild(body);
    root.appendChild(inputWrap);
    var typing = el("div", "ai-typing", "ИИ думает…");

    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitText(); }
    });
    // Поле растёт с текстом (до 120px, дальше — прокрутка).
    function fitInput() {
      input.style.height = "auto";
      var height = Math.min(input.scrollHeight, 120);
      input.style.height = height + "px";
      input.style.overflowY = input.scrollHeight > 120 ? "auto" : "hidden";
    }
    input.addEventListener("input", fitInput);

    function scroll() { body.scrollTop = body.scrollHeight; }
    function add(node) {
      if (typing.parentNode) view.insertBefore(node, typing); else view.appendChild(node);
      scroll();
      return node;
    }
    function event(icon, text) {
      var line = el("div", "ai-event");
      line.appendChild(el("i", "", icon));
      var span = el("span");
      inline(span, text);
      line.appendChild(span);
      return add(line);
    }
    function setNotice(text, kind) {
      notice.textContent = text || "";
      notice.className = "ai-notice" + (kind ? " " + kind : "");
      notice.hidden = !text;
    }
    // Под заголовком — какая модель и этап открыты (видно, о чём ИИ сейчас говорит).
    function updateSub() {
      var title = program.title();
      var stage = program.state().stage || {};
      sub.textContent = title ? title + (stage.index ? " · этап " + stage.index + (stage.total ? " из " + stage.total : "") : "")
        : "Модель не выбрана";
    }
    function setUsage(u) {
      if (!u || typeof u.limit !== "number") { usage.hidden = true; return; }
      s.usage = u;
      usage.textContent = "Сегодня " + u.used + " из " + u.limit;
      usage.hidden = false;
    }
    function setPaused(value) {
      s.paused = value;
      pause.textContent = value ? "▶" : "❚❚";
      pause.title = value ? "Продолжить: ИИ снова ведёт установку" : "Пауза: ИИ не будет сам продолжать и отвечать на события программы";
      pause.classList.toggle("on", value);
      setNotice(value ? "ИИ на паузе: сам не продолжает и не отвечает на события программы. Можно писать ему." : "", "");
      if (!value && s.outbox.length) sendTurn([]);
    }
    function photo(stage, block) {
      var url = program.photo(stage, block);
      var fig = el("figure", "ai-photo");
      if (url) {
        var img = el("img");
        img.src = url;
        img.alt = "Фото из инструкции, этап " + stage + ", блок " + block;
        fig.appendChild(img);
      }
      var cap = el("figcaption");
      cap.appendChild(el("span", "", "Инструкция, этап " + stage + " · " + block));
      var link = el("a", "", "Показать в инструкции");
      link.href = "#";
      link.addEventListener("click", function (e) {  // телефон: сначала открыть саму инструкцию (program.reveal)
        e.preventDefault();
        (program.reveal || program.focus)(stage, block);
      });
      cap.appendChild(link);
      fig.appendChild(cap);
      return fig;
    }
    function aiMessage(msg) {
      var node = el("div", "ai-msg ai");
      renderText(node, msg.text, photo);
      if (msg.text) add(node);
      if (msg.chips && msg.chips.length) {
        var chips = el("div", "ai-chips");
        msg.chips.forEach(function (chip) {
          chips.appendChild(button(chip, "ai-chip", function () { chips.remove(); userText(chip); }));
        });
        add(chips);
      }
    }
    function myMessage(text) {
      var node = el("div", "ai-msg me");
      renderText(node, text, function () { return document.createTextNode(""); });
      add(node);
    }

    // ---------------------------------------------------------------------------------------- виды без чата --
    function showView(kind, data) {
      view.innerHTML = "";
      inputWrap.hidden = kind !== "chat";
      pause.hidden = kind !== "chat";
      if (kind === "chat") return;
      var lock = el("div", "ai-lock");
      lock.appendChild(el("div", "ai-lock-icon", kind === "subscribe" ? "🔒" : "✦"));
      lock.appendChild(el("h3", "", "Установка с ИИ"));
      var texts = {
        subscribe: ["ИИ ведёт установку по шагам: показывает фото из инструкции, с вашего согласия нажимает кнопки " +
                    "программы и разбирается с ошибками по ходу.", "Для подписчиков Boosty — " +
                    ((data && data.limit) || 10) + " установок с ИИ в день."],
        login: ["Войдите в аккаунт программы — установка с ИИ доступна подписчикам Boosty."],
        limit: ["На сегодня установки с ИИ закончились (" + ((data && data.used) || 0) + " из " +
                ((data && data.limit) || 0) + "). Уже начатые установки продолжаются, новые — завтра."],
        budget: ["ИИ сейчас перегружен — попробуйте позже."],
        unofficial: ["Установка с ИИ работает только в официальной сборке программы с сайта magicsqd.ru."],
        unavailable: ["ИИ временно недоступен — попробуйте позже."],
        disabled: ["Установка с ИИ пока недоступна."],
      }[kind] || ["Установка с ИИ пока недоступна."];
      texts.forEach(function (t) { lock.appendChild(el("p", "", t)); });
      if (kind === "subscribe" && options.openUrl) {
        lock.appendChild(button("Подписаться", "primary", function () { options.openUrl(options.subscribeUrl); }));
      }
      view.appendChild(lock);
    }

    function welcome() {
      showView("chat");
      var key = program.modelKey();
      var text = key
        ? "Проведу установку на **" + program.title() + "** по шагам: доступ к ADB, подключение, выбор приложений, " +
          "установка. Покажу нужное место в инструкции, с вашего согласия нажму кнопки программы и разберусь с " +
          "ошибками.\nСпишется одна установка с ИИ на сегодня; продолжение этой модели в течение 12 часов — бесплатно."
        : "Помогу найти вашу машину в каталоге и пройти установку. Напишите марку, модель и год — или что написано " +
          "в меню магнитолы.";
      aiMessage({ text: text, chips: key ? ["Начать установку", "Что мне понадобится?"] : [] });
    }

    // ------------------------------------------------------------------------------------------------ протокол --
    async function refresh() {
      var data;
      try {
        data = await bridge.status();
      } catch (e) {
        setNotice("Нет связи с сервером — установка с ИИ откроется, когда появится интернет.", "warn");
        return null;
      }
      s.status = data.status;
      if (data.status === "ok" || data.status === "limit") setUsage(data); else usage.hidden = true;
      updateSub();
      if (data.status === "ok" || data.status === "limit") {
        if (data.status === "limit" && !s.session) showView("limit", data);
        else if (!s.started) { s.started = true; welcome(); }
      } else {
        showView(data.status === "disabled" ? "disabled" : data.status, data);
      }
      if (options.onStatus) options.onStatus(data.status);
      return data.status;
    }

    async function ensureSession() {
      var key = program.modelKey();
      if (s.session && s.modelKey === key && !s.resumeNeeded) return true;
      var data = await bridge.start({ model_key: key, outline: program.outline() });
      if (data.status === "no_model") {
        aiMessage({ text: "Эта модель недоступна для установки с ИИ — откройте другую или напишите, что за машина." });
        return false;
      }
      if (data.status !== "ok") {
        showView(data.status, data);
        return false;
      }
      s.session = data.session;
      s.modelKey = key;
      s.resumeNeeded = false;
      setUsage(data.usage);
      if (data.resumed) event("↺", "Продолжаем начатую установку — ИИ помнит переписку.");
      return true;
    }

    function scheduleRetry() {
      if (s.retryTimer) return;
      var delay = RETRY_MS[Math.min(s.retry, RETRY_MS.length - 1)];
      s.retry += 1;
      s.retryTimer = setTimeout(function () { s.retryTimer = null; sendTurn([]); }, delay);
    }

    async function sendTurn(extra) {
      if (extra && extra.length) s.outbox = s.outbox.concat(extra);
      if (s.sending) return;
      s.sending = true;
      var inputs = s.outbox.splice(0);
      if (!typing.parentNode) { view.appendChild(typing); scroll(); }
      var response = null;
      try {
        if (!(await ensureSession())) { s.sending = false; typing.remove(); return; }
        response = await bridge.turn({ session: s.session, req: newId(), input: inputs, state: program.state() });
      } catch (e) {
        s.outbox = inputs.concat(s.outbox);  // ничего не потеряли: уйдёт, когда связь вернётся
        s.sending = false;
        typing.remove();
        setNotice("Нет интернета — ИИ ответит, когда связь появится. Можно продолжать по инструкции.", "warn");
        scheduleRetry();
        return;
      }
      s.sending = false;
      typing.remove();
      s.retry = 0;
      if (!s.paused) setNotice("", "");
      var status = response.status;
      if (status === "busy") {  // предыдущий ход ещё идёт на сервере
        s.outbox = inputs.concat(s.outbox);
        setTimeout(function () { sendTurn([]); }, 1500);
        return;
      }
      if (status === "no_session") { s.session = null; s.outbox = inputs.concat(s.outbox); sendTurn([]); return; }
      if (status === "error") {
        // ввод уже сохранён на сервере — повтор без него
        var retryBox = el("div", "ai-event warn");
        retryBox.appendChild(el("span", "", response.error || "ИИ сейчас недоступен."));
        retryBox.appendChild(button("Повторить", "ai-small", function () { retryBox.remove(); sendTurn([]); }));
        add(retryBox);
        return;
      }
      if (status === "session_limit") {
        event("!", "Эта установка с ИИ слишком затянулась. Начните новую — или продолжайте по инструкции.");
        s.session = null;
        return;
      }
      if (status !== "ok") { showView(status, response); return; }
      setUsage(response.usage);
      updateSub();
      (response.messages || []).forEach(aiMessage);
      await runActions(response.actions || []);
      if ((needsTurn() || response["continue"]) && !s.paused) sendTurn([]);
    }

    // Ход без слова техника — только если есть чем ответить ИИ: результат команды, ответ карточки (кроме «Нажму
    // сам» — он ждёт события) или важное событие. Обычные события (сменился этап) уйдут со следующим ходом.
    function needsTurn() {
      return s.outbox.some(function (item) {
        return item.type === "result" || (item.type === "card" && item.answer !== "self")
          || (item.type === "event" && IMPORTANT[item.name]);
      });
    }

    function userText(text) {
      text = String(text || "").trim();
      if (!text) return;
      myMessage(text);
      var last = s.cards.length ? s.cards[s.cards.length - 1] : null;
      if (last && !last.done && /^(да|ага|давай|ок|ok|выполняй|нажимай|жми)[.!]*$/i.test(text)) {
        last.yes();  // «да» текстом — ответ на последнюю карточку
        return;
      }
      sendTurn([{ type: "text", text: text }]);
    }

    function submitText() {
      var text = input.value;
      input.value = "";
      input.style.height = "";
      userText(text);
    }

    // -------------------------------------------------------------------------------------------- действия ИИ --
    async function runActions(actions) {
      for (var i = 0; i < actions.length; i++) {
        try {
          await runAction(actions[i]);
        } catch (e) {  // сбой одного действия не обрывает ход: ИИ узнает о нём из результата или состояния
          if (actions[i].mode === "auto" && actions[i].tool === "shell") {
            s.outbox.push({ type: "result", id: actions[i].id, ok: false, output: "Не выполнилось: " + (e && e.message ? e.message : e) });
          }
        }
      }
    }

    async function runAction(a) {
      var args = a.args || {};
      if (a.tool === "focus_block") {
        var note = program.focus(args.stage, args.block);  // телефон: «отметил во вкладке «Инструкция»»
        if (typeof note === "string" && note) event("✦", note);
      } else if (a.tool === "highlight") {
        var hint = program.highlight(args.target);
        if (typeof hint === "string" && hint) event("✦", hint);
      } else if (a.tool === "select_apps") {
        var advice = program.selectApps(args.picks || [], args.avoid || []);
        event("✦", "Совет по приложениям — во вкладке «✦ Совет ИИ».");
        // сервер уже ответил «показано» — о путях, которых нет в программе, ИИ узнает со следующим ходом
        if (advice && (advice.ok === false || (advice.missing && advice.missing.length))) {
          s.outbox.push({ type: "event", name: "select_apps_problem", detail: advice.output });
        }
      } else if (a.tool === "shell" && a.mode === "auto") {
        var line = event("⚙", "Проверяю на магнитоле: `" + args.cmd + "`");
        var result = await runShell(args.cmd, "auto");
        line.classList.add(result.ok ? "ok" : "fail");
        s.outbox.push({ type: "result", id: a.id, ok: result.ok, output: result.output });
      } else if (a.mode === "confirm") {
        card(a);
      }
    }

    async function runShell(cmd, mode) {
      try {
        var r = await bridge.shell(cmd, mode);
        return { ok: !!(r && r.ok), output: String((r && r.output) || "") };
      } catch (e) {
        return { ok: false, output: "Не выполнилось: " + (e && e.message ? e.message : e) };
      }
    }

    function card(action) {
      var args = action.args || {};
      var stageAt = (program.state().stage || {}).index;
      var warn = action.tool === "shell";
      var node = el("div", "ai-card" + (warn ? " warn" : ""));
      var title = el("div", "ai-card-title");
      var buttons = el("div", "ai-actions");
      var entry = { id: action.id, done: false, stageAt: stageAt, node: node, yes: null };
      if (action.tool === "shell") {
        title.textContent = "Команда меняет магнитолу";
        node.appendChild(title);
        node.appendChild(el("pre", "", args.cmd));
        if (args.why) node.appendChild(el("div", "ai-note-why", args.why));
        if (args.undo) node.appendChild(el("div", "ai-note", "Как вернуть: " + args.undo));
      } else if (action.tool === "press") {
        // надпись настоящей кнопки, если программа её знает («Начать установку», название доп. действия)
        title.textContent = "Нажать «" + ((program.label && program.label(args.control)) || controlName(args.control)) + "»?";
        node.appendChild(title);
        if (args.why) node.appendChild(el("div", "ai-note-why", args.why));
      } else {
        title.textContent = "Открыть модель «" + String(args.model || "").split("/").join(" ") + "»?";
        node.appendChild(title);
        if (args.why) node.appendChild(el("div", "ai-note-why", args.why));
      }
      // quiet — ответ серверу не нужен: модель открыта по карточке, ИИ продолжит в установке новой модели.
      function finish(answer, result, quiet) {
        if (entry.done) return;
        entry.done = true;
        buttons.remove();
        var item = { type: "card", id: action.id, answer: answer };
        if (result) { item.ok = result.ok; item.output = result.output; }
        node.appendChild(el("div", "ai-card-done", {
          yes: result && !result.ok ? "Не получилось: " + result.output : "Сделано.",
          no: "Отказались.", self: "Техник сделает сам.", stale: "Устарело — программа уже в другом состоянии.",
        }[answer]));
        if (quiet) return;
        if (answer === "self") s.outbox.push(item);  // уйдёт со следующим событием программы
        else sendTurn([item]);
      }
      entry.yes = async function () {
        if (entry.done || entry.running) return;
        var nowStage = (program.state().stage || {}).index;
        if (action.tool === "press" && nowStage !== stageAt) { finish("stale"); return; }
        entry.running = true;  // этап сменится от самого нажатия — карточка при этом не «устарела»
        buttons.querySelectorAll("button").forEach(function (b) { b.disabled = true; });
        var result;
        if (action.tool === "shell") result = await runShell(args.cmd, "confirm");
        else if (action.tool === "press") result = await program.press(args.control);
        else {
          s.openingModel = true;  // событие model_opened придёт, пока модель открывается, — ИИ продолжит сам
          try { result = await program.openModel(args.model); } finally { s.openingModel = false; }
          if (result && result.ok) { finish("yes", result, true); return; }
        }
        finish("yes", result || { ok: true, output: "" });
      };
      if (action.tool === "shell") {
        buttons.appendChild(button("Выполнить", "primary", entry.yes));
        buttons.appendChild(button("Не надо", "", function () { finish("no"); }));
      } else if (action.tool === "press") {
        buttons.appendChild(button("Да, нажми", "primary", entry.yes));
        buttons.appendChild(button("Нажму сам", "", function () { finish("self"); }));
        program.highlight(args.control);
      } else {
        buttons.appendChild(button("Да, открыть", "primary", entry.yes));
        buttons.appendChild(button("Нет", "", function () { finish("no"); }));
      }
      node.appendChild(buttons);
      entry.stale = function () { if (!entry.done && !entry.running && action.tool === "press") finish("stale"); };
      entry.drop = function () {  // открыли другую модель: прежняя установка с ИИ закончилась, ответ не нужен
        if (entry.done) return;
        entry.done = true;
        buttons.remove();
        node.appendChild(el("div", "ai-card-done", "Устарело — открыта другая модель."));
      };
      s.cards.push(entry);
      add(node);
    }

    // ------------------------------------------------------------------------------------- события программы --
    var IMPORTANT = { adb_connected: true, connect_failed: true, install_finished: true, install_failed: true,
                      stage_failed: true, user_error: true, model_opened: true };
    function programEvent(name, detail) {
      updateSub();
      if (name === "stage_changed") s.cards.forEach(function (c) { if (c.stale) c.stale(); });
      if (name === "model_opened" && !modelOpened()) return;
      if (!s.session) return;
      var waitingSelf = s.outbox.some(function (item) { return item.type === "card"; });
      s.outbox.push({ type: "event", name: name, detail: detail ? String(detail).slice(0, 500) : "" });
      // важные события и ответ «Нажму сам» (ждёт следующего события) — ИИ отвечает сразу
      if ((IMPORTANT[name] || waitingSelf) && !s.paused && !s.sending) sendTurn([]);
    }

    // Открыли модель. По карточке ИИ («Да, открыть») или ту же самую — ИИ продолжает (true: событие уйдёт ему).
    // Другую техник открыл сам — новую установку с ИИ молча не начинаем (это одна из дневных): ждём его слова.
    function modelOpened() {
      var key = program.modelKey();
      if (!s.session) {
        if (s.started) welcome();  // приветствие — уже про открытую модель
        return false;
      }
      if (s.openingModel || key === s.modelKey) {
        s.resumeNeeded = true;  // следующий ход — start: сервер продолжит ту же или начнёт установку новой модели
        return true;
      }
      s.cards.forEach(function (c) { if (c.drop) c.drop(); });
      s.outbox = [];
      s.session = null;
      s.modelKey = null;
      event("↺", "Открыта модель **" + program.title() + "**.");
      aiMessage({ text: "Провести установку на ней с ИИ? Спишется одна установка с ИИ на сегодня.",
                  chips: ["Начать установку с ИИ"] });
      return false;
    }

    return {
      open: function () { return refresh().then(function (status) { input.focus(); return status; }); },
      refresh: refresh,
      event: programEvent,
      send: userText,
      element: root,
    };
  }

  window.AiMaster = { create: create, renderText: renderText };
})();
