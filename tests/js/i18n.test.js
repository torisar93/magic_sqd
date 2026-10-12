// Английская версия интерфейса (с 1.1.4; владелец, 12.10.2026): переводчик js/i18n.js + словарь js/i18n_en.js —
// общие файлы ПК и Android (копии сверяет tests/test_shared_frontend_copies.py). Здесь — сам перевод строки
// (i18n.translate) и здоровье словаря; обход страницы (MutationObserver) проверен на стенде настоящего интерфейса.
"use strict";
const { read, assert, run } = require("./_util");

const CYRILLIC = /[А-Яа-яЁё]/;

function load(base) {
  const window = {};
  run(read(base + "i18n_en.js") + "\n" + read(base + "i18n.js"), { window, JSON, RegExp, Map, WeakMap, Object, String });
  return window;
}

module.exports = async function () {
  for (const base of ["app/web/frontend/js/", "android/app/src/main/assets/js/"]) {
    const { i18n, I18N_EN } = load(base);
    const t = (text) => i18n.translate(text);
    const eq = (text, expected) => {
      const got = t(text);
      assert(got === expected, `${base}: «${text}» → ${JSON.stringify(got)}, ждали ${JSON.stringify(expected)}`);
    };

    // Точные строки, пробелы по краям и внутри не важны.
    eq("Сообщить о проблеме", "Report a problem");
    eq("  Назад \n", "  Back \n");
    eq("Выберите марку и модель слева, чтобы увидеть\n      инструкцию и этапы установки.",
      "Choose a brand and model on the left to see the instructions and installation stages.");
    // Точка/многоточие на конце: в словаре без них.
    eq("Ничего не найдено…", "Nothing found…");
    // Переменные и формы слов.
    eq("15 моделей", "15 models");
    eq("1 модель", "1 model");
    eq("Этап 2 из 5", "Stage 2 of 5");
    eq("Удалить 1 приложение", "Remove 1 app");
    eq("Удалить 3 приложения", "Remove 3 apps");
    eq("Выбрано: 5 · Обязательных: 2", "Selected: 5 · Required: 2");
    eq("Выбрано: 5", "Selected: 5");
    // Переменная часть тоже переводится, если она есть в словаре.
    eq("Этап можно пропустить — нажмите «Далее».", "You can skip this stage — press “Next”.");
    eq("Ошибка установки: магнитола не дала права root (adb root: adbd cannot run as root)",
      "Installation error: the head unit didn't grant root (adb root: adbd cannot run as root)");
    // Число-переменная ловит только числа: «из» в обычной фразе — не «of».
    assert(t("Выйти из режима администратора") === null, `${base}: {#a} из {#b} поймал обычную фразу`);
    assert(t("список из 3 пунктов") === null, `${base}: «из» внутри фразы`);
    // Единицы — заменой куска, если русского больше не осталось.
    eq("MYUSB · 7,5 ГБ", "MYUSB · 7,5 GB");
    eq("12 МБ", "12 MB");
    eq("Без метки · 7,5 ГБ", "No label · 7,5 GB");
    // Склейка из предложений (подписи карточек каталога для экранного диктора).
    eq("Haval. 8 моделей", "Haval. 8 models");
    eq("H3. Открыть", "H3. Open");
    // Многострочное окно — по строкам; строка с «{переменной}» не глотает соседние.
    eq("Накопитель: E:\nФайловая система: FAT32\n\nФорматировать именно этот накопитель?",
      "Drive: E:\nFile system: FAT32\n\nFormat this exact drive?");
    eq("Похоже, программа установлена в Program Files, куда Windows не даёт писать без прав администратора. Решения:\n"
      + "1) Запустить magic_sqd.exe один раз через правый клик → \"Запуск от имени администратора\";",
    "It looks like the program is installed in Program Files, where Windows doesn't allow writing without administrator rights. Solutions:\n"
      + "1) Run magic_sqd.exe once via right-click → \"Run as administrator\";");
    // Чего нет в словаре — null (страница остаётся как есть), без кириллицы — тоже не трогаем.
    assert(t("Совершенно новая фраза интерфейса") === null, `${base}: неизвестная фраза`);
    assert(t("Haval Dargo") === null, `${base}: без кириллицы`);
    assert(i18n.missing().includes("Совершенно новая фраза интерфейса"), `${base}: missing() собирает непереведённое`);

    // Тексты каталога (сервер переводит, программа отдаёт словарь — extend): целиком и частью строки.
    assert(i18n.extend({ "Обычная": "Standard", "рест": "facelift", "Доступ к ADB": "ADB (catalog)",
      "Добавлена кнопка.": "Added a button.", "пусто": "" }) === 4, `${base}: extend считает тексты`);
    eq("Обычная", "Standard");
    eq("Geely Atlas New — Обычная", "Geely Atlas New — Standard");
    eq("Belgee X70 — рест", "Belgee X70 — facelift");
    assert(t("Belgee X70 рестайлинг") === null, `${base}: «рест» внутри другого слова не заменяется`);
    eq("Доступ к ADB", "ADB access");  // словарь интерфейса главнее текстов каталога
    eq("Обновлены:\n  • Haval H3: Добавлена кнопка.", "Updated:\n  • Haval H3: Added a button.");
    i18n.extend({});  // пересобранный словарь без этих строк — их больше нет
    assert(t("Geely Atlas New — Обычная") === null, `${base}: старые тексты каталога забыты`);

    // Здоровье словаря.
    for (const [key, value] of Object.entries(I18N_EN)) {
      assert(typeof value === "string" && value.trim(), `${base}: пустой перевод у «${key}»`);
      assert(!CYRILLIC.test(value), `${base}: в переводе «${key}» осталась кириллица: ${value}`);
      if (key.startsWith("re:") || key.includes(" @ ")) continue;
      // Шаблон со свободной {переменной} без своего русского текста ловил бы что попало («{a}: {b}»).
      const free = /\{(?!#)[^{}|]+\}/.test(key);
      const literal = key.replace(/\{[^{}|]+\}/g, "").replace(/\[\[[^\]]*\]\]/g, "");
      if (free) assert((literal.match(/[А-Яа-яЁё]/g) || []).length >= 4, `${base}: слишком общий шаблон «${key}»`);
      // Переменные значения есть в ключе.
      const names = new Set([...key.matchAll(/\{#?([^{}|]+)\}/g)].map((m) => m[1]));
      for (const m of value.matchAll(/\{([^{}|]+)(?:\|[^{}|]*\|[^{}|]*)?\}/g)) {
        assert(names.has(m[1]), `${base}: в переводе «${key}» переменная {${m[1]}}, которой нет в ключе`);
      }
    }
    assert(i18n.language() === "ru", `${base}: по умолчанию русский`);
  }
};
