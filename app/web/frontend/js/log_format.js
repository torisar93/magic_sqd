// Классификация строки лога по русским ключевым словам — уровни success/
// error/warn/info раскрашиваются в CSS (.log-line-*, см. components.css).
// Специально НЕ поле в событиях от Python (см. app/web/events.py) — почти
// весь текст там уже написан по-человечески (ctx.log("Готово."),
// event_bridge.push({"text": "Опубликовано на сервере."}) и т.п.), красить
// готовую строку на глаз проще и надёжнее, чем протаскивать level через
// сотни существующих call site'ов. Порядок проверок важен: "ошибка"
// проверяется раньше "успешно", чтобы строка вида "Не удалось скачать
// файл" не попала в success из-за случайного совпадения другого слова.
(function () {
  function classifyLogLevel(text) {
    // Английские "Error"/"Exception" и т.п. — сырой непереведённый вывод
    // adb/Android (см. install_api.py:_translate_console_error — переводит
    // только известные частые случаи, для остальных это единственный
    // сигнал, что строка вообще про ошибку, а не обычный вывод команды).
    if (/ошибк|не удал|отклон|не найден|провал|неизвестн|\berror\b|\bexception\b|\bfailed\b|\bfailure\b|permission denial/i.test(text)) return "error";
    if (/внимани|предупрежд/i.test(text)) return "warn";
    if (/готово\.?$|успешно|выдан|установлен|опубликован|подключ[её]н|заверш/i.test(text)) return "success";
    return "info";
  }

  function escapeHtml(text) {
    return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // Частые служебные слова в сыром английском выводе adb/Android
  // (например "Starting: Intent {...}", "Warning: ...", необработанные
  // случаи "Error"/"Exception" — см. install_api.py:_translate_console_error
  // за переводом целых сообщений про конкретные частые ошибки, здесь же —
  // общий, менее точный, но работающий для ЛЮБОЙ команды случай: само
  // ключевое слово заменяется русским и подсвечивается, остальная строка —
  // как есть. Правила друг с другом не пересекаются (разные слова без общих
  // подстрок), поэтому порядок между ними не важен.
  const KEYWORD_TRANSLATIONS = [
    { pattern: /\bfatal\b/gi, text: "Критично", cls: "error" },
    { pattern: /\bexception\b/gi, text: "Исключение", cls: "error" },
    { pattern: /\berror\b/gi, text: "Ошибка", cls: "error" },
    { pattern: /\bfailed\b/gi, text: "Не удалось", cls: "error" },
    { pattern: /\bfailure\b/gi, text: "Сбой", cls: "error" },
    { pattern: /\bdenied\b/gi, text: "Отказано", cls: "error" },
    { pattern: /\bwarning\b/gi, text: "Внимание", cls: "warn" },
    { pattern: /\bdeprecated\b/gi, text: "Устарело", cls: "warn" },
    { pattern: /\bstarting\b/gi, text: "Запуск", cls: "info" },
    { pattern: /\bsuccess(?:fully)?\b/gi, text: "Успешно", cls: "success" },
    { pattern: /\bconnected\b/gi, text: "Подключено", cls: "success" },
    { pattern: /\bdisconnected\b/gi, text: "Отключено", cls: "warn" },
    { pattern: /\baborted\b/gi, text: "Прервано", cls: "error" },
    { pattern: /\btimeout\b/gi, text: "Истекло время ожидания", cls: "error" },
    { pattern: /\bkilled\b/gi, text: "Остановлено", cls: "warn" },
    { pattern: /\bunavailable\b/gi, text: "Недоступно", cls: "warn" },
    { pattern: /\bskipped\b/gi, text: "Пропущено", cls: "warn" },
    { pattern: /\bunauthorized\b/gi, text: "Не авторизовано", cls: "error" },
    { pattern: /\boffline\b/gi, text: "Не отвечает", cls: "error" },
    { pattern: /\bdisabled\b/gi, text: "Отключено", cls: "warn" },
    { pattern: /\benabled\b/gi, text: "Включено", cls: "success" },
  ];

  // Возвращает готовый HTML (не сырой текст!) — вызывающий код обязан
  // присвоить его через innerHTML, а не textContent, иначе теги останутся
  // видны буквально. escapeHtml выше применяется ПЕРВЫМ шагом, до всех
  // замен, — без этого произвольный текст из вывода устройства (например
  // имя пакета со спецсимволами) мог бы сломать разметку строки лога.
  function highlightKeywords(text) {
    let html = escapeHtml(text);
    for (const { pattern, text: translated, cls } of KEYWORD_TRANSLATIONS) {
      html = html.replace(pattern, `<span class="log-keyword log-keyword-${cls}">${translated}</span>`);
    }
    return html;
  }

  window.classifyLogLevel = classifyLogLevel;
  window.highlightLogKeywords = highlightKeywords;
})();
