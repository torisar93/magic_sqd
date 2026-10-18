// Подсказка про кнопку «Собрать подробный лог» в окне непонятной ошибки этапа (владелец, 18.10.2026: «надо добавить
// подсказку для техника, чтобы можно было ее нажать»): stage_run.js — текст (общий для ПК и Android), платформа —
// есть ли кнопка у открытой модели и уместна ли она (этап с ADB, не флешка): stage_wizard.js / app.js.
"use strict";
const { read, assert, run, slice } = require("./_util");
const { makeDom } = require("./_ring_dom");

const GENERIC = "Если не понимаете, что произошло, откройте лог, скопируйте его и отправьте разработчику.";

function load() {
  const { document, LabUI, body } = makeDom();
  const window = { LabUI: { ...LabUI, busy: (host) => { const status = document.createElement("div"); host.append(status); return status; } } };
  run(read("app/web/frontend/js/components/user_errors.js") + "\n" + read("app/web/frontend/js/components/stage_run.js"),
    { window, document, navigator: {}, setTimeout });
  return { StageRun: window.StageRun, body };
}

function failWith(StageRun, body, message) {
  const w = StageRun.open({ title: "Установка приложений" });
  w.finish({ success: false, message });
  const hint = body.querySelector(".stage-run-hint");
  return hint ? hint.textContent : null;
}

const UNCLEAR = "Не удалось установить NS.apk ни одним из способов: Failure [INSTALL_FAILED_INTERNAL_ERROR]";

function platformHelper(file, marker) {
  const src = read(file);
  const code = slice(src, "  const DIAGNOSTICS_LABELS", "\n  }\n", file) + "\n  }\n";
  return (stages, currentIndex) => {
    const ctx = { stages, currentIndex, result: null, String };
    run(code + "\nresult = diagnosticsStageTitle();", ctx);
    return ctx.result;
  };
}

module.exports = async function () {
  // 1) Без кнопки у модели — прежняя подсказка «откройте лог…».
  {
    const { StageRun, body } = load();
    assert(failWith(StageRun, body, UNCLEAR) === GENERIC, "без кнопки — общая подсказка");
  }
  // 2) Кнопка есть — подсказка ведёт к ней, с названием этапа.
  {
    const { StageRun, body } = load();
    StageRun.configure({ diagnosticsStage: () => "Дополнительные действия" });
    const hint = failWith(StageRun, body, UNCLEAR);
    assert(hint === "Если не понимаете, что произошло: на этапе «Дополнительные действия» нажмите «Собрать подробный лог» — " +
      "так разработчик увидит, почему не получилось.", "подсказка про кнопку: " + hint);
  }
  // 3) Ошибка на стороне техника — окно «что сделать», подсказки нет вовсе; сбой функции платформы — общая подсказка.
  {
    const { StageRun, body } = load();
    StageRun.configure({ diagnosticsStage: () => "Дополнительные действия" });
    assert(failWith(StageRun, body, "adb: error: failed to get feature set: device offline") === null, "окно техника без подсказки");
    const other = load();
    other.StageRun.configure({ diagnosticsStage: () => { throw new Error("нет этапов"); } });
    assert(failWith(other.StageRun, other.body, UNCLEAR) === GENERIC, "сбой платформы — общая подсказка");
  }
  // 4) Платформы: этап с кнопкой — его название; ошибка на флешке или у модели без кнопки — null.
  const STAGES = [
    { type: "instruction", title: "Доступ к ADB" },
    { type: "apps", title: "Выбор приложений" },
    { type: "usb", title: "Флешка" },
    { type: "actions", title: "Доп. кнопки", actions: [{ label: "Выдать разрешения" }, { label: "Собрать подробный лог" }] },
  ];
  for (const file of ["app/web/frontend/js/screens/stage_wizard.js", "android/app/src/main/assets/js/app.js"]) {
    const title = platformHelper(file);
    assert(title(STAGES, 1) === "Доп. кнопки", `${file}: этап приложений`);
    assert(title(STAGES, 3) === "Доп. кнопки", `${file}: этап действий`);
    assert(title(STAGES, 2) === null, `${file}: флешка — без подсказки`);
    assert(title(STAGES, -1) === null, `${file}: вступление`);
    const english = STAGES.map((s) => (s.type === "actions" ? { ...s, actions: [{ label: "Collect a detailed log" }] } : s));
    assert(title(english, 1) === "Доп. кнопки", `${file}: английская подпись кнопки`);
    const without = STAGES.map((s) => (s.type === "actions" ? { ...s, actions: [{ label: "Выдать разрешения" }] } : s));
    assert(title(without, 1) === null, `${file}: у модели нет кнопки`);
  }
};
