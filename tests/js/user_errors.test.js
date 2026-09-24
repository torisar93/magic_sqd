// Ошибки на стороне техника (нет магнитолы, флешки, интернета, разрешения…) — окно «что сделать» вместо
// «отправьте лог разработчику» (владелец, 2026-09-24). Тексты — реальные строки из логов на сервере обеих
// платформ (номера логов рядом). Файлы общие: app/web/frontend/js/components/{user_errors,stage_run}.js —
// копии в android/app/src/main/assets/js/ сверяет tests/test_shared_frontend_copies.py.
"use strict";
const { read, assert, run } = require("./_util");

const CASES = [
  // ПК, adb (логи #734, #736, #786, #789, #557, #365, #156)
  ["Магнитола не подключена — установка не начиналась. Подключите её кабелем (или по Wi-Fi), проверьте отладку по USB и запустите этап заново. (adb: no devices/emulators found)", "no_device"],
  [String.raw`Команда завершилась с ошибкой (1): C:\Users\user\AppData\Local\Programs\Magic SQD\tools\adb.exe install -r C:\x\Everywhere_launcher2.39.apk adb.exe: no devices/emulators found`, "no_device"],
  ["adb: error: failed to get feature set: device 'R58N12ABCDE' not found", "no_device"],
  ["adb: error: failed to get feature set: device offline", "no_device"],
  ["Магнитола отключилась во время установки — дальше ничего не ставилось. (adb: device 'X' not found)", "link_lost"],
  ["Магнитола не разрешила отладку по USB — подтвердите запрос «Разрешить отладку» на её экране и запустите этап заново. (adb: device unauthorized)", "unauthorized"],
  // Android, ADB (логи #327, #698, #788, #585, #452, #325)
  ["ADB не подключён — сначала подключись к устройству", "no_device"],
  ["Устройство с ADB-интерфейсом не найдено среди подключённых по USB — проверь, что на магнитоле включена отладка по USB и это OTG-подключение.", "no_device"],
  ["Связь с магнитолой оборвалась во время установки «monguard_app.apk». Проверьте Wi-Fi или кабель, что магнитола не ушла в сон, и запустите этап заново. Техническая причина: Не удалось отправить OPEN для sync:", "link_lost"],
  ["'logcat -c': Не удалось отправить OPEN", "link_lost"],
  ["Магнитола перестала отвечать или связь с ней оборвалась (ADB: получено -1 из 24 байт заголовка).", "link_lost"],
  ["Пользователь отклонил разрешение на доступ к USB-устройству", "usb_permission"],
  ["Не удалось подключиться по TCP к 192.168.43.1:5555: ConnectException: failed to connect to /192.168.43.1 (port 5555) from /192.168.43.20 (port 41234) after 5000ms: isConnected failed: EHOSTUNREACH (No route to host)", "wifi_unreachable"],
  ["Ошибка установки: Не удалось подключиться по telnet к [fe80::1:2:3%en0]:23: [Errno 61] Connection refused", "wifi_unreachable"],
  ["Ошибка установки: Не удалось подключиться к 192.168.1.1:5555", "wifi_unreachable"],
  // Флешка (логи #781, #783, #749, #750, #767, #671, #289, #105)
  ["Флешка не найдена (нет USB mass storage устройств) — проверь OTG-подключение.", "flash_not_found"],
  ["Флешка не подключена — сначала подключись к ней", "flash_not_found"],
  ["Подключите флешку и выберите её в списке.", "flash_not_found"],
  ["Пользователь отклонил разрешение на доступ к флешке", "usb_permission"],
  ["Не удалось записать svlog.flag: флешка перестала отвечать. Выньте и снова вставьте флешку (и OTG-переходник) и повторите запись; если не помогло — отформатируйте флешку", "flash_io"],
  ["Ошибка записи: IOException: MAX_RECOVERY_ATTEMPTS Exceeded while trying to transfer command to device, please reattach device and try again", "flash_io"],
  ["Флешка перестала отвечать при подключении (could not claim interface!)", "flash_io"],
  ["Не удалось прочитать флешку (Index 8 out of bounds for length 8)", "flash_unreadable"],
  ["Не удалось записать magic_sqd.apk: сбой файловой системы флешки — отформатируйте флешку («Параметры флешки» → «Форматировать флешку») и запишите файлы заново.", "flash_unreadable"],
  ["Ошибка: [Errno 28] No space left on device: 'E:\\\\update.zip'", "no_space"],
  ["Ошибка: [WinError 19] Носитель защищен от записи", "write_protected"],
  // QR ADB (логи #754, #766, #774, #675, #695)
  ["На флешке не найдена папка logs_*. Проверьте: файл svlog.flag был на флешке ДО того, как её вставили в магнитолу, и на экране появилась надпись «QNX OK».", "qr_no_logs"],
  ["В папке logs_20260924-0812 не найден файл bugreport-*.zip", "qr_no_bugreport"],
  // Выбор и скачивание (логи #640, #788, #378, #631, #570)
  ["Выбраны приложения с одним и тем же именем пакета (ru.kinopoisk): «Кинопоиск_7.50.3_+30%_v3.4_PBT.apk», «Кинопоиск_7.50.3_+30%_T3_v2.apk». Они заменяют друг друга и не могут стоять вместе (второе не установится из-за другой подписи). Оставьте что-то одно и запустите этап заново.", "duplicate_package"],
  ["Не удалось скачать: Яндекс_Браузер.apk, ЯМ.apk. Проверьте интернет (телефон не должен быть в Wi-Fi магнитолы без интернета) и повторите.", "no_internet"],
  ["Файл не скачан: /data/user/0/ru.magicsqd.mobile/files/cars/Geely/Monjaro/SE/files/pack/optional/monguard_app.apk", "no_internet"],
  ["Не скачаны приложения: GLauncher.Link.1.1.apk — без них установка невозможна. Проверьте интернет (компьютер не должен быть подключён к Wi-Fi магнитолы без интернета) и запустите установку заново.", "no_internet"],
  ["На магнитоле уже установлена версия «Yandex.apk» новее (или такая же), чем в этой сборке — Android не позволяет тихо откатить версию назад.", "version_conflict"],
  // Не ошибки техника — прежнее окно «отправьте разработчику» (логи #390, #299, #452, #718, #130)
  ["Ошибка установки: monji: session=1234 flags=0x116", null],
  ["3screen.apk: dex-хелпер не подтвердил успех (новых пакетов: нет): monji: session=1 flags=0x116", null],
  ["Неожиданная команда в ответе: 0x45534c43", null],
  ["Поля salt/password/sn не найдены ни в одном .txt внутри bugreport-zip", null],
  ["IOException: Item already exists!", null],
  ["Не удалось скачать файлы этапа: HTTP Error 404: Not Found", null],
  ["", null],
];

