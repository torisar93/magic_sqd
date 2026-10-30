// Доступ по региону (с 1.1.4; python/access_gate.py, server/access_policy.py). Владелец, 12.10.2026: бесплатно — наши
// языки и часовые пояса, остальным — по подписке Boosty. Если сервер говорит «нужна подписка», каталог закрывает это
// окно: подписаться на Boosty, войти аккаунтом подписчика (окно аккаунта приложения), проверить снова. Итог проверки
// Kotlin присылает событием access_state (WebBridge.startSync, accessCheckAsync). ПК — js/components/access_gate.js.
// Обязательная регистрация (владелец 14.10.2026): access=login_required — «Войдите в аккаунт», без Boosty.
// Бан аккаунта (владелец 30.10.2026, сервер: ban_user): access=banned — «Доступ закрыт», без Boosty и входа.
window.accessGate = (() => {
  const TEXT = {
    ru: {
      title: "Нужна подписка",
      text: "В вашем регионе Magic SQD работает по подписке Boosty. Оформите подписку и войдите в приложение " +
        "аккаунтом подписчика — каталог откроется сам.",
      why: "Регион приложение определяет по языку телефона, клавиатуры и часовому поясу.",
      boosty: "Подписаться на Boosty", login: "Войти", again: "Проверить снова", checking: "Проверяю…",
      still: "Подписки пока нет — каталог закрыт.", offline: "Нет связи с сервером — действует последнее решение.",
      loginTitle: "Войдите в аккаунт",
      loginText: "Каталог Magic SQD открыт для зарегистрированных техников. Войдите или зарегистрируйтесь — " +
        "это бесплатно, каталог откроется сам.",
      loginButton: "Войти или зарегистрироваться", loginStill: "Вход не выполнен — каталог закрыт.",
      bannedTitle: "Доступ закрыт",
      bannedText: "Доступ к каталогу Magic SQD для этого аккаунта или телефона закрыт администратором. " +
        "Если это ошибка — свяжитесь с нами.",
      bannedStill: "Доступ по-прежнему закрыт.",
    },
    en: {
      title: "Subscription required",
      text: "In your region Magic SQD is available with a Boosty subscription. Subscribe, then sign in to the app " +
        "with your subscriber account — the catalog will open automatically.",
      why: "The region is determined by your phone language, keyboard and time zone.",
      boosty: "Subscribe on Boosty", login: "Sign in", again: "Check again", checking: "Checking…",
      still: "No subscription yet — the catalog stays closed.", offline: "No connection to the server — the last decision applies.",
      loginTitle: "Sign in to your account",
      loginText: "The Magic SQD catalog is open to registered technicians. Sign in or create an account — " +
        "it's free, and the catalog will open automatically.",
      loginButton: "Sign in or register", loginStill: "Not signed in — the catalog stays closed.",
      bannedTitle: "Access closed",
      bannedText: "The administrator has closed access to the Magic SQD catalog for this account or phone. " +
        "If this is a mistake, please contact us.",
      bannedStill: "Access is still closed.",
    },
  };
  const DEFAULT_BOOSTY = "https://boosty.to/magic_sqd";
  let root = null, statusEl = null, boostyEl = null, titleEl = null, textEl = null, whyEl = null, loginEl = null;
  let state = null, lang = "ru", openAccount = null, onOpened = null, checking = false;
  const t = (key) => (TEXT[lang] || TEXT.ru)[key];

  function build() {
    root = document.createElement("div");
    root.className = "access-gate";
    root.hidden = true;
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    const card = document.createElement("div");
    card.className = "access-gate-card";
    const title = titleEl = document.createElement("h2");
    title.textContent = t("title");
    const text = textEl = document.createElement("p");
    text.textContent = t("text");
    const why = whyEl = document.createElement("p");
    why.className = "access-gate-why";
    why.textContent = t("why");
    const actions = document.createElement("div");
    actions.className = "access-gate-actions";
    boostyEl = document.createElement("a");
    boostyEl.className = "access-gate-boosty";
    boostyEl.target = "_blank";
    boostyEl.rel = "noopener";
    boostyEl.textContent = t("boosty");
    const login = loginEl = document.createElement("button");
    login.className = "primary";
    login.textContent = t("login");
    login.addEventListener("click", () => { if (openAccount) openAccount(); });
    const again = document.createElement("button");
    again.textContent = t("again");
    again.addEventListener("click", recheck);
    actions.append(boostyEl, login, again);
    statusEl = document.createElement("p");
    statusEl.className = "access-gate-status";
    statusEl.setAttribute("role", "status");
    card.append(title, text, why, actions, statusEl);
    root.append(card);
    document.body.append(root);
  }

  function apply(next, source) {
    if (!root) build();
    const wasClosed = Boolean(state && state.allowed === false);
    state = next || null;
    const closed = Boolean(state && state.allowed === false);
    root.hidden = !closed;
    document.body.classList.toggle("access-closed", closed);
    if (closed) {
      const mode = state.access === "banned" ? "banned" : state.access === "login_required" ? "login" : "";
      titleEl.textContent = t(mode ? mode + "Title" : "title");
      textEl.textContent = t(mode ? mode + "Text" : "text");
      whyEl.hidden = boostyEl.hidden = Boolean(mode);
      loginEl.hidden = mode === "banned";  // подписка и вход заблокированному не помогут
      loginEl.textContent = t(mode === "login" ? "loginButton" : "login");
      boostyEl.href = state.boosty || DEFAULT_BOOSTY;
      statusEl.textContent = state.offline ? t("offline") : (checking ? t(mode ? mode + "Still" : "still") : "");
    }
    checking = false;
    // Доступ появился («Проверить снова», правило поменяли) — каталог заново; после входа он и так уже синхронизируется.
    if (wasClosed && !closed && onOpened && source !== "sync") onOpened();
  }

  function recheck() {
    checking = true;
    if (statusEl) statusEl.textContent = t("checking");
    Bridge.call("access_check", {});
  }

  function init(options) {
    lang = options && options.uiLanguage === "en" ? "en" : "ru";
    openAccount = options && options.openAccount;
    onOpened = options && options.onOpened;
    window.events.on("access_state", (event) => apply(event.state, event.source));
  }

  return { init, apply, recheck, isClosed: () => Boolean(state && state.allowed === false) };
})();
