// Общее для JS-тестов (node, без браузера): функции фронтенда вырезаются из исходников по маркерам и
// исполняются в vm с заглушками DOM/моста. Хрупко к переименованию функций-маркеров — тогда тест падает с
// понятным «marker not found», а не проходит зря.
"use strict";
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const ROOT = path.resolve(__dirname, "..", "..");

function read(rel) {
  return fs.readFileSync(path.join(ROOT, rel), "utf8");
}

// Вырезает [startMarker, endMarker) из исходника; endMarker ищется после startMarker.
function slice(src, startMarker, endMarker, file) {
  const a = src.indexOf(startMarker);
  if (a < 0) throw new Error(`marker not found in ${file}: ${JSON.stringify(startMarker)}`);
  const b = endMarker === null ? src.length : src.indexOf(endMarker, a);
  if (b < 0) throw new Error(`end marker not found in ${file}: ${JSON.stringify(endMarker)}`);
  return src.slice(a, b);
}

function assert(condition, message) {
  if (!condition) throw new Error("FAIL: " + message);
}

function run(code, context) {
  vm.createContext(context);
  vm.runInContext(code, context);
  return context;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

module.exports = { ROOT, read, slice, assert, run, sleep };
