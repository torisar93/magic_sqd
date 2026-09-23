// Этап «Флешка» из блоков: одинаковые тексты и отметки «сделано» на ПК
// (app/web/frontend/js/screens/stage_wizard.js) и Android (android/.../assets/js/app.js) —
// владелец: «должно быть всё одинаково на всех платформах». Инструкция одной строкой
// (без кнопки) считается пройденной, как только сделан любой блок после неё.
"use strict";
const { read, slice, assert, run } = require("./_util");

module.exports = async function () {
  const pcFile = "app/web/frontend/js/screens/stage_wizard.js";
  const pcSrc = read(pcFile);
  const pc = run(
    slice(pcSrc, "  function flashFilesDescription(block) {", "  // Та же полоса выбора флешки", pcFile)
    + slice(pcSrc, "  function flashBlockDone(blocks, state, k) {", "  function renderFlashBlocksStage(", pcFile)
    + "\nthis.describe = flashFilesDescription; this.done = flashBlockDone;", {});

  const androidFile = "android/app/src/main/assets/js/app.js";
  const androidSrc = read(androidFile);
  const android = run(
    slice(androidSrc, "  function flashFilesDescription(block) {", "  function syncFlashCardStates(stage) {", androidFile)
    + "\nthis.describe = flashFilesDescription; this.state = flashCardState;",
    { flashBlockResults: {}, basename: (p) => p.split("/").pop() });

  const cases = [
    [{ file_names: ["svengmode.flag"] }, { files: ["/m/usb_files/step_1/svengmode.flag"] }],
    [{ file_names: ["a.bin", "maps"], copy_selected_apks: true, shared_folder: "freetuga" },
      { files: ["/m/a.bin", "/m/maps"], copy_selected_apks: true, shared_folder: "freetuga" }],
    [{ file_names: ["1", "2", "3", "4"] }, { files: ["/1", "/2", "/3", "/4"] }],
    [{ file_names: [] }, { files: [] }],
  ];
  for (const [pcBlock, androidBlock] of cases) {
    const a = pc.describe(pcBlock), b = android.describe(androidBlock);
    assert(a === b, `описание блока записи одинаковое на ПК и Android: ${a} / ${b}`);
  }
  assert(pc.describe(cases[0][0]) === "На флешку будет записано: svengmode.flag.", pc.describe(cases[0][0]));
  assert(pc.describe(cases[2][0]) === "На флешку будет записано: файлы этапа (4).", pc.describe(cases[2][0]));

  // запись → инструкция одной строкой → запись → пароль
  const blocks = [{ kind: "write" }, { kind: "instruction" }, { kind: "write" }, { kind: "password" }];
  const pcDone = (done) => blocks.map((_, k) => pc.done(blocks, { done }, k));
  assert(JSON.stringify(pcDone({ 0: true })) === "[true,false,false,false]", "ПК: после первой записи строка ещё не пройдена");
  assert(JSON.stringify(pcDone({ 0: true, 2: true })) === "[true,true,true,false]", "ПК: вторая запись закрывает строку перед ней");

  const stage = { index: 5, flash_blocks: blocks };
  const androidStates = () => blocks.map((_, k) => android.state(stage, k)).join();
  android.flashBlockResults[5] = { 0: { ok: true } };
  assert(androidStates() === "done,active,idle,idle", "Android: активна строка после первой записи: " + androidStates());
  android.flashBlockResults[5] = { 0: { ok: true }, 2: { ok: false, error: "нет флешки" } };
  assert(androidStates() === "done,active,error,idle", "Android: ошибка записи видна: " + androidStates());
  android.flashBlockResults[5] = { 0: { ok: true }, 2: { ok: true } };
  assert(androidStates() === "done,done,done,active", "Android: вторая запись закрывает строку перед ней: " + androidStates());
};
