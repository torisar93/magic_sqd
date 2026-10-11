// Аккаунт — единственный вход в программу (владелец 11.10: «в ПК несколько мест, через которые можно
// залогиниться — привести к единому виду»). Вход/регистрация email+пароль, см. app/web/api/auth_api.py,
// server/backend.py: протокол /auth/*. Права администратора — по аккаунту (сервер принимает его сессию
// в /admin/api/*), отдельного входа по логину/паролю веб-кабинета у программы нет.
//
// Живёт в попапе кнопки аккаунта в шапке (main_picker.js: catalog-admin-toggle/-popover):
// не вошёл -> форма входа/регистрации; вошёл -> «Вы вошли как …», срок входа, у администратора — те же
// admin-actions/pending (setAdminMode в main_picker.js). Кнопка в шапке (вариант 3, выбран владельцем
// 11.10): значок пользователя в ряду с соседними, мятная рамка — администратор, точка в углу — мятная
// (вход в порядке) или красная (вход истёк).
//
// requireLogin({needAdmin, reason}) — для действий, которым нужен вход (публикация модели, выгрузка,
// заявки, файлы на сервере): сначала тихо продлевает вход (auth_refresh), и только если он нужен —
// показывает ТОТ ЖЕ попап по центру в своём модальном <dialog> (поверх любого другого окна, в т.ч.
// редактора моделей), с пояснением, зачем вход. После входа промис разрешается — действие продолжается.
window.authDialog = (() => {
  let toggleEl, popoverEl, titleEl, subtitleEl, guestEl, loggedinEl;
  let emailInput, passwordInput, statusEl, submitBtn, switchBtn, forgotBtn, logoutBtn, loggedinEmailEl;
  let changePasswordToggleBtn, changePasswordSection, currentPasswordInput, newPasswordInput,
    newPasswordRepeatInput, changePasswordStatusEl, changePasswordSubmitBtn;
  let sessionEl, noteEl, modalEl, homeParent, homeNext;
  let mode = "login"; // "login" | "register"
  let currentEmail = null;
  let isSubscriber = false;
  let adminVisible = false;
  let expired = false;          // вход истёк (сервер ответил 401) — красная точка, «Вход истёк»
  let sessionExpiresAt = null;  // эпоха-секунды: «Вход действует до …»
  let pending = null;           // { needAdmin, resolve } — ждущий requireLogin

  const api = () => window.pywebview.api;

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

    // Срок входа — под «Вы вошли как»; пояснение (зачем вход / вход истёк / нет прав) — над формами.
    sessionEl = document.createElement("div");
    sessionEl.className = "account-session";
    sessionEl.hidden = true;
    loggedinEl.querySelector(".menus13-identity").after(sessionEl);
    noteEl = document.createElement("div");
    noteEl.className = "account-note";
    noteEl.hidden = true;
    guestEl.before(noteEl);

    // Модальный режим: свой <dialog> (верхний слой — поверх редактора моделей и других окон).
    modalEl = document.createElement("dialog");
    modalEl.className = "account-modal";
    modalEl.setAttribute("aria-label", "Вход в аккаунт");
    document.body.append(modalEl);
    modalEl.addEventListener("cancel", (event) => { event.preventDefault(); cancelModal(); });
    popoverEl.querySelector("#catalog-account-close").addEventListener("click", () => { if (isModal()) cancelModal(); });

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
    switchBtn.addEventListener("click", () => {
      if (isModal() && mode === "login") { cancelModal(); return; }  // в окне входа из действия — «Отмена»
      setMode(mode === "login" ? "register" : "login");
    });
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
    // При каждом открытии окна аккаунта — свежее состояние с сервера: продлевает вход, обновляет срок,
    // права и статус подписчика (его владелец выставляет вручную, пока программа открыта).
    toggleEl.addEventListener("click", () => { if (currentEmail) refresh(); });
    setMode("login");
    renderToggle();
  }

  function setMode(newMode) {
    mode = newMode;
    if (mode === "login") {
      titleEl.textContent = "Вход";
      submitBtn.textContent = "Войти";
      switchBtn.textContent = isModal() ? "Отмена" : "Нет аккаунта? Зарегистрироваться";
    } else {
      titleEl.textContent = "Регистрация";
      submitBtn.textContent = "Зарегистрироваться";
      switchBtn.textContent = "Уже есть аккаунт? Войти";
    }
    passwordInput.autocomplete = mode === "login" ? "current-password" : "new-password";
    subtitleEl.textContent = mode === "login" ? "Один вход для техника и администратора" : "Подтвердите email после регистрации";
    statusEl.textContent = "";
    // Появляется только после неудачной попытки входа (см. onSubmit) — не
    // нужно предлагать восстановление, пока человек ещё даже не пробовал
    // войти обычным способом.
    forgotBtn.hidden = true;
  }

  // -- состояние аккаунта ----------------------------------------------------
  // account — ответ auth_api (status/login/refresh): {ok, email, is_admin, subscriber, session_expires_at,
  // expired, offline, …}. Вызывается при старте (app.js: info.auth_account), после входа/выхода и refresh.
  function setAccount(account) {
    account = account || {};
    if (account.ok && account.email) {
      expired = false;
      sessionExpiresAt = account.session_expires_at || null;
      setLoggedIn(account.email, Boolean(account.subscriber));
      return;
    }
    sessionExpiresAt = null;
    expired = Boolean(account.expired);
    setLoggedIn(null);
    if (account.email) emailInput.value = account.email;
    showNote(expired
      ? { kind: "warn", title: "Вход истёк", text: "Введите пароль — работа продолжится с того же места." }
      : (account.offline ? { kind: "warn", title: "Нет связи с сервером", text: "Вход проверю, когда появится интернет." } : null));
    renderToggle();
  }

  // Вызывается из setAccount и app.js (выход из аккаунта) — переключает попап между формой входа и
  // «Вы вошли как …», и вид кнопки в шапке.
  function setLoggedIn(email, subscriber = false) {
    currentEmail = email;
    if (email) expired = false;
    const globalStatus = popoverEl.querySelector("#catalog-account-global-status");
    if (globalStatus) globalStatus.textContent = "";
    guestEl.hidden = Boolean(email);
    loggedinEl.hidden = !email;
    applySubscriber(Boolean(email) && subscriber);
    if (email) {
      titleEl.textContent = "Аккаунт";
      subtitleEl.hidden = true;
      loggedinEmailEl.textContent = email;
      showNote(null);
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
    renderSession();
    renderToggle();
    // Вход/выход меняет, доступна ли «Установка с ИИ» (js/screens/ai_panel.js спрашивает сервер заново).
    document.dispatchEvent(new CustomEvent("magicsqd-auth", { detail: { email, subscriber } }));
  }

  // Свежее состояние с сервера; нет связи — оставляем как было.
  async function refresh() {
    let result;
    try { result = await api().auth_refresh(); } catch (_) { return null; }
    if (!result || result.offline) return result;
    if (result.ok) {
      if (Boolean(result.is_admin) !== adminVisible) window.applyAdminMode(Boolean(result.is_admin));
      setAccount(result);
    } else if (result.expired) {
      window.applyAdminMode(false);
      setAccount(result);
    }
    return result;
  }

  const fmtDate = (sec) => new Date(sec * 1000).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
  const roleText = () => (adminVisible ? "Администратор" : (isSubscriber ? "Подписчик Boosty" : "Аккаунт техника"));

  function renderSession() {
    if (!sessionEl) return;
    sessionEl.hidden = !(currentEmail && sessionExpiresAt);
    if (sessionEl.hidden) return;
    sessionEl.replaceChildren();
    const icon = window.AppIcons ? window.AppIcons.icon("check") : document.createElement("span");
    const text = document.createElement("div");
    const b = document.createElement("b");
    b.textContent = `Вход действует до ${fmtDate(sessionExpiresAt)}`;
    const s = document.createElement("span");
    s.textContent = "Продлевается сам, пока вы работаете в программе.";
    text.append(b, s);
    sessionEl.append(icon, text);
  }

  // Кнопка в шапке: не вошёл — «Вход»; вошёл — значок пользователя (как соседние), администратор — мятная
  // рамка; точка в углу — мятная (вход в порядке) / красная (вход истёк).
  function renderToggle() {
    if (!toggleEl) return;
    const loggedIn = Boolean(currentEmail);
    const iconMode = loggedIn || expired;
    toggleEl.classList.toggle("account-icon", iconMode);
    toggleEl.classList.toggle("icon-button", iconMode);
    toggleEl.classList.toggle("is-admin", loggedIn && adminVisible);
    toggleEl.classList.toggle("is-expired", expired);
    if (!iconMode) {
      toggleEl.textContent = "Вход";
      toggleEl.title = "Вход в аккаунт";
      toggleEl.setAttribute("aria-label", "Вход в аккаунт");
      return;
    }
    toggleEl.replaceChildren();
    if (window.AppIcons) toggleEl.append(window.AppIcons.icon("user"));
    const dot = document.createElement("i");
    dot.className = `account-dot ${expired ? "off" : "ok"}`;
    toggleEl.append(dot);
    const label = expired
      ? "Вход истёк — нажмите, чтобы войти снова"
      : [currentEmail, roleText(), sessionExpiresAt ? `вход до ${fmtDate(sessionExpiresAt)}` : null].filter(Boolean).join(" · ");
    toggleEl.title = label;
    toggleEl.setAttribute("aria-label", `Аккаунт: ${label}`);
  }

  function showNote(note) {
    if (!noteEl) return;
    noteEl.hidden = !note;
    noteEl.replaceChildren();
    if (!note) return;
    noteEl.dataset.kind = note.kind || "info";
    const b = document.createElement("b");
    b.textContent = note.title;
    const s = document.createElement("span");
    s.textContent = note.text || "";
    noteEl.append(b, s);
  }

  // Подписчик Boosty — кнопка аккаунта в шапке и подпись роли окрашены в цвет Boosty.
  function applySubscriber(subscriber) {
    isSubscriber = Boolean(subscriber);
    toggleEl.classList.toggle("is-subscriber", isSubscriber);
    const role = popoverEl.querySelector("#catalog-account-role");
    if (role) role.dataset.subscriber = String(isSubscriber);
    updateRoleLabel();
    window.OfflineUI?.refresh();  // замок на «Скачать заранее» снимается/ставится сразу
  }

  function updateRoleLabel() {
    const role = popoverEl.querySelector("#catalog-account-role");
    if (role) role.textContent = roleText();
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
      const result = await api().auth_register(email, password);
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
    const result = await api().auth_login(email, password);
    submitBtn.disabled = false;
    switchBtn.disabled = false;
    if (!result.ok) {
      statusEl.textContent = result.error;
      forgotBtn.hidden = false;
      return;
    }
    window.applyAdminMode(Boolean(result.is_admin));
    setAccount({ ...result, ok: true });
    if (pending) {
      if (pending.needAdmin && !result.is_admin) {
        showNote({ kind: "warn", title: "Нужны права администратора",
          text: "У этого аккаунта их нет. Выйдите и войдите аккаунтом администратора." });
        return;
      }
      finishModal(true);
      return;
    }
    popoverEl.hidden = true;
    toggleEl.setAttribute("aria-expanded", "false");
    if (result.is_admin) window.notice("Вход выполнен — функции администратора включены.");
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
    const result = await api().auth_forgot_password(email);
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
    const result = await api().auth_change_password(current, next);
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
    const result = await api().auth_logout();
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
    window.applyAdminMode(false);
    setAccount({});
    if (!isModal()) {
      popoverEl.hidden = true;
      toggleEl.setAttribute("aria-expanded", "false");
    }
  }

  // Пока admin_mode включён, у "Выйти из аккаунта" внутри admin-actions (см. app.js: admin-logout-btn) и
  // общего "Выйти" здесь одна и та же цель — прячем эту, оставляем только admin-кнопку.
  function setAdminVisible(enabled) {
    if (logoutBtn) logoutBtn.hidden = enabled;
    adminVisible = Boolean(enabled);
    updateRoleLabel();
    renderToggle();
  }

  // -- вход, когда он нужен действию ------------------------------------------
  const isModal = () => Boolean(modalEl && modalEl.open);

  async function requireLogin({ needAdmin = false, reason = "" } = {}) {
    if (pending) return pending.promise;  // окно входа уже открыто другим действием — ждём его
    if (currentEmail) await refresh();     // продлевает вход; истёкший — переключит в «Вход истёк»
    if (currentEmail && !expired && (!needAdmin || adminVisible)) return true;
    let resolveFn;
    const promise = new Promise((resolve) => { resolveFn = resolve; });
    pending = { needAdmin, resolve: resolveFn, promise };
    openModal(needAdmin, reason);
    return promise;
  }

  function openModal(needAdmin, reason) {
    homeParent = popoverEl.parentNode;
    homeNext = popoverEl.nextSibling;
    modalEl.append(popoverEl);
    popoverEl.classList.add("is-modal");
    popoverEl.hidden = false;
    toggleEl.setAttribute("aria-expanded", "false");
    modalEl.showModal();
    if (currentEmail && needAdmin && !adminVisible) {
      showNote({ kind: "warn", title: reason || "Нужны права администратора",
        text: "У этого аккаунта их нет. Выйдите и войдите аккаунтом администратора." });
      return;
    }
    if (!expired) {
      showNote({ kind: "info", title: reason || "Нужен вход",
        text: needAdmin ? "Войдите в аккаунт администратора — после входа действие продолжится само."
          : "Войдите в аккаунт — после входа действие продолжится само." });
    }
    setMode("login");
    (emailInput.value ? passwordInput : emailInput).focus({ preventScroll: true });
  }

  function finishModal(ok) {
    if (modalEl.open) modalEl.close();
    popoverEl.classList.remove("is-modal");
    popoverEl.hidden = true;
    if (homeParent) homeParent.insertBefore(popoverEl, homeNext);
    if (!expired) showNote(null);
    setMode("login");
    const done = pending;
    pending = null;
    if (done) done.resolve(ok);
  }

  function cancelModal() { finishModal(false); }

  // Действие, которое сервер может отклонить из-за входа: call() вернул {need_login} (входа нет или он
  // истёк) — окно входа, после входа один повтор. Вход отменили — остаётся первый результат.
  async function retryAfterLogin(call, reason) {
    const first = await call();
    if (!first || !first.need_login) return first;
    if (!(await requireLogin({ needAdmin: true, reason }))) return first;
    return call();
  }

  return { attach, setAccount, setLoggedIn, setAdminVisible, requireLogin, retryAfterLogin, refresh,
    logout: onLogout, isSubscriber: () => isSubscriber, isExpired: () => expired };
})();
