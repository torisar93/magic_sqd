// Подсказка «Похоже, это другая машина» (владелец, 2026-09-30; вариант 3 — окно с картинками моделей): общий
// device_hint.js (ПК и Android) — ключ магнитолы, пороги совета, содержимое окна; и поток ПК в stage_wizard.js.
"use strict";
const { read, slice, assert, run, sleep } = require("./_util");
const { makeDom } = require("./_ring_dom");

const FX11 = "geely_fx11_j1|FX11_J1|msmnile_gvmq";
const TABLE = { version: 1, devices: {
  [FX11]: [{ model: "Geely/Atlas New/Monji", ok: 19, phones: 6 }, { model: "Geely/Preface/Monji", ok: 1, phones: 1 }],
  "few|x|y": [{ model: "VOLGA/C50", ok: 4, phones: 1 }],
  "split|x|y": [{ model: "A/B", ok: 5, phones: 3 }, { model: "A/C", ok: 4, phones: 2 }],
  "both|x|y": [{ model: "A/B", ok: 30, phones: 5 }, { model: "A/C", ok: 3, phones: 2 }],
} };

function load(file) {
  const { document, LabUI } = makeDom();
  const window = {};
  run(read(file), { window, document, LabUI, Number, String });
  return { DeviceHint: window.DeviceHint, document };
}

