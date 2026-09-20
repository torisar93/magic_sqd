// Блок «Спасибо вам» в окне «Готово!» — ОДИН И ТОТ ЖЕ файл для ПК (app/web/frontend/js) и Android
// (android/app/src/main/assets/js). Данные — content/supporters.json (пишет сервер при каждой правке
// в таблице пользователей админки, см. server/backend.py: build_supporters): только имена и цвет
// карточки, ни почт, ни сумм. Как файл попадает сюда — дело платформы (ПК: Python-мост, Android:
// событие supporters_result): она зовёт Thanks.set(данные). Сама вёрстка — Thanks.block().
// Без ??=/?. и т.п. — тот же старый Chromium в Win7-сборке, что и у остального фронтенда.
(function () {
  const { el } = window.dom;
  const CACHE_KEY = "magicsqd_supporters_v2";
  const HEART_PATH = "M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z";
  let current = null;

  function clean(data) {
    if (!data || !Array.isArray(data.people)) return null;
    const people = [];
    for (const item of data.people) {
      const name = item && typeof item.name === "string" ? item.name.trim().slice(0, 40) : "";
      if (!name) continue;
      people.push({ name: name, kind: item.kind === "sub" ? "sub" : "don", top: item.top === true });
    }
    return { people: people.slice(0, 500) };
  }

  // Приложение на Android открывают у магнитолы, где интернета нет, — поэтому последний удачно
  // полученный список запоминаем (на ПК localStorage не сохраняется между запусками, там список
  // просто берётся заново при старте).
  function set(data) {
    const cleaned = clean(data);
    if (!cleaned) return;
    current = cleaned;
    try { localStorage.setItem(CACHE_KEY, JSON.stringify(cleaned)); } catch (e) { /* не критично */ }
  }

  function get() {
    if (current) return current;
    try { current = clean(JSON.parse(localStorage.getItem(CACHE_KEY))); } catch (e) { current = null; }
    return current;
  }

  function plural(n) {
    const mod100 = n % 100, mod10 = n % 10;
    if (mod100 >= 11 && mod100 <= 14) return "человек";
    if (mod10 === 1) return "человек";
    if (mod10 >= 2 && mod10 <= 4) return "человека";
    return "человек";
  }

  function card(person) {
    const avatar = el("span", { class: "thanks-av", text: Array.from(person.name)[0].toUpperCase() });
    return el("div", { class: "thanks-card " + (person.kind === "sub" ? "is-sub" : "is-don") }, [
      avatar, el("span", { class: "thanks-nm", text: person.name, title: person.name }),
    ]);
  }

  function heart() {
    const span = el("span", { class: "thanks-heart" });
    span.innerHTML = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="' + HEART_PATH + '"/></svg>';
    return span;
  }

  // null, если показывать нечего (список пуст или ещё ни разу не получали).
  function block() {
    const data = get();
    if (!data || !data.people.length) return null;
    const top = data.people.filter(function (p) { return p.top; });
    const rest = data.people.filter(function (p) { return !p.top; });
    const scroll = el("div", { class: "thanks-scroll" });
    if (top.length) {
      scroll.appendChild(el("div", { class: "thanks-label", text: "Особая благодарность" }));
      scroll.appendChild(el("div", { class: "thanks-grid thanks-top" }, top.map(card)));
    }
    if (rest.length) {
      if (top.length) scroll.appendChild(el("div", { class: "thanks-label", text: "Все, кто с нами" }));
      scroll.appendChild(el("div", { class: "thanks-grid" }, rest.map(card)));
    }
    return el("section", { class: "thanks", "aria-label": "Спасибо вам" }, [
      el("div", { class: "thanks-head" }, [
        heart(),
        el("div", {}, [el("b", { text: "Спасибо вам" }), el("small", { text: "Проект живёт благодаря этим людям" })]),
        el("span", { class: "thanks-count", text: data.people.length + " " + plural(data.people.length) }),
      ]),
      scroll,
      el("div", { class: "thanks-legend" }, [
        el("span", {}, [el("i", { class: "sub" }), "Подписчик Boosty"]),
        el("span", {}, [el("i", { class: "don" }), "Донат"]),
      ]),
    ]);
  }

  window.Thanks = { set: set, get: get, block: block };
})();
