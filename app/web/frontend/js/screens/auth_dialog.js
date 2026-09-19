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
    boosty.toggleBtn = loggedinEl.querySelector("#catalog-account-boosty-toggle");
    boosty.formEl = loggedinEl.querySelector("#catalog-account-boosty-form");
    boosty.emailInput = loggedinEl.querySelector("#catalog-account-boosty-email");
    boosty.sendBtn = loggedinEl.querySelector("#catalog-account-boosty-send");
    boosty.codeField = loggedinEl.querySelector("#catalog-account-boosty-code-field");
    boosty.codeInput = loggedinEl.querySelector("#catalog-account-boosty-code");
    boosty.confirmBtn = loggedinEl.querySelector("#catalog-account-boosty-confirm");
    boosty.statusEl = loggedinEl.querySelector("#catalog-account-boosty-status");
    boosty.actionsEl = loggedinEl.querySelector("#catalog-account-boosty-actions");
    boosty.refreshBtn = loggedinEl.querySelector("#catalog-account-boosty-refresh");
    boosty.unlinkBtn = loggedinEl.querySelector("#catalog-account-boosty-unlink");

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
    boosty.toggleBtn.addEventListener("click", () => {
      boosty.formEl.hidden = !boosty.formEl.hidden;
      boosty.toggleBtn.setAttribute("aria-expanded", String(!boosty.formEl.hidden));
      if (!boosty.formEl.hidden) {
        if (!boosty.emailInput.value) boosty.emailInput.value = currentEmail || "";
        boosty.emailInput.focus({ preventScroll: true });
      }
    });
    boosty.sendBtn.addEventListener("click", safely(onBoostySend, boosty.sendBtn, boosty.statusEl));
    boosty.confirmBtn.addEventListener("click", safely(onBoostyConfirm, boosty.confirmBtn, boosty.statusEl));
    boosty.refreshBtn.addEventListener("click", safely(onBoostyRefresh, boosty.refreshBtn, boosty.statusEl));
    boosty.unlinkBtn.addEventListener("click", safely(onBoostyUnlink, boosty.unlinkBtn, boosty.statusEl));
    boosty.codeInput.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); boosty.confirmBtn.click(); } });
    // Каждый раз при открытии окна аккаунта — свежий статус подписки.
    toggleEl.addEventListener("click", () => { if (currentEmail) refreshBoosty(); });
    setMode("login");
  }

  // -- Boosty: связать аккаунт по почте Boosty; подписчик любого платного уровня
  // снимает лимиты на место и ИИ-чат (см. server/backend.py: /auth/boosty/*) --
  const mb = (bytes) => `${Math.round(bytes / 1048576)} МБ`;

  function renderBoosty(status) {
    const el = boosty.stateEl;
    el.dataset.state = "";
    if (!status || !status.ok) {
      el.textContent = "Boosty: не удалось получить статус.";
      boosty.toggleBtn.hidden = false; boosty.actionsEl.hidden = true;
      return;
    }
    const chat = status.chat_per_hour === null ? "без лимита" : `${status.chat_per_hour} запросов в час`;
    const space = status.storage_limit_bytes === null || status.storage_limit_bytes === undefined
      ? `${mb(status.storage_used_bytes || 0)}, без лимита`
      : `${mb(status.storage_used_bytes || 0)} из ${mb(status.storage_limit_bytes)}`;
    let text;
    if (!status.configured) text = "Boosty: интеграция пока не включена.";
    else if (status.subscriber) text = `Подписчик Boosty${status.level ? ` (${status.level})` : ""} — лимиты сняты. Чат: ${chat}. Место: ${space}.`;
    else if (status.linked) text = `Boosty привязан (${status.email}), но платной подписки не найдено. Чат: ${chat}. Место: ${space}.`;
    else text = `Подписчикам Boosty (любой платный уровень) лимиты снимаются. Сейчас чат: ${chat}, место: ${space}. Нажмите «Связать с Boosty».`;
    el.textContent = text;
    boosty.toggleBtn.hidden = !status.configured || status.linked;
    boosty.actionsEl.hidden = !status.linked;
    if (status.linked) { boosty.formEl.hidden = true; boosty.toggleBtn.setAttribute("aria-expanded", "false"); }
  }

  async function refreshBoosty() {
    try { renderBoosty(await window.pywebview.api.auth_boosty_status()); }
    catch (_) { renderBoosty(null); }
  }

  async function onBoostySend() {
    boosty.statusEl.dataset.state = "error";
    const email = boosty.emailInput.value.trim();
    if (!email) { boosty.statusEl.textContent = "Введите почту, на которую зарегистрирован Boosty."; return; }
    boosty.sendBtn.disabled = true;
    boosty.statusEl.textContent = "Отправляю код...";
    const res = await window.pywebview.api.auth_boosty_start(email);
    if (!res.ok) { boosty.statusEl.textContent = res.error; return; }
    boosty.statusEl.dataset.state = "success";
    boosty.statusEl.textContent = `Код отправлен на ${email}. Введите его ниже (действует 15 минут).`;
    boosty.codeField.hidden = false; boosty.confirmBtn.hidden = false;
    boosty.codeInput.focus({ preventScroll: true });
  }

  async function onBoostyConfirm() {
    boosty.statusEl.dataset.state = "error";
    const code = boosty.codeInput.value.trim();
    if (!/^\d{6}$/.test(code)) { boosty.statusEl.textContent = "Введите 6 цифр из письма."; return; }
    boosty.confirmBtn.disabled = true;
    boosty.statusEl.textContent = "Проверяю...";
    const res = await window.pywebview.api.auth_boosty_confirm(code);
    if (!res.ok) { boosty.statusEl.textContent = res.error; return; }
    boosty.codeInput.value = ""; boosty.codeField.hidden = true; boosty.confirmBtn.hidden = true;
    boosty.statusEl.dataset.state = "success";
    boosty.statusEl.textContent = res.subscriber ? "Готово: подписка найдена, лимиты сняты." : "Аккаунт привязан, но платной подписки на эту почту пока не найдено.";
    renderBoosty(res);
  }

  async function onBoostyRefresh() {
    boosty.statusEl.dataset.state = "error";
    boosty.refreshBtn.disabled = true;
    boosty.statusEl.textContent = "Проверяю подписку...";
    const res = await window.pywebview.api.auth_boosty_refresh();
    if (!res.ok) { boosty.statusEl.textContent = res.error; return; }
    boosty.statusEl.dataset.state = "success";
    boosty.statusEl.textContent = "Статус обновлён.";
    renderBoosty(res);
  }

  async function onBoostyUnlink() {
    if (!(await window.confirmDialog("Отвязать Boosty? Лимиты вернутся к обычным, пока вы не свяжете аккаунт снова."))) return;
    const res = await window.pywebview.api.auth_boosty_unlink();
    boosty.statusEl.dataset.state = res.ok ? "success" : "error";
    boosty.statusEl.textContent = res.ok ? "Boosty отвязан." : res.error;
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
      boosty.formEl.hidden = true; boosty.codeField.hidden = true; boosty.confirmBtn.hidden = true;
      boosty.statusEl.textContent = ""; boosty.codeInput.value = "";
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
