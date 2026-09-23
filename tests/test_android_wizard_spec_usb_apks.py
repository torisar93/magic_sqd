"""android/app/src/main/python/wizard_spec.py — на реальной модели Belgee X70/
рест (2026-09-21) обнаружилось, что standard_apks/standard_apks_optional
usb-этапа никогда не резолвились на Android: load_wizard_spec их разбирал
только для step_type == "apps", для "usb" всегда оставлял пустыми списками —
техник видел только общую библиотеку apk/, а необязательный APK ИМЕННО этой
модели вообще не показывался (desktop это уже умел, app/web/api/install_api.py:
standard_apks не делает разницы по типу этапа вовсе). Модуль — чистый Python
(json/re/pathlib), без Chaquopy-специфики, поэтому тестируется как обычный."""
from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WIZARD_SPEC_PATH = ROOT / "android/app/src/main/python/wizard_spec.py"


@pytest.fixture(scope="module")
def wizard_spec():
    """Импортирует модуль по прямому пути файла — он лежит вне обычных
    Python-пакетов проекта (android/.../python/ — Chaquopy source root, не
    app/), поэтому обычный import не находит его без правки sys.path."""
    spec = importlib.util.spec_from_file_location("android_wizard_spec", WIZARD_SPEC_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_spec(model_dir: Path, steps: list[dict]) -> None:
    model_dir.mkdir(parents=True, exist_ok=True)
    (model_dir / "_wizard_spec.json").write_text(
        json.dumps({"wifi": False, "wifi_port": 5555, "steps": steps}, ensure_ascii=False), encoding="utf-8")


def _usb_step(**overrides) -> dict:
    step = {
        "type": "usb", "title": "Флешка", "usb_files": [], "usb_copy_selected_apks": True,
        "usb_apks_dest": "apk", "usb_shared_folder": "", "standard_apks": [], "standard_apks_optional": [],
        "variants": [],
    }
    step.update(overrides)
    return step


def test_usb_step_without_variants_resolves_standard_apks_optional(tmp_path, wizard_spec):
    model_dir = tmp_path / "model"
    _write_spec(model_dir, [_usb_step(standard_apks_optional=[
        {"filename": "Data_Belgee_2.3.apk", "name": "Фикс сброса даты/времени", "description": "..."},
    ])])

    result = wizard_spec.load_wizard_spec(model_dir)
    step = result["steps"][0]

    assert step["standard_apks"] == []
    assert step["standard_apks_optional"] == [{
        "path": str(model_dir / "files" / "usb_pack_1" / "optional" / "Data_Belgee_2.3.apk"),
        "name": "Фикс сброса даты/времени", "description": "...",
    }]


def test_usb_step_without_variants_resolves_standard_apks_required_too(tmp_path, wizard_spec):
    # «Обязательных» больше нет (2026-09-23) — старая запись не теряется, а
    # показывается обычной галочкой (путь по-прежнему в required/, где файл
    # лежит у ещё не перенесённой модели).
    model_dir = tmp_path / "model"
    _write_spec(model_dir, [_usb_step(standard_apks=["mandatory.apk"])])

    result = wizard_spec.load_wizard_spec(model_dir)
    step = result["steps"][0]

    assert step["standard_apks"] == []
    assert step["standard_apks_optional"] == [{
        "path": str(model_dir / "files" / "usb_pack_1" / "required" / "mandatory.apk"),
        "name": "", "description": "",
    }]


def _apps_step(**overrides) -> dict:
    step = {"type": "apps", "title": "Приложения", "standard_apks": [], "standard_apks_optional": [],
            "variants": [], "apps_connection": "wired"}
    step.update(overrides)
    return step


def test_apps_required_apks_become_regular_checkboxes(tmp_path, wizard_spec):
    model_dir = tmp_path / "model"
    _write_spec(model_dir, [_apps_step(
        standard_apks=[{"filename": "ginputbridge.apk", "name": "GInputBridge", "description": ""}],
        standard_apks_optional=["climator.apk"])])

    step = wizard_spec.load_wizard_spec(model_dir)["steps"][0]

    assert step["standard_apks"] == []
    assert [Path(e["path"]).name for e in step["standard_apks_optional"]] == ["ginputbridge.apk", "climator.apk"]
    assert step["standard_apks_optional"][0]["name"] == "GInputBridge"


def test_same_file_in_required_and_optional_is_listed_once(tmp_path, wizard_spec):
    model_dir = tmp_path / "model"
    _write_spec(model_dir, [_apps_step(standard_apks=["x.apk"], standard_apks_optional=["x.apk"])])

    step = wizard_spec.load_wizard_spec(model_dir)["steps"][0]

    assert [e["path"] for e in step["standard_apks_optional"]] == [
        str(model_dir / "files" / "pack" / "optional" / "x.apk")]


def test_variant_required_apks_become_regular_checkboxes(tmp_path, wizard_spec):
    model_dir = tmp_path / "model"
    _write_spec(model_dir, [_apps_step(variants=[
        {"name": "Full", "standard_apks": ["a.apk"], "standard_apks_optional": ["b.apk"]}])])

    variant = wizard_spec.load_wizard_spec(model_dir)["steps"][0]["variants"][0]

    assert variant["standard_apks"] == []
    assert [Path(e["path"]).name for e in variant["standard_apks_optional"]] == ["a.apk", "b.apk"]


def test_usb_step_old_bug_regression_empty_when_no_apks_configured(tmp_path, wizard_spec):
    """Модель без необязательных APK на usb-этапе — списки просто пустые,
    не бросает исключений (самый частый случай, большинство usb-моделей)."""
    model_dir = tmp_path / "model"
    _write_spec(model_dir, [_usb_step()])

    result = wizard_spec.load_wizard_spec(model_dir)
    step = result["steps"][0]

    assert step["standard_apks"] == []
    assert step["standard_apks_optional"] == []


def test_usb_step_with_variants_resolves_per_variant_apk_dir(tmp_path, wizard_spec):
    model_dir = tmp_path / "model"
    _write_spec(model_dir, [_usb_step(variants=[
        {"name": "Full", "usb_files": ["full.bin"], "standard_apks_optional": [
            {"filename": "full_extra.apk", "name": "Full extra", "description": ""},
        ]},
        {"name": "Lite", "usb_files": ["lite.bin"], "standard_apks_optional": []},
    ])])

    result = wizard_spec.load_wizard_spec(model_dir)
    variants = result["steps"][0]["variants"]
    full = next(v for v in variants if v["name"] == "Full")
    lite = next(v for v in variants if v["name"] == "Lite")

    assert full["standard_apks_optional"] == [{
        "path": str(model_dir / "files" / "usb_pack_1" / "Full" / "optional" / "full_extra.apk"),
        "name": "Full extra", "description": "",
    }]
    assert full["usb_files"] == [str(model_dir / "usb_files" / "step_1" / "Full" / "full.bin")]
    assert lite["standard_apks_optional"] == []


def test_apps_step_unaffected_by_the_usb_fix(tmp_path, wizard_spec):
    """Регресс-страховка — apps-этап резолвил standard_apks_optional и до
    этой правки, поведение не должно было измениться."""
    model_dir = tmp_path / "model"
    _write_spec(model_dir, [{
        "type": "apps", "title": "Приложения", "standard_apks": [], "standard_apks_optional": [
            {"filename": "extra.apk", "name": "Extra", "description": "d"},
        ], "variants": [],
    }])

    result = wizard_spec.load_wizard_spec(model_dir)
    step = result["steps"][0]

    assert step["standard_apks_optional"] == [{
        "path": str(model_dir / "files" / "pack" / "optional" / "extra.apk"),
        "name": "Extra", "description": "d",
    }]
