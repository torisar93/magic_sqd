/* Подсказка «Похоже, это другая машина» (владелец, 2026-09-30; вариант 3 макета — окно с картинками моделей). Техник
   открыл не ту модель, а при подключении ADB программа узнаёт магнитолу по таблице «магнитола → модель»
   (content/device_models.json, считает server/device_models.py по логам установок) и предлагает перейти к нужной
   инструкции. Файл ОДИНАКОВЫЙ на ПК (app/web/frontend/js/components/device_hint.js) и Android
   (android/app/src/main/assets/js/device_hint.js) — правьте оба сразу (cmp должен молчать). Окно платформа
   показывает своим способом (ПК — <dialog>, Android — showModal), здесь — только решение и содержимое. */
(() => {
  // Советуем только при уверенной статистике: у лидера не меньше MIN_OK успешных сессий с MIN_PHONES разных телефонов и
  // не меньше MIN_SHARE всех успехов с этой магнитолой. Молчим, если открыт сам лидер или с открытой моделью магнитола
  // тоже работала по-настоящему (от MIN_OK успехов). Одна случайная удача не в счёт: у FX11_J1 — 19 успехов в Atlas
  // New Monji и 1 в Preface Monji (30.09), и из-за этой одной подсказка в Preface не появилась бы никогда.
  const MIN_OK = 3, MIN_PHONES = 2, MIN_SHARE = 0.8;

  const n = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null && text !== "") node.textContent = text;
    return node;
  };

  // Ключ магнитолы «name|model|device» — тот же собирает сервер (server/device_models.py: fingerprint_key).
  function key(fp) {
    if (!fp) return null;
    const parts = [fp.name, fp.model, fp.device].map((value) => String(value || "").trim());
    return parts.some(Boolean) ? parts.join("|") : null;
  }

  // Баннер Android (AdbSession: «device::ro.product.name=…;ro.product.model=…;ro.product.device=…;features=…»).
  function fromBanner(banner) {
    const props = {};
    String(banner || "").replace(/^[^:]*::/, "").split(";").forEach((part) => {
      const at = part.indexOf("=");
      if (at > 0) props[part.slice(0, at).trim()] = part.slice(at + 1);
    });
    return { name: props["ro.product.name"] || "", model: props["ro.product.model"] || "", device: props["ro.product.device"] || "" };
  }

  // «Марка/Модель[/Модификация]» — как папки каталога и как пишет таблица.
  function modelPath(model) {
    return model ? [model.brand, model.name, model.modification].filter(Boolean).join("/") : "";
  }

  // Совет из таблицы или null. Сервер сортирует модели по убыванию успехов.
  function suggest(table, fpKey, currentPath) {
    const entries = (table && table.devices && fpKey && table.devices[fpKey]) || [];
    const top = entries[0], mine = entries.find((entry) => entry.model === currentPath);
    if (!top || top.model === currentPath || (mine && mine.ok >= MIN_OK)) return null;
    const total = entries.reduce((sum, entry) => sum + (Number(entry.ok) || 0), 0);
    if (top.ok < MIN_OK || top.phones < MIN_PHONES || top.ok < MIN_SHARE * total) return null;
    return top;
  }

  function installs(count) {
    const m10 = count % 10, m100 = count % 100;
    if (m10 === 1 && m100 !== 11) return `${count} успешная установка`;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return `${count} успешные установки`;
    return `${count} успешных установок`;
  }

  function card(cls, caption, label, image, note) {
    const box = n("div", `hint-card ${cls}`);
    const img = n("img");
    img.alt = "";
    if (image) img.src = image;
    else img.hidden = true;
    const text = n("div");
    text.append(n("span", "", caption), n("b", "", label));
    if (note) text.append(n("small", "", note));
    box.append(img, text);
    return box;
  }

  // Содержимое окна: заголовок, две карточки, кнопки. current/suggested — {label, image}; suggested.ok — успехи.
  function content({ current, suggested, actionsClass, onStay, onGo }) {
    const cards = n("div", "hint-cards");
    cards.append(
      card("current", "Сейчас открыта", current.label, current.image),
      card("suggested", "Магнитола как у этой модели", suggested.label, suggested.image, `✓ ${installs(suggested.ok)}`));
    const actions = n("div", actionsClass || "dialog-actions");
    const stay = n("button", "", "Остаться");
    stay.type = "button";
    stay.onclick = onStay;
    const go = n("button", "accent", "Перейти");
    go.type = "button";
    go.onclick = onGo;
    actions.append(stay, go);
    return [n("h2", "", "Похоже, это другая машина"), cards, actions];
  }

  // Строки журнала сессии — одинаковые на обеих платформах (их знают правила разбора логов на сервере).
  const lines = {
    shown: (target, current, ok) => `Подсказка: магнитола похожа на «${target}» (${installs(ok)}), открыта «${current}».`,
    went: (target) => `Техник перешёл к «${target}» по подсказке.`,
    stayed: (current) => `Техник остался в «${current}».`,
  };

  window.DeviceHint = { key, fromBanner, modelPath, suggest, content, installs, lines, MIN_OK, MIN_PHONES, MIN_SHARE };
})();
