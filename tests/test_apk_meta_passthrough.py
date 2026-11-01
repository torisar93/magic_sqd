"""Настройки приложения целиком — apk.meta (владелец 01.11.2026: «включить в этот выпуск»). Раньше программы отдавали
интерфейсу только известные им поля сайдкара (name, description, mock_location, hidden_models, exclusive_group), и
каждая новая настройка вроде «скрыть на модели» или «только одно» требовала выпуска. Теперь весь <файл>.json (у
приложения модели на Android — вся запись _wizard_spec.json) приходит как apk.meta, а редактор моделей переносит
незнакомые ему поля записи как есть: новую настройку добавляет админка или бандл интерфейса, без выпуска."""
from __future__ import annotations
import importlib.util
import json
import shutil
import sys
import types
from pathlib import Path

import pytest

from app import car_generator as cg
from app.scanner import scan_apk_dir_with_remote, scan_apks
from app.web.api.scanner_api import apk_to_dict

# car_editor_api импортирует pywebview (диалоги выбора файлов), которого нет в CI — как в test_editor_model_access.py.
_WEBVIEW_STUBBED = "webview" not in sys.modules
if _WEBVIEW_STUBBED:
    sys.modules["webview"] = types.ModuleType("webview")
from app.web.api import car_editor_api  # noqa: E402
if _WEBVIEW_STUBBED:
    del sys.modules["webview"]

REPO = Path(__file__).resolve().parents[1]
ANDROID_PY = REPO / "android/app/src/main/python"
FUTURE = {"future_flag": True, "future_list": ["a", "b"]}


def _sidecar(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_desktop_library_and_model_pack_pass_the_whole_sidecar(tmp_path):
    apk_dir = tmp_path / "apk"
    _sidecar(apk_dir / "Видео" / "remote.json", {"name": "Remote", **FUTURE})
    (apk_dir / "Видео" / "plain.apk").write_bytes(b"PK")  # без сайдкара
    library = {Path(a.path).name: apk_to_dict(a)
               for a in scan_apks(apk_dir, [{"rel_path": "Видео/remote.apk", "size": 3}])}
    assert library["remote.apk"]["meta"] == {"name": "Remote", **FUTURE}
    assert library["plain.apk"]["meta"] == {}

    folder = tmp_path / "cars/Geely/Monjaro/SE/files/pack/optional"
    _sidecar(folder / "app.json", {"name": "App", **FUTURE})
    (apk,) = scan_apk_dir_with_remote(folder, [{"path": "cars/Geely/Monjaro/SE/files/pack/optional/app.apk"}])
    assert apk_to_dict(apk)["meta"] == {"name": "App", **FUTURE}
    json.dumps(apk_to_dict(apk))  # уходит в окно как JSON


def test_desktop_broken_sidecar_gives_empty_meta(tmp_path):
    folder = tmp_path / "pack"
    folder.mkdir()
    (folder / "app.apk").write_bytes(b"PK")
    (folder / "app.json").write_bytes(b"\xff\xfe not json")
    (apk,) = scan_apk_dir_with_remote(folder, [])
    assert apk.meta == {} and apk.name == "app" and apk.exclusive_group == ""


@pytest.fixture
def android(monkeypatch):
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    modules = {}
    for name in ("offline_pack", "content_sync", "apk_library", "wizard_spec"):
        spec = importlib.util.spec_from_file_location(name, ANDROID_PY / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules[name] = module
    return modules


def test_android_library_and_model_pack_pass_the_whole_entry(tmp_path, android, monkeypatch):
    apk_library = android["apk_library"]
    apk_dir = tmp_path / "apk"
    (apk_dir / "Видео").mkdir(parents=True)
    (apk_dir / "Видео" / "app.apk").write_bytes(b"PK")
    _sidecar(apk_dir / "Видео" / "app.json", {"name": "App", **FUTURE})
    monkeypatch.setattr(apk_library, "fetch_manifest", lambda base_url: None)
    (item,) = json.loads(apk_library.list_apks(apk_dir, "http://127.0.0.1:9"))
    assert item["meta"] == {"name": "App", **FUTURE}

    base = Path("/cars/Geely/Monjaro/SE/files/pack/optional")
    entry = android["wizard_spec"]._apk_entry({"filename": "app.apk", "name": "App", **FUTURE}, base)
    assert entry["meta"] == {"filename": "app.apk", "name": "App", **FUTURE}
    assert android["wizard_spec"]._apk_entry("old.apk", base)["meta"] == {}


def test_editor_keeps_unknown_fields_in_spec_and_sidecar(tmp_path):
    cars = tmp_path / "cars"
    (cars / "_shared").mkdir(parents=True)
    shutil.copy(REPO / "cars/_shared/load_sibling.py", cars / "_shared/load_sibling.py")
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.apk").write_bytes(b"PK")
    spec = cg.NewCarSpec(brand="Geely", model="Monjaro", modification="SE", steps=[
        cg.StepSpec(type="apps", title="Приложения", standard_apks_optional=[
            cg.StandardApkSpec(path=src / "app.apk", name="App",
                               extra={**FUTURE, "name": "не перезапишет", "filename": "x.apk"}),
        ]),
    ])
    model_dir = cg.create_car(cars, spec)

    # Через словари редактора (load → окно → сохранение): поля, которых редактор не знает, не теряются.
    loaded = cg.load_car_spec(model_dir, "Geely", "Monjaro", "SE")
    dicts = [car_editor_api._apk_entry_to_dict(apk) for apk in loaded.steps[0].standard_apks_optional]
    dicts[0]["display_name"] = "App 2"
    loaded.steps[0].standard_apks_optional = [car_editor_api._apk_entry_from_dict(item) for item in dicts]
    cg.update_car(cars, model_dir, loaded)

    (entry,) = json.loads((model_dir / "_wizard_spec.json").read_text(encoding="utf-8"))["steps"][0][
        "standard_apks_optional"]
    assert entry == {"filename": "app.apk", "name": "App 2", "description": "", **FUTURE}
    sidecar = json.loads((model_dir / "files/pack/optional/app.json").read_text(encoding="utf-8"))
    assert sidecar == {"name": "App 2", "description": "", **FUTURE}