module.exports = async function () {
  for (const file of ["app/web/frontend/js/components/device_hint.js", "android/app/src/main/assets/js/device_hint.js"]) {
    const { DeviceHint } = load(file);
    // Ключ — как у сервера (server/device_models.py: fingerprint_key); баннер Android разбирается в те же поля.
    const fromBanner = DeviceHint.fromBanner("device::ro.product.name=geely_fx11_j1;ro.product.model=FX11_J1;"
      + "ro.product.device=msmnile_gvmq;features=shell_v2,cmd");
    assert(DeviceHint.key(fromBanner) === FX11, `${file}: ключ из баннера — ${DeviceHint.key(fromBanner)}`);
    assert(DeviceHint.key({ name: "geely_fx11_j1", model: "FX11_J1", device: "msmnile_gvmq" }) === FX11, `${file}: ключ ПК`);
    assert(DeviceHint.key({}) === null, `${file}: пустой отпечаток`);
    assert(DeviceHint.modelPath({ brand: "Geely", name: "Preface", modification: "Monji" }) === "Geely/Preface/Monji", file);
    assert(DeviceHint.modelPath({ brand: "VOLGA", name: "K40", modification: "" }) === "VOLGA/K40", file);

    // Открыта Preface Monji: с ней магнитола сработала лишь раз (1 из 20, реальная таблица 30.09) — советуем Atlas.
    assert(DeviceHint.suggest(TABLE, FX11, "Geely/Preface/Monji").model === "Geely/Atlas New/Monji", `${file}: совет`);
    assert(DeviceHint.suggest(TABLE, FX11, "Haval/H3").model === "Geely/Atlas New/Monji", `${file}: совет в чужой модели`);
    assert(DeviceHint.suggest(TABLE, FX11, "Geely/Atlas New/Monji") === null, `${file}: открыт сам лидер`);
    assert(DeviceHint.suggest(TABLE, "both|x|y", "A/C") === null, `${file}: с открытой моделью тоже работала (3 успеха)`);
    assert(DeviceHint.suggest(TABLE, "few|x|y", "Haval/H3") === null, `${file}: один телефон — не уверены`);
    assert(DeviceHint.suggest(TABLE, "split|x|y", "Haval/H3") === null, `${file}: лидер меньше 80% — не уверены`);
    assert(DeviceHint.suggest(TABLE, "unknown", "Haval/H3") === null && DeviceHint.suggest(null, FX11, "Haval/H3") === null,
      `${file}: нет таблицы или магнитолы`);
    assert(DeviceHint.installs(1) === "1 успешная установка" && DeviceHint.installs(3) === "3 успешные установки"
      && DeviceHint.installs(19) === "19 успешных установок" && DeviceHint.installs(21) === "21 успешная установка", file);

    // Окно варианта 3: две карточки с картинками и кнопки «Остаться» / «Перейти».
    const pressed = [];
    const [title, cards, actions] = DeviceHint.content({
      current: { label: "Geely / Preface — Monji", image: "cur.webp" },
      suggested: { label: "Geely / Atlas New — Monji", image: "sug.webp", ok: 19 },
      actionsClass: "modal-actions", onStay: () => pressed.push("stay"), onGo: () => pressed.push("go"),
    });
    assert(title.textContent === "Похоже, это другая машина", `${file}: заголовок`);
    const [cur, sug] = cards.children;
    assert(cur.className === "hint-card current" && cur.children[0].src === "cur.webp"
      && cur.textContent === "Сейчас открытаGeely / Preface — Monji", `${file}: карточка открытой — ${cur.textContent}`);
    assert(sug.className === "hint-card suggested" && sug.children[0].src === "sug.webp"
      && sug.textContent === "Магнитола как у этой моделиGeely / Atlas New — Monji✓ 19 успешных установок",
      `${file}: карточка совета — ${sug.textContent}`);
    assert(actions.className === "modal-actions" && actions.children.map((b) => b.textContent).join("|") === "Остаться|Перейти"
      && actions.children[1].className === "accent", `${file}: кнопки`);
    actions.children[1].onclick();
    actions.children[0].onclick();
    assert(pressed.join(",") === "go,stay", `${file}: нажатия`);
  }

  // Поток ПК: отпечаток → окно → «Перейти» открывает нужную модель; строки в журнале сессии.
  const file = "app/web/frontend/js/screens/stage_wizard.js";
  const core = slice(read(file), "  let sessionLog = [];", "  // -- инициализация экрана", file);
  const { DeviceHint, document } = load("app/web/frontend/js/components/device_hint.js");
  const dialogs = [];
  document.createElement = ((make) => (tag) => {
    const node = make(tag);
    if (tag === "dialog") {
      Object.assign(node, { showModal() { this.open = true; dialogs.push(this); }, close() { this.open = false; },
        addEventListener(type, handler) { this["on" + type] = handler; } });
    }
    return node;
  })(document.createElement);
  document.body.append = document.body.append.bind(document.body);
  const opened = [];
  const catalog = {
    "Geely/Atlas New/Monji": { model: { display_label: "Geely / Atlas New — Monji", key: "/cars/Geely/Atlas New/Monji", hero: "" },
      group: { hero: "data:image/webp;base64,ATLAS" } },
    "Geely/Preface/Monji": { model: { display_label: "Geely / Preface — Monji" }, group: { hero: "data:image/webp;base64,PREFACE" } },
  };
  const ctx = {
    Date, document,
    model: { brand: "Geely", name: "Preface", modification: "Monji", display_label: "Geely / Preface — Monji" },
    logFn: () => {},
    window: {
      appInfo: {}, DeviceHint,
      mainPicker: { findModelByPath: (path) => catalog[path] || null, openModel: (m) => opened.push(m.key) },
      pywebview: { api: {
        install_device_fingerprint: () => Promise.resolve({ name: "geely_fx11_j1", model: "FX11_J1", device: "msmnile_gvmq" }),
        install_device_models: () => Promise.resolve(TABLE),
        install_log_append: () => Promise.resolve(),
      } },
    },
  };
  const api = run(core + "\nthis.__api={noteDeviceFingerprint,state:()=>({sessionLog})};", ctx).__api;
  await api.noteDeviceFingerprint("ABC123");
  await sleep(0);
  assert(dialogs.length === 1 && dialogs[0].className === "dialog-info device-hint", "окно показано: " + dialogs.length);
  const images = dialogs[0].children[1].children.map((c) => c.children[0].src);
  assert(images.join("|") === "data:image/webp;base64,PREFACE|data:image/webp;base64,ATLAS", "картинки моделей: " + images);
  const go = dialogs[0].children[2].children[1];
  go.onclick();
  const lines = api.state().sessionLog;
  assert(lines.at(-2) === "Подсказка: магнитола похожа на «Geely / Atlas New — Monji» (19 успешных установок), открыта «Geely / Preface — Monji»."
    && lines.at(-1) === "Техник перешёл к «Geely / Atlas New — Monji» по подсказке.", lines.slice(-2).join(" | "));
  assert(opened.join() === "/cars/Geely/Atlas New/Monji" && dialogs[0].open === false, "открыта модель из подсказки");
  await api.noteDeviceFingerprint("ABC123");  // та же магнитола ещё раз — второго окна нет
  await sleep(0);
  assert(dialogs.length === 1, "подсказка — один раз за сессию");
};
