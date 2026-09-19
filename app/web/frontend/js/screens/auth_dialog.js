// Аккаунт техника — вход/регистрация email+пароль (подтверждение почты по
// ссылке при регистрации), см. app/web/api/auth_api.py, server/backend.py:
// протокол /auth/*. Живёт ВНУТРИ того же попапа/кнопки, что раньше были
// только у "Админ" (см. main_picker.js: catalog-admin-toggle/-popover) —
// одна кнопка на всех, просто содержимое попапа зависит от состояния:
// не вошёл -> форма входа/регистрации; вошёл (обычный аккаунт) -> "Вы
// вошли как ..." + "Выйти"; вошёл с правами администратора -> те же
// admin-actions/pending, что и раньше (см. setAdminMode в main_picker.js),
// это НЕ трогаем — attach() тут только про блоки входа/аккаунта.
window.authDialog = (() => {
  let toggleEl, popoverEl, titleEl, subtitleEl, guestEl, loggedinEl;
  let emailInput, passwordInput, statusEl, submitBtn, switchBtn, forgotBtn, logoutBtn, loggedinEmailEl;
  let changePasswordToggleBtn, changePasswordSection, currentPasswordInput, newPasswordInput,
    newPasswordRepeatInput, changePasswordStatusEl, changePasswordSubmitBtn;
  let mode = "login"; // "login" | "register"
  let currentEmail = null;
  let boosty = {};
  let tg = {};

  function attach(refs) {
    toggleEl = refs.toggleEl;
    popoverEl = refs.popoverEl;
    titleEl = refs.titleEl;
    guestEl = refs.guestEl;
    loggedinEl = refs.loggedinEl;
    subtitleEl = popoverEl.querySelector("#catalog-account-subtitle");

    emailInput = guestEl.querySelector("#catalog-account-email-input");
    passwordInput = guestEl.querySelector("#catalog-account-password-input");
    statusEl = guestEl.querySelector("#catalog-account-status");
    submitBtn = guestEl.querySelector("#catalog-account-submit");
    switchBtn = guestEl.querySelector("#catalog-account-switch");
    forgotBtn = guestEl.querySelector("#catalog-account-forgot");
    loggedinEmailEl = loggedinEl.querySelector("#catalog-account-loggedin-email");
    logoutBtn = loggedinEl.querySelector("#catalog-account-logout");
    changePasswordToggleBtn = loggedinEl.querySelector("#catalog-account-change-password-toggle");
    changePasswordSection = loggedinEl.querySelector("#catalog-account-change-password");
    currentPasswordInput = loggedinEl.querySelector("#catalog-account-current-password");
    newPasswordInput = loggedinEl.querySelector("#catalog-account-new-password");
    newPasswordRepeatInput = loggedinEl.querySelector("#catalog-account-new-password-repeat");
    changePasswordStatusEl = loggedinEl.querySelector("#catalog-account-change-password-status");
    changePasswordSubmitBtn = loggedinEl.querySelector("#catalog-account-change-password-submit");

    boosty.stateEl = loggedinEl.querySelector("#catalog-account-boosty-state");
    boosty.tgBtn = loggedinEl.querySelector("#catalog-account-boosty-tg");
    boosty.openBtn = loggedinEl.querySelector("#catalog-account-boosty-open");
    boosty.statusEl = loggedinEl.querySelector("#catalog-account-boosty-status");
    boosty.actionsEl = loggedinEl.querySelector("#catalog-account-boosty-actions");
    boosty.refreshBtn = loggedinEl.querySelector("#catalog-account-boosty-refresh");
    boosty.unlinkBtn = loggedinEl.querySelector("#catalog-account-boosty-unlink");
    tg.loginBtn = guestEl.querySelector("#catalog-account-tg-login");
    tg.statusEl = guestEl.querySelector("#catalog-account-tg-status");
    tg.actionsEl = guestEl.querySelector("#catalog-account-tg-actions");
    tg.openBtn = guestEl.querySelector("#catalog-account-tg-open");
    tg.cancelBtn = guestEl.querySelector("#catalog-account-tg-cancel");

    // safely() — тонкая обёртка вокруг обработчиков клика: не даёт нажать
    // повторно, пока предыдущий запрос ещё не завершился (кнопка всё равно
    // задизейблена самим обработчиком, но Enter в поле вызывает функцию
    // напрямую, минуя disabled-кнопку), и подстраховывает от необработанного
    // отказа промиса (обрыв соединения и т.п.) — иначе кнопка осталась бы
    // задизейбленной навсегда, а пользователь без объяснения в статусе.
    const safely = (fn, button, status) => async () => {
      if (button.disabled) return;
      try { await fn(); }
      catch (_) {
        if (status) { status.textContent = "Нет ответа от сервера. Проверьте подключение и повторите попытку."; status.dataset.state = "error"; }
      }
      finally { button.disabled = false; if (button === submitBtn) switchBtn.disabled = false; }
    };
    const submit = safely(onSubmit, submitBtn, statusEl);
    const forgot = safely(onForgotPassword, forgotBtn, statusEl);
    const changePassword = safely(onChangePassword, changePasswordSubmitBtn, changePasswordStatusEl);
    submitBtn.addEventListener("click", submit);
    switchBtn.addEventListener("click", () => setMode(mode === "login" ? "register" : "login"));
    forgotBtn.addEventListener("click", forgot);
    // #catalog-account-global-status пока не всегда есть в разметке (см.
    // main_picker.js — ждёт своей миграции отдельно от этого файла), поэтому
    // querySelector тут может вернуть null — safely() и onLogout() ниже это
    // учитывают и просто не показывают статус, если элемента ещё нет.
    logoutBtn.addEventListener("click", safely(onLogout, logoutBtn, popoverEl.querySelector("#catalog-account-global-status")));
    changePasswordToggleBtn.addEventListener("click", () => {
      changePasswordSection.hidden = !changePasswordSection.hidden;
      changePasswordToggleBtn.setAttribute("aria-expanded", String(!changePasswordSection.hidden));
      if (!changePasswordSection.hidden) currentPasswordInput.focus({ preventScroll: true });
    });
    changePasswordSubmitBtn.addEventListener("click", changePassword);
    const onEnter = (event) => { if (event.key === "Enter") { event.preventDefault(); submit(); } };
    emailInput.addEventListener("keydown", onEnter);
    passwordInput.addEventListener("keydown", onEnter);
    const onChangePasswordEnter = (event) => { if (event.key === "Enter") { event.preventDefault(); changePassword(); } };
    currentPasswordInput.addEventListener("keydown", onChangePasswordEnter);
    newPasswordInput.addEventListener("keydown", onChangePasswordEnter);
    newPasswordRepeatInput.addEventListener("keydown", onChangePasswordEnter);
    tg.loginBtn.addEventListener("click", safely(() => startTelegram("login"), tg.loginBtn, tg.statusEl));
    tg.openBtn.addEventListener("click", () => { if (tg.link) window.pywebview.api.open_external(tg.link); });
    tg.cancelBtn.addEventListener("click", cancelTelegram);
    boosty.tgBtn.addEventListener("click", safely(() => startTelegram("link"), boosty.tgBtn, boosty.statusEl));
    boosty.openBtn.addEventListener("click", () => window.pywebview.api.open_external(boosty.url || "https://boosty.to/magic_sqd"));
    boosty.refreshBtn.addEventListener("click", safely(onBoostyRefresh, boosty.refreshBtn, boosty.statusEl));
    boosty.unlinkBtn.addEventListener("click", safely(onBoostyUnlink, boosty.unlinkBtn, boosty.statusEl));
    // Каждый раз при открытии окна аккаунта — свежий статус подписки.
    toggleEl.addEventListener("click", () => { if (currentEmail) refreshBoosty(); });
    setMode("login");
  }

  // -- Telegram: вход/регистрация без почты и привязка к аккаунту; подписка Boosty определяется по
  // членству привязанного Telegram в закрытой группе подписчиков (см. server/backend.py: /auth/tg/...) --
  const mb = (bytes) => `${Math.round(bytes / 1048576)} МБ`;
  let tgTimer = null;

  function stopTelegramPolling() { if (tgTimer) { clearInterval(tgTimer); tgTimer = null; } }

  function cancelTelegram() {
    stopTelegramPolling();
    tg.link = null; tg.actionsEl.hidden = true; tg.statusEl.textContent = "";
    window.pywebview.api.auth_tg_cancel();
  }

  // purpose: "login" (гость) или "link" (уже вошёл по почте — привязать Telegram)
  async function startTelegram(purpose) {
    const statusTarget = purpose === "login" ? tg.statusEl : boosty.statusEl;
    statusTarget.dataset.state = "error";
    statusTarget.textContent = "Готовлю ссылку...";
    const res = await window.pywebview.api.auth_tg_start(purpose);
    if (!res.ok) { statusTarget.textContent = res.error; return; }
    tg.link = res.link; tg.purpose = purpose;
    statusTarget.dataset.state = "success";
    statusTarget.textContent = "Открылся Telegram: нажмите Start у бота, затем «Подтвердить». Если он не открылся — кнопка «Открыть Telegram». Жду подтверждения…";
    if (purpose === "login") tg.actionsEl.hidden = false;
    stopTelegramPolling();
    const startedAt = Date.now();
    tgTimer = setInterval(async () => {
      if (Date.now() - startedAt > (res.expires_in || 600) * 1000) { cancelTelegram(); statusTarget.dataset.state = "error"; statusTarget.textContent = "Время вышло. Нажмите кнопку ещё раз."; return; }
      let r;
      try { r = await window.pywebview.api.auth_tg_poll(); } catch (_) { return; }
      if (r.status === "pending" || r.status === "awaiting") return;
      stopTelegramPolling(); tg.actionsEl.hidden = true;
      if (r.status !== "done") { statusTarget.dataset.state = "error"; statusTarget.textContent = r.error || "Не удалось подтвердить вход."; return; }
      if (purpose === "link") { statusTarget.dataset.state = "success"; statusTarget.textContent = "Telegram привязан."; renderBoosty(r); return; }
      popoverEl.hidden = true;
      toggleEl.setAttribute("aria-expanded", "false");
      setLoggedIn(r.email);
      if (r.is_admin) { window.applyAdminMode(true); window.notice("Вход выполнен — функции администратора включены."); }
      else window.notice(r.created ? "Аккаунт создан через Telegram — вы вошли." : "Вы вошли через Telegram.");
    }, 2000);
  }

  function renderBoosty(status) {
    const el = boosty.stateEl;
    el.dataset.state = "";
    boosty.url = status && status.boosty_url;
    if (!status || !status.ok) {
      el.textContent = "Boosty: не удалось получить статус.";
      boosty.tgBtn.hidden = true; boosty.actionsEl.hidden = true;
      return;
    }
    const t = status.telegram || {};
    const chat = status.chat_per_hour === null ? "без лимита" : `${status.chat_per_hour} запросов в час`;
    const space = status.storage_limit_bytes === null || status.storage_limit_bytes === undefined
      ? `${mb(status.storage_used_bytes || 0)}, без лимита`
      : `${mb(status.storage_used_bytes || 0)} из ${mb(status.storage_limit_bytes)}`;
    let text;
    if (!t.configured && !status.configured) text = "Boosty: интеграция пока не включена.";
    else if (status.subscriber) text = `Подписчик Boosty — лимиты сняты. Чат: ${chat}. Место: ${space}.`;
    else if (t.linked && t.group_configured) text = `Telegram привязан${t.username ? ` (@${t.username})` : ""}, но в закрытой группе подписчиков Boosty вас нет. Оформите подписку на Boosty, привяжите Telegram в Boosty и вступите в группу. Чат: ${chat}. Место: ${space}.`;
    else if (t.linked) text = `Telegram привязан${t.username ? ` (@${t.username})` : ""}. Чат: ${chat}. Место: ${space}.`;
    else text = `Подписчикам Boosty (любой платный уровень) лимиты снимаются. Привяжите Telegram — по нему мы узнаём о подписке. Сейчас чат: ${chat}, место: ${space}.`;
    el.textContent = text;
    boosty.tgBtn.hidden = !t.configured || !!t.linked;
    boosty.actionsEl.hidden = !t.linked;
    boosty.unlinkBtn.hidden = !t.can_unlink;
  }

  async function refreshBoosty() {
    try { renderBoosty(await window.pywebview.api.auth_boosty_status()); }
    catch (_) { renderBoosty(null); }
  }

  async function onBoostyRefresh() {
    boosty.statusEl.dataset.state = "error";
    boosty.statusEl.textContent = "Проверяю подписку...";
    const res = await window.pywebview.api.auth_boosty_refresh();
    if (!res.ok) { boosty.statusEl.textContent = res.error; return; }
    boosty.statusEl.dataset.state = "success";
    boosty.statusEl.textContent = "Статус обновлён.";
    renderBoosty(res);
  }

  async function onBoostyUnlink() {
    if (!(await window.confirmDialog("Отвязать Telegram? Лимиты вернутся к обычным, пока вы не привяжете его снова."))) return;
    const res = await window.pywebview.api.auth_tg_unlink();
    boosty.statusEl.dataset.state = res.ok ? "success" : "error";
    boosty.statusEl.textContent = res.ok ? "Telegram отвязан." : res.error;
    if (res.ok) renderBoosty(res);
  }

  function setMode(newMode) {
    mode = newMode;
    if (mode === "login") {
      titleEl.textContent = "Вход";
      submitBtn.textContent = "Войти";
      switchBtn.textContent = "Нет аккаунта? Зарегистрироваться";
    } else {
      titleEl.textContent = "Регистрация";
      submitBtn.textContent = "Зарегистрироваться";
      switchBtn.textContent = "Уже есть аккаунт? Войти";
    }
    passwordInput.autocomplete = mode === "login" ? "current-password" : "new-password";
    subtitleEl.textContent = mode === "login" ? "Аккаунт техника" : "Подтвердите email после регистрации";
    statusEl.textContent = "";
    // Появляется только после неудачной попытки входа (см. onSubmit) — не
    // нужно предлагать восстановление, пока человек ещё даже не пробовал
    // войти обычным способом.
    forgotBtn.hidden = true;
  }

  // Вызывается при старте (см. app.js: app_get_info().auth_email) и сразу
  // после успешного входа/выхода — переключает попап между формой входа
  // и "Вы вошли как ...", и подпись самой кнопки в шапке каталога.
  function setLoggedIn(email) {
    currentEmail = email;
    const globalStatus = popoverEl.querySelector("#catalog-account-global-status");
    if (globalStatus) globalStatus.textContent = "";
    guestEl.hidden = Boolean(email);
    loggedinEl.hidden = !email;
    toggleEl.textContent = email || "Вход";
    toggleEl.title = email ? `Аккаунт: ${email}` : "Вход в аккаунт";
    if (email) {
      titleEl.textContent = "Аккаунт";
      subtitleEl.hidden = true;
      loggedinEmailEl.textContent = email;
      boosty.statusEl.textContent = "";
      refreshBoosty();
    } else {
      subtitleEl.hidden = false;
      setMode("login");
      emailInput.value = "";
      passwordInput.value = "";
    }
    // Форма смены пароля — всегда сворачивается и очищается при любом
    // переключении состояния (вход/выход), чтобы не оставлять введённые
    // пароли висеть в скрытых полях между сессиями.
    changePasswordSection.hidden = true;
    changePasswordToggleBtn.setAttribute("aria-expanded", "false");
    currentPasswordInput.value = "";
    newPasswordInput.value = "";
    newPasswordRepeatInput.value = "";
    changePasswordStatusEl.textContent = "";
  }

  async function onSubmit() {
    statusEl.dataset.state = "error";
    const email = emailInput.value.trim();
    const password = passwordInput.value;
    if (!email || !password) {
      statusEl.textContent = "Введите email и пароль.";
      return;
    }
    submitBtn.disabled = true;
    switchBtn.disabled = true;
    statusEl.textContent = mode === "login" ? "Вхожу..." : "Регистрирую...";
    if (mode === "register") {
      const result = await window.pywebview.api.auth_register(email, password);
      submitBtn.disabled = false;
      switchBtn.disabled = false;
      if (!result.ok) {
        statusEl.textContent = result.error;
        return;
      }
      setMode("login");
      statusEl.dataset.state = "success";
      statusEl.textContent = "Письмо с подтверждением отправлено — перейдите по ссылке из него, потом войдите здесь.";
      emailInput.value = email;
      return;
    }
    const result = await window.pywebview.api.auth_login(email, password);
    submitBtn.disabled = false;
    switchBtn.disabled = false;
    if (!result.ok) {
      statusEl.textContent = result.error;
      forgotBtn.hidden = false;
      return;
    }
    popoverEl.hidden = true;
    toggleEl.setAttribute("aria-expanded", "false");
    setLoggedIn(result.email);
    if (result.is_admin) {
      window.applyAdminMode(true);
      window.notice("Вход выполнен — функции администратора включены.");
    }
  }

  async function onForgotPassword() {
    statusEl.dataset.state = "error";
    const email = emailInput.value.trim();
    if (!email) {
      statusEl.textContent = "Введите email, на который зарегистрирован аккаунт.";
      return;
    }
    forgotBtn.disabled = true;
    statusEl.textContent = "Отправляю письмо...";
    const result = await window.pywebview.api.auth_forgot_password(email);
    forgotBtn.disabled = false;
    statusEl.dataset.state = result.ok ? "success" : "error";
    // Сервер намеренно отвечает одинаково независимо от того, есть такой
    // email в базе или нет (см. server/backend.py:_handle_auth_forgot_
    // password) — не подтверждаем/опровергаем существование аккаунта.
    statusEl.textContent = result.ok
      ? "Если такой аккаунт есть, письмо со ссылкой для сброса пароля отправлено."
      : result.error;
  }

  async function onChangePassword() {
    changePasswordStatusEl.dataset.state = "error";
    const current = currentPasswordInput.value;
    const next = newPasswordInput.value;
    const repeat = newPasswordRepeatInput.value;
    if (!current || !next || !repeat) {
      changePasswordStatusEl.textContent = "Заполните все поля.";
      return;
    }
    if (next !== repeat) {
      changePasswordStatusEl.textContent = "Новые пароли не совпадают.";
      return;
    }
    if (next.length < 8) {
      changePasswordStatusEl.textContent = "Новый пароль должен быть не короче 8 символов.";
      return;
    }
    changePasswordSubmitBtn.disabled = true;
    changePasswordStatusEl.textContent = "Сохраняю...";
    const result = await window.pywebview.api.auth_change_password(current, next);
    changePasswordSubmitBtn.disabled = false;
    if (!result.ok) {
      changePasswordStatusEl.textContent = result.error;
      return;
    }
    currentPasswordInput.value = "";
    newPasswordInput.value = "";
    newPasswordRepeatInput.value = "";
    changePasswordStatusEl.dataset.state = "success";
    changePasswordStatusEl.textContent = "Пароль изменён.";
  }

  async function onLogout() {
    if (!(await window.confirmDialog(`Выйти из аккаунта ${currentEmail}?`))) return;
    const result = await window.pywebview.api.auth_logout();
    if (!result.ok) {
      // См. комментарий в attach() — этот элемент появится в разметке
      // отдельной миграцией main_picker.js, пока может отсутствовать.
      const status = popoverEl.querySelector("#catalog-account-global-status");
      if (status) {
        status.textContent = result.error || "Не удалось выйти из аккаунта. Повторите попытку.";
        status.dataset.state = "error";
      }
      return;
    }
    setLoggedIn(null);
    window.applyAdminMode(false);
    popoverEl.hidden = true;
    toggleEl.setAttribute("aria-expanded", "false");
  }

  // Пока admin_mode включён, у "Выйти из режима администратора" (внутри
  // admin-actions, см. app.js) и общего "Выйти" здесь одна и та же цель
  // (полный выход из аккаунта, см. app.js: admin-logout-btn), показывать
  // обе кнопки одновременно бессмысленно — прячем эту, оставляем только
  // admin-кнопку.
  function setAdminVisible(enabled) {
    if (logoutBtn) logoutBtn.hidden = enabled;
    const role = popoverEl?.querySelector("#catalog-account-role");
    if (role) role.textContent = enabled ? "Администратор" : "Аккаунт техника";
  }

  return { attach, setLoggedIn, setAdminVisible };
})();
