// Доступ по региону (с 1.1.4; app/access_gate.py, server/access_policy.py). Владелец, 12.10.2026: бесплатно — наши
// языки и часовые пояса, остальным — по подписке Boosty. Если сервер говорит «нужна подписка», каталог закрывает это
// окно: подписаться на Boosty, войти аккаунтом подписчика (тот же вход, что в шапке), проверить снова. Язык окна —
// язык интерфейса программы (info.ui_language).
// Обязательная регистрация (владелец 14.10.2026, переключатель во вкладке «Доступ» админки): сервер отвечает
// access=login_required — то же окно, но «Войдите в аккаунт», без Boosty: вход и регистрация бесплатны.
window.accessGate = (() => {
  const TEXT = {
    ru: {
      title: "Нужна подписка",
      text: "В вашем регионе Magic SQD работает по подписке Boosty. Оформите подписку и войдите в программу " +
        "аккаунтом подписчика — каталог откроется сам.",
      why: "Регион программа определяет по языку системы, раскладкам клавиатуры и часовому поясу.",
      boosty: "Подписаться на Boosty", login: "Войти", again: "Проверить снова", checking: "Проверяю…",
      still: "Подписки пока нет — каталог закрыт.", offline: "Нет связи с сервером — действует последнее решение.",
      reason: "Вход для доступа к каталогу",
      loginTitle: "Войдите в аккаунт",
      loginText: "Каталог Magic SQD открыт для зарегистрированных техников. Войдите или зарегистрируйтесь — " +
        "это бесплатно, каталог откроется сам.",
      loginButton: "Войти или зарегистрироваться", loginStill: "Вход не выполнен — каталог закрыт.",
    },
    en: {
      title: "Subscription required",
      text: "In your region Magic SQD is available with a Boosty subscription. Subscribe, then sign in to the " +
        "program with your subscriber account — the catalog will open automatically.",
      why: "The region is determined by your system language, keyboard layouts and time zone.",
      boosty: "Subscribe on Boosty", login: "Sign in", again: "Check again", checking: "Checking…",
      still: "No subscription yet — the catalog stays closed.", offline: "No connection to the server — the last decision applies.",
      reason: "Sign in to open the catalog",
      loginTitle: "Sign in to your account",
      loginText: "The Magic SQD catalog is open to registered technicians. Sign in or create an account — " +
        "it's free, and the catalog will open automatically.",
      loginButton: "Sign in or register", loginStill: "Not signed in — the catalog stays closed.",
    },
  };
  const DEFAULT_BOOSTY = "https://boosty.to/magic_sqd";
  let root = null, statusEl = null, boostyEl = null, titleEl = null, textEl = null, whyEl = null, loginEl = null;
  let state = null;
  let lang = "ru";
  const t = (key) => (TEXT[lang] || TEXT.ru)[key];

  function build() {
    root = document.createElement("div");
    root.className = "access-gate";
    root.hidden = true;
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-labelledby", "access-gate-title");
    const card = document.createElement("div");
    card.className = "access-gate-card";
    const title = titleEl = document.createElement("h2");
    title.id = "access-gate-title";
    title.textContent = t("title");
    const text = textEl = document.createElement("p");
    text.textContent = t("text");
    const why = whyEl = document.createElement("p");
    why.className = "access-gate-why";
    why.textContent = t("why");
    const actions = document.createElement("div");
    actions.className = "access-gate-actions";
    boostyEl = document.createElement("a");
    boostyEl.className = "boosty-link access-gate-boosty";
    boostyEl.target = "_blank";
    boostyEl.rel = "noopener";
    boostyEl.textContent = t("boosty");
    const login = loginEl = document.createElement("button");
    login.className = "accent";
    login.textContent = t("login");
    login.addEventListener("click", async () => {
      if (await window.authDialog.requireLogin({ reason: t("reason") })) recheck();
    });
    const again = document.createElement("button");
    again.textContent = t("again");
    again.addEventListener("click", () => recheck());
    actions.append(boostyEl, login, again);
    statusEl = document.createElement("p");
    statusEl.className = "access-gate-status";
    statusEl.setAttribute("role", "status");
    card.append(title, text, why, actions, statusEl);
    root.append(card);
    document.body.append(root);
  }

  function render() {
    if (!root) build();
    const closed = Boolean(state && state.allowed === false);
    root.hidden = !closed;
    document.body.classList.toggle("access-closed", closed);
    if (!closed) return;
    const loginOnly = loginRequired();
    titleEl.textContent = t(loginOnly ? "loginTitle" : "title");
    textEl.textContent = t(loginOnly ? "loginText" : "text");
    whyEl.hidden = loginOnly;
    boostyEl.hidden = loginOnly;
    loginEl.textContent = t(loginOnly ? "loginButton" : "login");
    boostyEl.href = (state && state.boosty) || DEFAULT_BOOSTY;
    statusEl.textContent = state && state.offline ? t("offline") : "";
  }

  const loginRequired = () => Boolean(state && state.access === "login_required");

  function apply(next) {
    const wasClosed = Boolean(state && state.allowed === false);
    state = next || null;
    render();
    const nowOpen = !(state && state.allowed === false);
    if (wasClosed && nowOpen) {
      // Доступ появился (вошёл подписчик, правило поменяли) — каталог заново.
      window.pywebview.api.sync_startup().then(() => window.mainPicker.reload()).catch(() => {});
    }
  }

  let pending = null;  // проверка уже идёт (вход даёт и событие magicsqd-auth, и итог requireLogin) — одна на двоих

  function recheck() {
    if (pending) return pending;
    if (statusEl) statusEl.textContent = t("checking");
    pending = (async () => {
      let next;
      try { next = await window.pywebview.api.access_check(); } catch (_) { next = state; }
      apply(next);
      if (next && next.allowed === false && statusEl && !next.offline) statusEl.textContent = t(loginRequired() ? "loginStill" : "still");
      return next;
    })().finally(() => { pending = null; });
    return pending;
  }

  function init(info) {
    lang = info && info.ui_language === "en" ? "en" : "ru";
    window.events.on("access_state", (event) => apply(event.state));
    // Вход и выход из аккаунта (auth_dialog.js) — проверить заново, если каталог закрыт.
    document.addEventListener("magicsqd-auth", () => { if (state && state.allowed === false) recheck(); });
  }

  return { init, apply, recheck, isClosed: () => Boolean(state && state.allowed === false) };
})();
