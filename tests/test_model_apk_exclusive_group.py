"""«Только одно из группы» у приложений самой модели, а не только у общей библиотеки apk/ (владелец, 2026-09-29):
MonGuard и Monji (Monjaro SE, VOLGA K50) — один пакет com.geely.gc.cloudautoclient, вместе не встанут. Раньше
группа работала только для библиотеки: техник отмечал оба, и установка останавливалась на защите от дубля
(лог #1663). Группа лежит в сайдкаре APK (его читает список выбора на ПК) и в _wizard_spec.json (Android и
редактор); пересохранение модели в редакторе её не теряет."""
from __future__ import annotations
import importlib.util
import json
import shutil
import sys
from pathlib import Path

from app import car_generator as cg
from app.scanner import scan_apk_dir_with_remote
from app.web.api import car_editor_api
from app.web.api.scanner_api import apk_to_dict

REPO = Path(__file__).resolve().parents[1]
GROUP = "MonGuard / Monji"


def make_monjaro(tmp_path):
    cars = tmp_path / "cars"
    (cars / "_shared").mkdir(parents=True)
    shutil.copy(REPO / "cars/_shared/load_sibling.py", cars / "_shared/load_sibling.py")
    src = tmp_path / "src"
    src.mkdir()
    for name in ("monguard_app.apk", "monji_monjaro.apk", "aimp.apk"):
        (src / name).write_bytes(name.encode())
    spec = cg.NewCarSpec(brand="Geely", model="Monjaro", modification="SE", steps=[
        cg.StepSpec(type="apps", title="Установка приложений", standard_apks_optional=[
            cg.StandardApkSpec(path=src / "monguard_app.apk", name="MonGuard", exclusive_group=GROUP),
            cg.StandardApkSpec(path=src / "monji_monjaro.apk", name="Monji", exclusive_group=GROUP),
            cg.StandardApkSpec(path=src / "aimp.apk", name="AIMP"),
        ]),
    ])
    return cars, cg.create_car(cars, spec)


def test_group_is_written_to_spec_and_sidecar(tmp_path):
    _cars, model_dir = make_monjaro(tmp_path)
    saved = json.loads((model_dir / "_wizard_spec.json").read_text(encoding="utf-8"))["steps"][0]
    by_file = {entry["filename"]: entry for entry in saved["standard_apks_optional"]}
    assert by_file["monguard_app.apk"]["exclusive_group"] == GROUP
    assert "exclusive_group" not in by_file["aimp.apk"]  # без группы спека как раньше
    optional = model_dir / "files/pack/optional"
    assert json.loads((optional / "monji_monjaro.json").read_text(encoding="utf-8")) == {
        "name": "Monji", "description": "", "exclusive_group": GROUP}
    assert json.loads((optional / "aimp.json").read_text(encoding="utf-8")) == {"name": "AIMP", "description": ""}


def test_editor_resave_keeps_the_group(tmp_path):
    """Редактор группу не показывает, но и не теряет: load_car_spec → словари редактора → update_car."""
    cars, model_dir = make_monjaro(tmp_path)
    spec = cg.load_car_spec(model_dir, "Geely", "Monjaro", "SE")
    dicts = [car_editor_api._apk_entry_to_dict(apk) for apk in spec.steps[0].standard_apks_optional]
    for item in dicts:
        item["display_name"] = item["display_name"] + " (правка)"  # техник поменял только подпись
    spec.steps[0].standard_apks_optional = [car_editor_api._apk_entry_from_dict(item) for item in dicts]

    cg.update_car(cars, model_dir, spec)

    saved = json.loads((model_dir / "_wizard_spec.json").read_text(encoding="utf-8"))["steps"][0]
    groups = {entry["filename"]: entry.get("exclusive_group") for entry in saved["standard_apks_optional"]}
    assert groups == {"monguard_app.apk": GROUP, "monji_monjaro.apk": GROUP, "aimp.apk": None}
    sidecar = json.loads((model_dir / "files/pack/optional/monguard_app.json").read_text(encoding="utf-8"))
    assert sidecar == {"name": "MonGuard (правка)", "description": "", "exclusive_group": GROUP}


def test_desktop_list_has_group_before_the_apk_is_downloaded(tmp_path):
    """Список выбора на ПК строится до скачивания APK (remote_only) — сайдкар уже подтянут, группа из него."""
    folder = tmp_path / "cars/Geely/Monjaro/SE/files/pack/optional"
    folder.mkdir(parents=True)
    (folder / "monguard_app.json").write_text(json.dumps({"name": "MonGuard", "exclusive_group": GROUP}),
                                              encoding="utf-8")
    remote = [{"path": "cars/Geely/Monjaro/SE/files/pack/optional/monguard_app.apk", "size": 10},
              {"path": "cars/Geely/Monjaro/SE/files/pack/optional/monguard_app.json", "size": 5}]

    (apk,) = scan_apk_dir_with_remote(folder, remote)

    assert apk.remote_only and apk.name == "MonGuard"
    assert apk_to_dict(apk)["exclusive_group"] == GROUP


def test_android_takes_the_group_from_the_spec():
    spec = importlib.util.spec_from_file_location(
        "android_wizard_spec_groups", REPO / "android/app/src/main/python/wizard_spec.py")
    wizard_spec = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = wizard_spec
    spec.loader.exec_module(wizard_spec)
    base = Path("/cars/Geely/Monjaro/SE/files/pack/optional")

    grouped = wizard_spec._apk_entry({"filename": "monji_monjaro.apk", "name": "Monji", "exclusive_group": GROUP}, base)
    plain = wizard_spec._apk_entry("aimp.apk", base)

    assert grouped["exclusive_group"] == GROUP and grouped["name"] == "Monji"
    assert "exclusive_group" not in plain  # без группы — как раньше


def test_both_platforms_apply_groups_to_model_apps_too():
    """Общая AppTabs.exclusiveGroups (apps_tabs.js, её поведение — tests/js/apk_exclusive_groups.test.js) получает
    и приложения модели, не только библиотеку."""
    desktop = (REPO / "app/web/frontend/js/screens/stage_wizard.js").read_text(encoding="utf-8")
    assert ("window.AppTabs.exclusiveGroups(tree, [...standard.required, ...standard.optional, ...shared]"
            in desktop)
    android = (REPO / "android/app/src/main/assets/js/app.js").read_text(encoding="utf-8")
    assert "window.AppTabs.exclusiveGroups(page, [...rawLists.required, ...rawLists.optional, ...apkLibrary]," in android
