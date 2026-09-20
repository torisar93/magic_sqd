#!/usr/bin/env node
// Запуск всех JS-тестов (tests/js/*.test.js) последовательно: `node tests/js/run_all.js`.
// Каждый тест экспортирует async-функцию и бросает Error при провале. Нужен только node (без npm-пакетов).
"use strict";
const fs = require("fs");
const path = require("path");

(async () => {
  const dir = __dirname;
  const files = fs.readdirSync(dir).filter((f) => f.endsWith(".test.js")).sort();
  let failed = 0;
  for (const file of files) {
    try {
      await require(path.join(dir, file))();
      console.log("ok   " + file);
    } catch (error) {
      failed++;
      console.log("FAIL " + file + "\n     " + (error && error.stack ? error.stack.split("\n").slice(0, 3).join("\n     ") : error));
    }
  }
  console.log(`${files.length - failed}/${files.length} passed`);
  process.exit(failed ? 1 : 0);
})();
