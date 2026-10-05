/* Закрытый этап под замком (владелец, 2026-10-05, вариант 1): этап идёт в общем ряду («Этап 4 из 5»), но
   вместо содержимого — окно с подпиской на Boosty; «Далее» пропускает этап. Только кнопка «Подписаться» —
   разовый донат ничего не открывает. Содержимого у этапа здесь нет вовсе: без доступа сервер его не отдаёт
   (app/closed_stages.py, server/user_groups.py). Файл одинаковый для ПК (app/web/frontend/js/) и Android
   (assets/js/); стили — css/closed_stage.css. */
(() => {
  const BOOSTY_URL = "https://boosty.to/magic_sqd?locale=ru_RU";
  const LOCK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';
  const STAR = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l2.9 6.26L22 9.27l-5 4.87L18.2 21 12 17.77 5.8 21 7 14.14l-5-4.87 7.1-1.01L12 2z"/></svg>';

  function node(tag, cls, text) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (text) el.textContent = text;
    return el;
  }

  function icon(svg, cls) {
    const el = node("span", cls);
    el.setAttribute("aria-hidden", "true");
    el.innerHTML = svg;
    return el;
  }

  // stage — этап из бэкенда с closed_locked: title, next (null — последний), closed_subscribers.
  function page(stage) {
    const subscribers = stage.closed_subscribers !== false;
    const last = stage.next == null;
    const wrap = node("section", "closed-stage");
    const title = node("h1", "workflow-title closed-stage-title", stage.title || "Закрытый этап");
    const tag = node("span", "closed-stage-tag");
    tag.append(icon(LOCK, "closed-stage-tag-icon"), subscribers ? "для подписчиков" : "не для всех");
    title.append(tag);
    const card = node("div", "closed-stage-card");
    const head = node("div", "closed-stage-head");
    head.append(icon(LOCK, "closed-stage-lock"),
                node("span", "", subscribers ? "Этот этап — для подписчиков Boosty" : "Этот этап открыт не всем"));
    card.append(head);
    if (subscribers) {
      card.append(node("p", "closed-stage-text", "Подписчики Boosty видят этот этап целиком — с текстом, фото и командами."));
      const link = node("a", "boosty-link closed-stage-subscribe");
      link.href = BOOSTY_URL;
      link.target = "_blank";
      link.rel = "noopener";
      link.append(icon(STAR, "icon"), node("span", "", "Подписаться на Boosty"));
      const links = node("div", "boosty-links closed-stage-links");
      links.append(link);
      card.append(links);
      card.append(node("p", "closed-stage-hint", "Уже подписались? Войдите в аккаунт с той же почтой, что на Boosty. "
        + `Без подписки этап можно пропустить — нажмите «${last ? "Готово" : "Далее"}».`));
    } else {
      card.append(node("p", "closed-stage-text", "Его видят участники отдельных групп. Если он вам нужен — напишите нам через «Сообщить о проблеме»."));
      card.append(node("p", "closed-stage-hint", `Этап можно пропустить — нажмите «${last ? "Готово" : "Далее"}».`));
    }
    wrap.append(title, card);
    return wrap;
  }

  window.ClosedStageUI = { page };
})();