// Минимальный DOM: ровно то, что трогают stage_run.js и LabUI-заглушка.
function makeDocument() {
  class Node {
    constructor(tag) {
      this.tagName = String(tag).toUpperCase();
      this.children = []; this.parent = null; this.attrs = {}; this.dataset = {}; this._text = "";
      this.className = ""; this.style = {}; this.listeners = {}; this.disabled = false;
      const self = this;
      this.classList = {
        add: (...c) => { self.className = [...new Set([...self.className.split(" ").filter(Boolean), ...c])].join(" "); },
        remove: (...c) => { self.className = self.className.split(" ").filter((x) => x && !c.includes(x)).join(" "); },
        contains: (c) => self.className.split(" ").includes(c),
      };
    }
    set textContent(v) { this.children = []; this._text = String(v); }
    get textContent() { return this._text + this.children.map((c) => c.textContent).join(""); }
    append(...nodes) { nodes.forEach((node) => { if (node) { node.remove(); node.parent = this; this.children.push(node); } }); }
    appendChild(node) { this.append(node); return node; }
    replaceChildren(...nodes) { this.children.forEach((c) => { c.parent = null; }); this.children = []; this._text = ""; this.append(...nodes); }
    remove() { if (this.parent) { this.parent.children = this.parent.children.filter((c) => c !== this); this.parent = null; } }
    setAttribute(k, v) { this.attrs[k] = String(v); }
    getAttribute(k) { return this.attrs[k]; }
    addEventListener(kind, fn) { (this.listeners[kind] = this.listeners[kind] || []).push(fn); }
    focus() {}
    get isConnected() { return true; }
    all() { return this.children.flatMap((c) => [c, ...c.all()]); }
    querySelectorAll(selector) {
      if (selector.startsWith(":scope > .")) {
        const cls = selector.slice(":scope > .".length);
        return this.children.filter((c) => c.classList.contains(cls));
      }
      if (selector.startsWith(".") && !selector.includes(">") && !selector.includes(" ")) {
        return this.all().filter((c) => c.classList.contains(selector.slice(1)));
      }
      if (selector.startsWith("button")) return this.all().filter((c) => c.tagName === "BUTTON" && !c.disabled);
      return [];
    }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  }
  const body = new Node("body");
  const listeners = {};
  return {
    body, activeElement: null,
    createElement: (tag) => new Node(tag),
    addEventListener: (kind, fn) => { (listeners[kind] = listeners[kind] || []).push(fn); },
    removeEventListener() {},
    querySelector: () => null,
  };
}

function load(android) {
  const document = makeDocument();
  const window = {
    LabUI: { busy: (host, title) => { const status = document.createElement("div"); host.append(status); return status; } },
  };
  if (android) window.AndroidBridge = { call: () => "{}" };
  const ctx = run(read("app/web/frontend/js/components/user_errors.js") + "\n" +
    read("app/web/frontend/js/components/stage_run.js"), { window, document, navigator: {}, setTimeout });
  return { window: ctx.window, document };
}

