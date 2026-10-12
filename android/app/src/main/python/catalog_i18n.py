"""Перевод содержимого каталога — сторона программы (с 1.1.4; переводит сервер: server/catalog_i18n.py).

У английского интерфейса (js/i18n.js):
- инструкция этапа — instruction.en.html из той же папки, если перевод сделан с этой же версии русской инструкции
  (метка «magicsqd-i18n … source-sha1=…» в конце файла); правили инструкцию — до нового перевода русская;
- тексты моделей и библиотеки APK — словари <модель>/_i18n/en.json и apk/_i18n/en.json: программа отдаёт их
  переводчику интерфейса (load_strings), тот подставляет на экране.
Файлы приходят обычной синхронизацией каталога; инструкции и тексты моделей на диске зашифрованы (catalog_key).

Файл общий для ПК (app/catalog_i18n.py) и Android (android/app/src/main/python/catalog_i18n.py) — побайтно."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

try:
    from . import catalog_key  # ПК: модуль пакета app
except ImportError:  # Android (Chaquopy): модули верхнего уровня
    import catalog_key

DICT_DIR = "_i18n"
MARKER_RE = re.compile(r"<!-- magicsqd-i18n lang=(\w+) source-sha1=([0-9a-f]{40}) complete=([01]) -->")


def source_hash(text: str) -> str:
    """sha1 русского текста (переводы строк приведены к \\n) — та же формула у сервера."""
    return hashlib.sha1(str(text).replace("\r\n", "\n").encode("utf-8")).hexdigest()


def read_instruction(path, language: str = "ru", errors: str = "replace") -> str:
    """Текст инструкции на языке интерфейса: перевод той же версии, если он есть, иначе русский."""
    path = Path(path)
    text = catalog_key.read_text(path, errors=errors)
    if language in ("", "ru"):
        return text
    translated = path.with_name(f"{path.stem}.{language}{path.suffix}")
    try:
        candidate = catalog_key.read_text(translated, errors=errors)
    except (OSError, RuntimeError, ValueError):
        return text
    mark = MARKER_RE.search(candidate)
    if not mark or mark.group(1) != language or mark.group(2) != source_hash(text):
        return text
    return candidate


def load_strings(cars_dir, apk_dir, language: str = "en") -> dict:
    """{русский: перевод} из словарей всех скачанных моделей и библиотеки APK — для переводчика интерфейса."""
    strings: dict = {}
    name = f"{language}.json"
    paths = []
    cars_dir = Path(cars_dir)
    if cars_dir.is_dir():
        for dirpath, dirnames, filenames in os.walk(cars_dir):
            dirnames[:] = [d for d in dirnames if d not in ("files", "usb_files", "__pycache__") and not d.startswith(".")]
            if Path(dirpath).name == DICT_DIR and name in filenames:
                paths.append(Path(dirpath) / name)
    library = Path(apk_dir) / DICT_DIR / name
    if library.is_file():
        paths.append(library)
    for path in paths:
        try:
            data = json.loads(catalog_key.read_text(path))
        except (OSError, RuntimeError, ValueError):
            continue  # битый или недокачанный словарь — без этих переводов
        items = data.get("strings") if isinstance(data, dict) else None
        if isinstance(items, dict):
            strings.update({k: v for k, v in items.items() if isinstance(k, str) and isinstance(v, str) and v.strip()})
    return strings
