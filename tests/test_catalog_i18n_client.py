"""Перевод каталога на английский — сторона программы (с 1.1.4; переводит сервер: server/catalog_i18n.py).
Инструкция — instruction.en.html той же версии (метка с хешем русского текста), тексты моделей и библиотеки —
словари _i18n/en.json для переводчика интерфейса; на диске зашифрованы, как сам каталог (catalog_key)."""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from app import car_generator as cg
from app import catalog_crypto, catalog_i18n, catalog_key
from app.web.api.install_api import InstallApi

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"
RU = "<!DOCTYPE html>\n<html lang=\"ru\"><body><h1>Инструкция</h1><p>Подключите флешку.</p></body></html>\n"


def english_for(source: str, sha: str | None = None) -> str:
    sha = sha or catalog_i18n.source_hash(source)
    return ("<!DOCTYPE html>\n<html lang=\"en\"><body><h1>Instructions</h1><p>Connect the flash drive.</p></body>"
            f"</html>\n<!-- magicsqd-i18n lang=en source-sha1={sha} complete=1 -->\n")


def test_copies_are_identical_and_hash_matches_server():
    assert (ROOT / "app/catalog_i18n.py").read_bytes() == (ANDROID_PY / "catalog_i18n.py").read_bytes()
    # Та же строка и тот же хеш — в server/tests/test_catalog_i18n.py (сервер считает метку так же).
    assert catalog_i18n.source_hash("Привет\r\nмир") == "854b313dbb8589e1bb275b306ad8313418090b3f"


def test_translation_is_used_only_for_the_same_version(tmp_path):
    path = tmp_path / "instruction.html"
    path.write_text(RU, encoding="utf-8")
    assert catalog_i18n.read_instruction(path, "en") == RU  # перевода ещё нет
    (tmp_path / "instruction.en.html").write_text(english_for(RU), encoding="utf-8")
    assert "Connect the flash drive." in catalog_i18n.read_instruction(path, "en")
    assert catalog_i18n.read_instruction(path, "ru") == RU
    # Инструкцию поправили — старый перевод не показываем, пока сервер не переведёт новую.
    path.write_text(RU.replace("флешку", "флешку в USB-порт"), encoding="utf-8")
    assert "USB-порт" in catalog_i18n.read_instruction(path, "en")
    (tmp_path / "instruction.en.html").write_text("<p>без метки</p>", encoding="utf-8")
    assert "USB-порт" in catalog_i18n.read_instruction(path, "en")


def test_encrypted_files_are_read(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog_key, "_key", catalog_crypto.derive_key(b"s" * 32, b"r" * 32))
    path = tmp_path / "instruction.html"
    path.write_bytes(catalog_key.encrypt_bytes(RU.encode("utf-8")))
    (tmp_path / "instruction.en.html").write_bytes(catalog_key.encrypt_bytes(english_for(RU).encode("utf-8")))
    assert "Connect the flash drive." in catalog_i18n.read_instruction(path, "en")
    cars = tmp_path / "cars" / "Haval" / "H3" / "_i18n"
    cars.mkdir(parents=True)
    (cars / "en.json").write_bytes(catalog_key.encrypt_bytes(
        json.dumps({"strings": {"Доступ к ADB": "ADB access"}}, ensure_ascii=False).encode("utf-8")))
    assert catalog_i18n.load_strings(tmp_path / "cars", tmp_path / "apk") == {"Доступ к ADB": "ADB access"}


def test_load_strings_merges_models_and_library(tmp_path):
    def put(path, strings):
        path.mkdir(parents=True, exist_ok=True)
        (path / "en.json").write_text(json.dumps({"version": 1, "strings": strings}, ensure_ascii=False), encoding="utf-8")

    put(tmp_path / "cars/Geely/Atlas New/Обычная/_i18n", {"Обычная": "Standard", "Выбор приложений": "Choose apps"})
    put(tmp_path / "cars/Geely/Atlas New/Обычная/_closed/_i18n", {"Настройка ГУ": "Head unit setup"})
    put(tmp_path / "apk/_i18n", {"Утилиты": "Utilities", "пусто": ""})
    put(tmp_path / "cars/Haval/H3/files/instruction_1/_i18n", {"Чужое": "Not a dictionary"})  # не словарь модели
    (tmp_path / "cars/Haval/H3/_i18n").mkdir(parents=True)
    (tmp_path / "cars/Haval/H3/_i18n/en.json").write_text("{битый", encoding="utf-8")
    assert catalog_i18n.load_strings(tmp_path / "cars", tmp_path / "apk") == {
        "Обычная": "Standard", "Выбор приложений": "Choose apps", "Настройка ГУ": "Head unit setup",
        "Утилиты": "Utilities"}
    assert catalog_i18n.load_strings(tmp_path / "нет", tmp_path / "нет") == {}


def test_desktop_flash_block_instruction_in_english(tmp_path):
    folder = tmp_path / "files" / "flash_main"
    folder.mkdir(parents=True)
    (folder / "instruction.html").write_text(RU, encoding="utf-8")
    (folder / "instruction.en.html").write_text(english_for(RU), encoding="utf-8")
    model = type("Model", (), {"dir": tmp_path})()
    block = {"kind": "instruction", "title": "Шаги", "instruction": "files/flash_main/instruction.html"}
    assert "Connect the flash drive." in InstallApi._flash_block_to_dict(model, block, "en")["instruction_html"]
    assert "Подключите флешку." in InstallApi._flash_block_to_dict(model, block)["instruction_html"]


@pytest.fixture(scope="module")
def android_wizard_spec():
    sys.path.insert(0, str(ANDROID_PY))
    spec = importlib.util.spec_from_file_location("android_wizard_spec_i18n", ANDROID_PY / "wizard_spec.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(ANDROID_PY))
    return module


def test_android_instruction_stage_in_english(tmp_path, android_wizard_spec):
    cars = tmp_path / "files_root" / "cars"
    (cars / "_shared").mkdir(parents=True)
    shutil.copy(ROOT / "cars/_shared/load_sibling.py", cars / "_shared/load_sibling.py")
    step = cg.StepSpec(type="instruction", title="Доступ к ADB", instruction_blocks=[
        {"type": "p", "text": "Подключите флешку."}])
    model_dir = cg.create_car(cars, cg.NewCarSpec(brand="Haval", model="H3", steps=[step]))
    html_path = model_dir / "files" / "instruction_1" / "instruction.html"
    source = html_path.read_text(encoding="utf-8")
    (html_path.parent / "instruction.en.html").write_text(english_for(source), encoding="utf-8")
    english = android_wizard_spec.load_wizard_spec(model_dir, None, "en")["steps"][0]["instruction_html"]
    russian = android_wizard_spec.load_wizard_spec(model_dir, None)["steps"][0]["instruction_html"]
    assert "Connect the flash drive." in english and "Подключите флешку." in russian
