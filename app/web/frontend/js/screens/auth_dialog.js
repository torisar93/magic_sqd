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

    submitBtn.addEventListener("click", onSubmit);
    switchBtn.addEventListener("click", () => setMode(mode === "login" ? "register" : "login"));
    forgotBtn.addEventListener("click", onForgotPassword);
    logoutBtn.addEventListener("click", onLogout);
    changePasswordToggleBtn.addEventListener("click", () => {
      changePasswordSection.hidden = !changePasswordSection.hidden;
    });
    changePasswordSubmitBtn.addEventListener("click", onChangePassword);
    const onEnter = (event) => { if (event.key === "Enter") onSubmit(); };
    emailInput.addEventListener("keydown", onEnter);
    passwordInput.addEventListener("keydown", onEnter);
    const onChangePasswordEnter = (event) => { if (event.key === "Enter") onChangePassword(); };
    currentPasswordInput.addEventListener("keydown", onChangePasswordEnter);
    newPasswordInput.addEventListener("keydown", onChangePasswordEnter);
    newPasswordRepeatInput.addEventListener("keydown", onChangePasswordEnter);
    setMode("login");
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
    guestEl.hidden = Boolean(email);
    loggedinEl.hidden = !email;
    toggleEl.textContent = email || "Вход";
    if (email) {
      titleEl.textContent = "Аккаунт";
      subtitleEl.hidden = true;
      loggedinEmailEl.textContent = `Вы вошли как ${email}`;
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
    currentPasswordInput.value = "";
    newPasswordInput.value = "";
    newPasswordRepeatInput.value = "";
    changePasswordStatusEl.textContent = "";
  }

  async function onSubmit() {
    const email = emailInput.value.trim();
    const password = passwordInput.value;
    if (!email || !password) {
      statusEl.textContent = "Введите email и пароль.";
      return;
    }
    submitBtn.disabled = true;
    statusEl.textContent = mode === "login" ? "Вхожу..." : "Регистрирую...";
    if (mode === "register") {
      const result = await window.pywebview.api.auth_register(email, password);
      submitBtn.disabled = false;
      if (!result.ok) {
        statusEl.textContent = result.error;
        return;
      }
      statusEl.textContent = "Письмо с подтверждением отправлено — перейдите по ссылке из него, потом войдите здесь.";
      setMode("login");
      emailInput.value = email;
      return;
    }
    const result = await window.pywebview.api.auth_login(email, password);
    submitBtn.disabled = false;
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
    const email = emailInput.value.trim();
    if (!email) {
      statusEl.textContent = "Введите email, на который зарегистрирован аккаунт.";
      return;
    }
    forgotBtn.disabled = true;
    statusEl.textContent = "Отправляю письмо...";
    const result = await window.pywebview.api.auth_forgot_password(email);
    forgotBtn.disabled = false;
    // Сервер намеренно отвечает одинаково независимо от того, есть такой
    // email в базе или нет (см. server/backend.py:_handle_auth_forgot_
    // password) — не подтверждаем/опровергаем существование аккаунта.
    statusEl.textContent = result.ok
      ? "Если такой аккаунт есть, письмо со ссылкой для сброса пароля отправлено."
      : result.error;
  }

  async function onChangePassword() {
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
    changePasswordStatusEl.textContent = "Пароль изменён.";
  }

  async function onLogout() {
    if (!(await window.confirmDialog(`Выйти из аккаунта ${currentEmail}?`))) return;
    const result = await window.pywebview.api.auth_logout();
    if (!result.ok) return;
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
  }

  return { attach, setLoggedIn, setAdminVisible };
})();
