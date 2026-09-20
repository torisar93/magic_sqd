// Всплывающие окна с приглашением на Boosty — портировано с android/app/src/
// main/assets/js/app.js (boostyLinksRow/maybeShowWelcomeModal/
// showCompletionModal), тот же смысл: чистый донат/подписка (см. память
// проекта — без тиров/платного контента), просто внешние ссылки, реально
// открываются в системном браузере (pywebview: target="_blank" по умолчанию
// уходит через webbrowser.open(), см. webview.OPEN_EXTERNAL_LINKS_IN_BROWSER).
// В отличие от Android (одноразовые div-оверлеи) — здесь два готовых
// нативных <dialog> в index.html (welcome-dialog/completion-dialog), тот же
// приём, что и у остальных диалогов приложения (см. dialogs.js).
(function () {
  const { el } = window.dom;

  const STAR_ICON_PATH = "M12 2l2.9 6.26L22 9.27l-5 4.87L18.2 21 12 17.77 5.8 21 7 14.14l-5-4.87 7.1-1.01L12 2z";
  const HEART_ICON_PATH = "M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z";

  function svgIcon(pathD) {
    const span = el("span", { class: "icon" });
    span.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="${pathD}"/></svg>`;
    return span;
  }

  // ?locale=ru_RU — иначе Boosty открывается по умолчанию на английском (у Boosty нет
  // предсказуемой привязки к языку ОС/браузера технику; куки-подхват языка ставится
  // только ПОСЛЕ первого захода, а параметр в URL форсирует русский с первого клика).
  function boostyLinksRow() {
    return el("div", { class: "boosty-links" }, [
      el("a", { class: "boosty-link", href: "https://boosty.to/magic_sqd?locale=ru_RU", target: "_blank" }, [
        svgIcon(STAR_ICON_PATH), el("span", { text: "Подписаться на Boosty" }),
      ]),
      el("a", { class: "boosty-link", href: "https://boosty.to/magic_sqd/donate?locale=ru_RU", target: "_blank" }, [
        svgIcon(HEART_ICON_PATH), el("span", { text: "Разовый донат" }),
      ]),
    ]);
  }

  let welcomeDialog, welcomeLinksEl, welcomeCloseButton;
  let completionDialog, completionLinksEl, completionCloseButton, completionThanksEl;

  function init() {
    welcomeDialog = document.getElementById("welcome-dialog");
    welcomeLinksEl = document.getElementById("welcome-boosty-links");
    completionDialog = document.getElementById("completion-dialog");
    completionLinksEl = document.getElementById("completion-boosty-links");
    completionThanksEl = document.getElementById("completion-thanks");
    welcomeCloseButton = document.getElementById("welcome-dialog-close");
    completionCloseButton = document.getElementById("completion-dialog-close");
    welcomeCloseButton.addEventListener("click", () => welcomeDialog.close());
    completionCloseButton.addEventListener("click", () => completionDialog.close());
    refreshSupporters();
  }

  // Список «Спасибо вам» (см. thanks.js) забираем заранее, при старте, а не в момент окна: к концу
  // установки интернет может уже не понадобиться, но и мешать окну ждать сеть не должен.
  async function refreshSupporters() {
    try {
      const data = await window.pywebview.api.supporters_get();
      if (data && data.people) window.Thanks.set(data);
    } catch (e) { /* без списка окно просто без блока */ }
  }

  function fillCompletionThanks() {
    const block = window.Thanks.block();
    completionThanksEl.replaceChildren();
    if (block) completionThanksEl.appendChild(block);
    completionDialog.classList.toggle("has-thanks", !!block);
  }

  // Раз в час максимум в рамках ОДНОГО запуска программы — та же логика,
  // что и в мобильном приложении (там localStorage переживает перезапуск).
  // На десктопе pywebview по умолчанию поднимает окно в private_mode=True
  // (см. main_web.py: webview.start()) — localStorage не сохраняется между
  // запусками, так что на практике окно будет появляться заново при каждом
  // старте программы (что для десктопа, где сеанс работы и так ограничен
  // временем работы программы, скорее уместно, чем баг).
  const WELCOME_SHOWN_KEY = "magicsqd_welcome_shown_at";

  function maybeShowWelcomeDialog() {
    const last = Number(localStorage.getItem(WELCOME_SHOWN_KEY) || 0);
    if (Date.now() - last < 60 * 60 * 1000) return;
    localStorage.setItem(WELCOME_SHOWN_KEY, String(Date.now()));
    welcomeLinksEl.innerHTML = "";
    welcomeLinksEl.appendChild(boostyLinksRow());
    welcomeDialog.showModal();
    // Иначе Chromium/WebView2 фокусирует первую ссылку в <dialog>. Раньше
    // она из-за общего :hover/:focus-visible правила сразу выглядела наведённой.
    welcomeCloseButton.focus();
  }

  function showCompletionDialog() {
    completionLinksEl.innerHTML = "";
    completionLinksEl.appendChild(boostyLinksRow());
    fillCompletionThanks();
    completionDialog.showModal();
    completionCloseButton.focus();
    // Если при старте список не получили — пробуем ещё раз и дорисовываем, пока окно открыто.
    if (!window.Thanks.get()) refreshSupporters().then(() => { if (completionDialog.open) fillCompletionThanks(); });
  }

  window.boostyDialogs = { init, maybeShowWelcomeDialog, showCompletionDialog };
})();