const texts = (node) => node.all().map((c) => c._text).filter(Boolean);
const find = (node, cls) => node.all().find((c) => c.classList.contains(cls));

module.exports = async function () {
  // 1) Распознавание — одинаковое на обеих платформах, отличаются только шаги.
  for (const android of [false, true]) {
    const { window } = load(android);
    for (const [message, expected] of CASES) {
      const rule = window.UserErrors.classify(message);
      const got = rule ? rule.id : null;
      assert(got === expected, `${android ? "Android" : "ПК"}: ${JSON.stringify(message.slice(0, 90))} → ${got}, ждали ${expected}`);
      if (rule) {
        assert(rule.title && rule.steps.length > 0, `у ${rule.id} есть заголовок и шаги`);
        assert(rule.keepMessage || rule.text, `у ${rule.id} есть пояснение`);
      }
    }
  }
  const pc = load(false).window.UserErrors.byId("no_device");
  const phone = load(true).window.UserErrors.byId("no_device");
  assert(pc.steps.some((s) => /Обновить/.test(s)) && !pc.steps.some((s) => /OTG/.test(s)), "ПК: шаги про «Обновить», без OTG");
  assert(phone.steps.some((s) => /OTG-переходник/.test(s)), "Android: шаги про OTG-переходник");

  // 2) Итог этапа с ошибкой техника: окно «что сделать», без «отправьте разработчику» и кнопок лога.
  {
    const { window, document } = load(true);
    const reported = [];
    let retried = 0;
    window.StageRun.configure({ onUserError: (rule, message) => reported.push([rule.id, message]) });
    const runWin = window.StageRun.open({ title: "Установка приложений", retry: () => { retried++; } });
    runWin.finish({ success: false, message: "ADB не подключён — сначала подключись к устройству" });
    const view = find(document.body, "stage-run-result");
    assert(view.dataset.state === "user" && view.dataset.userError === "no_device", "окно ошибки техника: " + view.dataset.state);
    const all = texts(view);
    assert(all.includes("Магнитола не подключена"), "заголовок по-человечески: " + all.join(" | "));
    assert(all.includes("Что сделать") && all.some((t) => /OTG-переходник/.test(t)), "шаги видны");
    assert(!all.some((t) => /разработчику/.test(t)), "нет «отправьте лог разработчику»");
    const buttons = view.all().filter((c) => c.tagName === "BUTTON").map((b) => b._text);
    assert(!buttons.includes("Открыть лог") && buttons.includes("Повторить") && buttons.includes("Закрыть"), "кнопки: " + buttons);
    assert(all.includes("ADB не подключён — сначала подключись к устройству"), "исходный текст — в технических подробностях");
    assert(reported.length === 1 && reported[0][0] === "no_device", "платформа узнала, какое окно показано (строка в лог)");
    view.all().find((b) => b._text === "Повторить").onclick();
    await new Promise((r) => setTimeout(r, 5));
    assert(retried === 1, "«Повторить» запускает этап заново");
  }

  // 3) Обычная ошибка программы — прежнее окно с подсказкой и кнопками лога.
  {
    const { window, document } = load(false);
    const runWin = window.StageRun.open({ title: "Установка приложений" });
    runWin.finish({ success: false, message: "Ошибка установки: monji: session=1 flags=0x116" });
    const view = find(document.body, "stage-run-result");
    assert(view.dataset.state === "error", "обычная ошибка: " + view.dataset.state);
    assert(texts(view).some((t) => /отправьте разработчику/.test(t)), "подсказка про лог осталась");
    assert(view.all().some((c) => c._text === "Открыть лог"), "кнопка «Открыть лог» осталась");
  }

  // 4) Отдельное окно до запуска этапа: по id, поверх незавершённого этапа не открывается.
  {
    const { window, document } = load(false);
    const shown = window.StageRun.showUserError({ id: "no_device" });
    assert(shown && find(document.body, "stage-run-result").dataset.userError === "no_device", "окно «Магнитола не подключена» по id");
    shown.close();
    const running = window.StageRun.open({ title: "Идёт этап" });
    assert(window.StageRun.showUserError({ id: "no_device" }) === null, "идущий этап не закрываем чужим окном");
    assert(!running.finished, "идущий этап цел");
    assert(window.StageRun.showUserError({ message: "Ошибка установки: monji" }) === null, "не ошибка техника — окна нет");
  }

  // 5) Блок для окна записи на флешку (ПК): шаги без подсказки про разработчика.
  {
    const { window } = load(false);
    const block = window.StageRun.userErrorBlock("Ошибка: [Errno 28] No space left on device");
    assert(block && block.rule.id === "no_space", "нет места на флешке");
    assert(texts(block.node).some((t) => /отформатируйте/.test(t)), "шаги в блоке");
    assert(window.StageRun.userErrorBlock("Ошибка: что-то странное") === null, "неизвестная ошибка — прежний hintBlock");
  }
};
