// Английская версия интерфейса (с 1.1.4; владелец, 12.10.2026: «сделаем англоязычную версию программы»).
// Интерфейс по-прежнему пишется по-русски; этот модуль переводит готовую страницу по словарю js/i18n_en.js:
// видимый текст, подсказки (title), placeholder, aria-label. Новые и изменённые узлы ловит MutationObserver —
// так переводятся и окна, и сообщения из Python/Kotlin, если они есть в словаре. Лог на экране тоже (владелец,
// 12.10: «поправь всё, что осталось на русском») — журнал установки, который уходит на сервер, пишется отдельно и
// остаётся русским. Не трогает [data-i18n-skip] и поля ввода; инструкции моделей живут в iframe — их программа
// отдаёт уже переведёнными (app/catalog_i18n.py).
//
// Словарь: ключ — русский текст, как он на экране (пробелы не важны), значение — английский. {имя} в ключе —
// переменная часть (название файла, другой текст из словаря — он тоже переведётся), {#имя} — только число;
// в значении {имя} подставляется, {n|one|other} — слово по числу n (1 → one, иначе other); [[а|б|в]] в ключе —
// любой из вариантов (русские падежи: «модель|модели|моделей»); «текст @ селектор» — перевод только внутри
// элементов под селектором; «re:регэксп» — замена куска строки (единицы «5 МБ» в любом тексте), если после всех
// замен русского не осталось. Точка, двоеточие, «…» на конце переводятся сами, если в словаре есть текст без них.
// Многострочный текст (окна notice) переводится по строкам.
//
// Язык выбирает программа: настройка «Язык», «авто» — по системе (наш язык в системе — русский, иначе
// английский; app/client_env.py, ClientEnv.kt). Android отвечает сразу (синхронный мост) — страница переводится
// ещё до первого кадра; ПК — app.js после pywebviewready (setLanguage(info.ui_language)).
//
// Тексты каталога (этапы, кнопки, приложения, названия моделей) переводит сервер (server/catalog_i18n.py); у
// английского интерфейса программа отдаёт их словарь (loadCatalog → мост catalog_i18n, app/catalog_i18n.py) —
// он дополняет этот; его строки подставляются и частью текста («Geely Atlas New — Обычная»), если после замены
// русского не осталось. Инструкции моделей программа сама отдаёт в переводе.
// Общий файл ПК и Android (tests/test_shared_frontend_copies.py).
window.i18n = (() => {
  const CYRILLIC = /[А-Яа-яЁё]/;
  const ATTRS = ["title", "placeholder", "aria-label", "alt", "label"];
  const SKIP = "[data-i18n-skip], script, style, textarea, [contenteditable=''], [contenteditable='true']";
  let language = "ru";
  let exact = new Map();
  let patterns = [];
  let contextual = new Map();         // текст → [{selector, value}] («Обновить @ .offline-label»)
  let fragments = [];                 // [{re, value}] — «re:…» ключи
  let catalogExact = new Map();       // тексты каталога (extend): русский → английский
  let catalogPieces = [];             // они же для замены части строки, длинные первыми
  let compiled = false;
  let observer = null;
  const memo = new Map();
  const missing = new Map();          // непереведённое (для проверки: i18n.missing() в консоли)
  const originalText = new WeakMap(); // Text → {ru, en}: вернуть русский при смене языка, если там всё ещё наш перевод
  const originalAttrs = new WeakMap(); // Element → {атрибут: {ru, en}}

  const normalize = (text) => String(text).replace(/\s+/g, " ").trim();
  const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const VAR = /\{([^{}|]+)\}/;

  function compile() {
    compiled = true;
    exact = new Map();
    patterns = [];
    contextual = new Map();
    fragments = [];
    for (const [rawKey, value] of Object.entries(window.I18N_EN || {})) {
      if (rawKey.startsWith("re:")) {
        try { fragments.push({ re: new RegExp(rawKey.slice(3), "g"), value }); } catch (_) { /* неверный регэксп */ }
        continue;
      }
      const at = rawKey.indexOf(" @ ");
      if (at > 0) {
        const text = normalize(rawKey.slice(0, at));
        contextual.set(text, [...(contextual.get(text) || []), { selector: rawKey.slice(at + 3).trim(), value }]);
        continue;
      }
      const key = normalize(rawKey);
      if (!VAR.test(key) && !key.includes("[[")) { exact.set(key, value); continue; }
      const names = [];
      let source = "";
      for (const part of key.split(/(\{[^{}|]+\}|\[\[[^\]]*\]\])/)) {
        if (!part) continue;
        if (/^\{#[^{}|]+\}$/.test(part)) { names.push(part.slice(2, -1)); source += "(\\d+(?:[.,\\u00a0\\u202f ]\\d+)*)"; }
        else if (/^\{[^{}|]+\}$/.test(part)) { names.push(part.slice(1, -1)); source += "(.+?)"; }
        else if (part.startsWith("[[")) source += "(?:" + part.slice(2, -2).split("|").map(escapeRe).join("|") + ")";
        else source += escapeRe(part);
      }
      // Чем больше в ключе постоянного текста, тем раньше он проверяется: «Скачиваю {name}…» раньше «{name}…».
      const weight = key.replace(/\{[^{}|]+\}/g, "").length + (key.match(/\{#/g) || []).length;
      // Самый длинный постоянный кусок — быстрая проверка до регулярного выражения (шаблонов сотни).
      const anchor = key.split(/\{[^{}|]+\}|\[\[[^\]]*\]\]/).reduce((a, b) => (b.length > a.length ? b : a), "").trim();
      patterns.push({ re: new RegExp("^" + source + "$", "s"), names, value, weight, anchor });
    }
    patterns.sort((a, b) => b.weight - a.weight);
    memo.clear();
  }

  function fill(value, names, groups, depth) {
    const vars = {};
    names.forEach((name, index) => { vars[name] = groups[index]; });
    return value.replace(/\{([^{}|]+)(?:\|([^{}|]*)\|([^{}|]*))?\}/g, (whole, name, one, other) => {
      if (!(name in vars)) return whole;
      const raw = vars[name];
      if (one !== undefined) return raw.trim() === "1" ? one : other;
      const inner = lookup(raw.trim(), depth + 1) ?? lookupFragments(raw.trim()) ?? lookupList(raw.trim(), depth + 1);
      return inner == null ? raw : raw.replace(raw.trim(), () => inner);
    });
  }

  // Перечень через запятую («не выполнено: Выбор приложений, Доступ к ADB»): каждый пункт — сам по себе.
  function lookupList(text, depth) {
    if (!text.includes(", ") || depth > 2) return null;
    let changed = false;
    const parts = text.split(", ").map((part) => {
      if (!CYRILLIC.test(part)) return part;
      const out = lookup(part, depth) ?? lookupFragments(part);
      if (out != null) changed = true;
      return out;
    });
    return changed && !parts.includes(null) ? parts.join(", ") : null;
  }

  function lookup(text, depth = 0) {
    if (!text || !CYRILLIC.test(text)) return null;
    const norm = normalize(text);
    const direct = exact.get(norm) ?? catalogExact.get(norm);
    if (direct != null) return direct;
    const tail = norm.match(/^(.*?[^.:!?…\s])\s*([.:!?…]+)$/s);
    const bare = tail ? exact.get(tail[1]) ?? catalogExact.get(tail[1]) : null;
    if (bare != null) return bare + tail[2];
    if (depth > 2) return null;
    for (const pattern of patterns) {
      if (pattern.anchor && !norm.includes(pattern.anchor)) continue;
      const match = pattern.re.exec(norm);
      if (match) return fill(pattern.value, pattern.names, match.slice(1), depth);
    }
    return null;
  }

  // «• Текст», «1) Текст», «— Текст»: значок списка оставить, перевести остальное.
  function lookupLine(line) {
    const whole = lookup(line);
    if (whole != null) return whole;
    const parts = line.match(/^(\s*(?:[-–—•·*]\s+|\d+[.)]\s+))(.+?)\s*$/s);
    const inner = parts ? lookup(parts[2]) : null;
    return inner == null ? lookupFragments(line) : parts[1] + inner;
  }

  // Замены кусков («5 МБ» → «5 MB», «… — Обычная» → «… — Standard»): годятся, только если русского не осталось.
  function lookupFragments(text) {
    let out = text;
    for (const fragment of fragments) out = out.replace(fragment.re, fragment.value);
    for (let i = 0; i < catalogPieces.length && CYRILLIC.test(out); i++) {
      if (out.includes(catalogPieces[i][0])) out = replaceWhole(out, catalogPieces[i][0], catalogPieces[i][1]);
    }
    return out !== text && !CYRILLIC.test(out) ? out : null;
  }

  // Замена только целого слова/фразы: «рест» в «Belgee X70 — рест», но не внутри «рестайлинг».
  const WORD = /[0-9A-Za-zА-Яа-яЁё]/;
  function replaceWhole(text, piece, value) {
    let result = "";
    let from = 0;
    for (let at = text.indexOf(piece); at >= 0; at = text.indexOf(piece, at + 1)) {
      if (at < from) continue;
      const before = at > 0 ? text[at - 1] : "";
      const after = text.charAt(at + piece.length);
      if ((before && WORD.test(before)) || (after && WORD.test(after))) continue;
      result += text.slice(from, at) + value;
      from = at + piece.length;
    }
    return result + text.slice(from);
  }

  function noteMissing(text) {
    if (missing.size < 2000) missing.set(text, (missing.get(text) || 0) + 1);
  }

  // Предложения строки: «Не удалось. Проверьте адрес.» → ["Не удалось.", "Проверьте адрес."] (без lookbehind —
  // его нет в старом Safari/WKWebView).
  function sentences(text) {
    const parts = [];
    const re = /[.!?…]\s+(?=[А-ЯЁA-Z0-9«"(])/g;
    let last = 0;
    for (let match = re.exec(text); match; match = re.exec(text)) {
      parts.push(text.slice(last, match.index + 1));
      last = match.index + match[0].length;
    }
    parts.push(text.slice(last));
    return parts;
  }

  // Одна строка: целиком из словаря, иначе по предложениям (склейки вроде «Модель. Скачана заранее. Открыть»).
  // Шаблон со {свободной} переменной может проглотить и следующее предложение («…записан на {диск}.» + «Убран
  // файл…»), оставив его русским, — тогда берём перевод по предложениям, если в нём русского не осталось.
  function translateLine(line) {
    const whole = lookupLine(line);
    if (whole != null && !CYRILLIC.test(whole)) return whole;
    // «[Geely / Atlas New — Обычная] текст» — строка лога модели на ПК (stage_wizard.js: log): подпись и текст отдельно.
    const tagged = line.match(/^\[([^\]\n]+)\](\s+)(.*\S.*)$/s);
    if (tagged && CYRILLIC.test(tagged[3])) {
      const rest = translateLine(tagged[3]);
      if (rest != null) {
        const label = CYRILLIC.test(tagged[1]) ? lookup(tagged[1]) ?? lookupFragments(tagged[1]) : null;
        return `[${label ?? tagged[1]}]${tagged[2]}${rest}`;
      }
    }
    const parts = sentences(line);
    if (parts.length < 2) return whole;
    let changed = false;
    const out = parts.map((part) => {
      if (!CYRILLIC.test(part)) return part;
      const done = lookupLine(part.trim());
      if (done == null) { if (whole == null) noteMissing(normalize(part)); return part.trim(); }
      changed = true;
      return done;
    });
    const split = changed ? out.join(" ") : null;
    if (whole == null) return split;
    return split != null && !CYRILLIC.test(split) ? split : whole;
  }

  // Перевод строки целиком; null — переводить нечего или нет в словаре.
  function translate(text) {
    if (!text || !CYRILLIC.test(text)) return null;
    if (!compiled) compile();
    if (memo.has(text)) return memo.get(text);
    const lead = text.match(/^\s*/)[0];
    const trail = text.slice(lead.length).match(/\s*$/)[0];
    const core = text.slice(lead.length, text.length - trail.length);
    // Многострочный текст — только целиком из словаря или по строкам: {имя} не должно глотать соседние строки.
    let done = core.includes("\n") ? exact.get(normalize(core)) ?? null : translateLine(core);
    if (done == null && core.includes("\n")) {
      // Многострочное окно (notice): по строкам; непереведённые строки остаются как есть.
      let changed = false;
      const lines = core.split("\n").map((line) => {
        const out = CYRILLIC.test(line) ? translateLine(line) : null;
        if (out == null) { if (CYRILLIC.test(line)) noteMissing(line.trim()); return line; }
        changed = true;
        return out;
      });
      if (changed) done = lines.join("\n");
    } else if (done == null) {
      noteMissing(normalize(core));
    }
    const result = done == null ? null : lead + done + trail;
    if (memo.size > 5000) memo.clear();
    memo.set(text, result);
    return result;
  }

  const skipped = (element) => Boolean(element && element.closest && element.closest(SKIP));

  // Перевод по месту: одно русское слово — разный английский в разных окнах.
  function inPlace(text, element) {
    if (!contextual.size || !element) return null;
    const list = contextual.get(normalize(text));
    if (!list) return null;
    for (const entry of list) {
      try { if (element.closest(entry.selector)) return text.match(/^\s*/)[0] + entry.value + text.match(/\s*$/)[0]; }
      catch (_) { /* неверный селектор в словаре — пропустить */ }
    }
    return null;
  }

  function translateText(node) {
    const data = node.data;
    if (!CYRILLIC.test(data)) return;
    if (!compiled) compile();
    const out = inPlace(data, node.parentElement) ?? translate(data);
    if (out == null || out === data) return;
    originalText.set(node, { ru: data, en: out });
    node.data = out;
  }

  function translateAttrs(element) {
    for (const name of ATTRS) {
      const value = element.getAttribute(name);
      if (!value || !CYRILLIC.test(value)) continue;
      if (!compiled) compile();
      const out = inPlace(value, element) ?? translate(value);
      if (out == null || out === value) continue;
      const saved = originalAttrs.get(element) || {};
      saved[name] = { ru: value, en: out };
      originalAttrs.set(element, saved);
      element.setAttribute(name, out);
    }
    if (element.tagName === "INPUT" && (element.type === "button" || element.type === "submit")) {
      const out = translate(element.value);
      if (out != null) element.value = out;
    }
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) { if (!skipped(root.parentElement)) translateText(root); return; }
    if (root.nodeType === 1) {
      if (skipped(root)) { if (!skipped(root.parentElement)) translateAttrs(root); return; }
      translateAttrs(root);
    } else if (root.nodeType !== 9 && root.nodeType !== 11) {
      return;
    }
    // Лог и прочее под SKIP: подпись самого контейнера (aria-label) переводим, содержимое — нет.
    const walker = document.createTreeWalker(root, 1 | 4, {
      acceptNode: (node) => {
        if (node.nodeType !== 1 || !node.matches(SKIP)) return 1;
        translateAttrs(node);
        return 2;
      },
    });
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      if (node.nodeType === 3) translateText(node); else translateAttrs(node);
    }
  }

  function onMutations(records) {
    for (const record of records) {
      if (record.type === "childList") {
        record.addedNodes.forEach((node) => { if (node.isConnected) walk(node); });
      } else if (record.type === "characterData") {
        if (!skipped(record.target.parentElement)) translateText(record.target);
      } else if (record.type === "attributes" && !skipped(record.target.parentElement)) {
        translateAttrs(record.target);
      }
    }
  }

  function start() {
    if (observer) return;
    observer = new MutationObserver(onMutations);
    observer.observe(document.documentElement, {
      childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS,
    });
    walk(document.documentElement);
  }

  function stop() {
    if (!observer) return;
    onMutations(observer.takeRecords());
    observer.disconnect();
    observer = null;
    // Вернуть русский там, где на странице всё ещё наш перевод (что программа с тех пор переписала — не трогаем).
    const walker = document.createTreeWalker(document.documentElement, 1 | 4);
    for (let node = walker.currentNode; node; node = walker.nextNode()) {
      if (node.nodeType === 3) {
        const saved = originalText.get(node);
        if (saved && node.data === saved.en) node.data = saved.ru;
      } else if (originalAttrs.has(node)) {
        for (const [name, saved] of Object.entries(originalAttrs.get(node))) {
          if (node.getAttribute(name) === saved.en) node.setAttribute(name, saved.ru);
        }
      }
    }
  }

  function setLanguage(next) {
    const value = next === "en" ? "en" : "ru";
    if (value === language) return language;
    language = value;
    document.documentElement.lang = value;
    document.documentElement.classList.toggle("lang-en", value === "en");
    if (value === "en") { start(); setTimeout(loadCatalog, 0); } else stop();
    return language;
  }

  // Словарь текстов каталога (весь заново — после синхронизации что-то могло поменяться).
  function extend(strings) {
    if (!compiled) compile();
    catalogExact = new Map();
    for (const [ru, en] of Object.entries(strings || {})) {
      if (typeof en === "string" && en.trim() && CYRILLIC.test(ru)) catalogExact.set(normalize(ru), en);
    }
    catalogPieces = [...catalogExact.entries()].filter(([ru]) => ru.length >= 3).sort((a, b) => b[0].length - a[0].length);
    memo.clear();
    for (const text of [...missing.keys()]) if (catalogExact.has(text)) missing.delete(text);
    if (observer) walk(document.documentElement);
    return catalogExact.size;
  }

  // Переводы каталога у программы: Android — синхронный мост, ПК — pywebview. Только английскому интерфейсу.
  async function loadCatalog() {
    if (language !== "en") return 0;
    try {
      let reply = null;
      if (window.AndroidBridge) reply = JSON.parse(window.AndroidBridge.call("catalog_i18n", "{}") || "{}");
      else if (window.pywebview && window.pywebview.api && window.pywebview.api.catalog_i18n) {
        reply = await window.pywebview.api.catalog_i18n();
      }
      return reply && reply.strings ? extend(reply.strings) : 0;
    } catch (_) {
      return 0;  // старая сборка без перевода каталога или сбой чтения — интерфейс переведён, каталог нет
    }
  }

  // Для кода, которому нужен текст, а не узел страницы (системные окна, document.title…).
  const t = (text) => (language === "en" ? translate(text) ?? text : text);

  // Android: синхронный мост есть уже при загрузке страницы — переводим до первого кадра.
  try {
    if (window.AndroidBridge) {
      const reply = JSON.parse(window.AndroidBridge.call("ui_language", "{}") || "{}");
      if (reply && reply.language) setLanguage(reply.language);
    }
  } catch (_) { /* старая сборка без ui_language — по app_version в app.js */ }

  return {
    setLanguage, t, translate, extend, loadCatalog,
    language: () => language,
    missing: () => [...missing.entries()].sort((a, b) => b[1] - a[1]).map(([text]) => text),
  };
})();
