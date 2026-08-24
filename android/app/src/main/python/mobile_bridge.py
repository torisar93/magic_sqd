"""Тонкая склейка content_sync.py/scanner.py под WebBridge.kt — принимает
простые типы (строки), отдаёт JSON-строки (проще и надёжнее гонять через
Chaquopy, чем сложные dict/dataclass через границу Kotlin<->Python)."""
import json
import threading
from pathlib import Path
from urllib.parse import quote

from content_sync import (sync_scripts, sync_model_subfolder, sync_shared_folder, fetch_manifest,
                           prune_removed_models)
from scanner import scan_cars, model_status_color, rollup_status_color, _read_version
from wizard_spec import load_wizard_spec
from apk_library import list_apks as _list_apks, ensure_apks_downloaded as _ensure_apks_downloaded

# Прогресс текущей закачки (sync_cars/sync_payload) — пишется из фонового
# потока WebBridge.startSync/syncModelPayload, читается через get_sync_progress
# (см. WebBridge.kt "get_sync_progress"), которую JS опрашивает раз в ~300мс,
# пока идёт синхронизация (см. app.js: раньше во время скачивания был только
# статичный текст без индикации хода дела). Простой dict под лock вместо
# честного callback через границу Kotlin<->Python — синхронный call/return,
# как и все остальные методы моста, никакой новой машинерии.
_progress_lock = threading.Lock()
_progress = {"phase": "", "done": 0, "total": 0}


def _progress_cb(phase: str):
    def cb(done: int, total: int) -> None:
        with _progress_lock:
            _progress["phase"] = phase
            _progress["done"] = done
            _progress["total"] = total
    return cb


def get_sync_progress() -> str:
    with _progress_lock:
        return json.dumps(dict(_progress))


def sync_cars(cars_dir: str, base_url: str) -> str:
    lines = []
    cars_path = Path(cars_dir)
    manifest = fetch_manifest(base_url)
    downloaded = sync_scripts(base_url, cars_path, log=lambda m: lines.append(m),
                               on_progress=_progress_cb("cars"), manifest=manifest)
    # sync_scripts выше только докачивает — модели, переименованные/убранные
    # на сервере (см. desktop app/car_generator.py:update_car), сами собой
    # локально не пропадают, поэтому отдельно подчищаем их здесь (см.
    # content_sync.prune_removed_models — не трогает то, что техник только
    # что создал локально и ещё не опубликовал).
    prune_removed_models(cars_path.parent, cars_path, manifest, log=lambda m: lines.append(m))
    return json.dumps({"downloaded": downloaded, "log": lines})


def _model_to_dict(model) -> dict:
    return {
        "key": str(model.dir),
        "brand": model.brand,
        "name": model.name,
        "modification": model.modification,
        "display_label": model.display_label,
        "no_instruction": model.no_instruction,
        "has_wizard_spec": (model.dir / "_wizard_spec.json").exists(),
        "status": model.status,
        "status_color": model_status_color(model),
    }


def _group_to_dict(group) -> dict:
    leaf_dict = _model_to_dict(group.leaf) if group.leaf else None
    mod_dicts = [_model_to_dict(m) for m in group.modifications]
    colors = ([leaf_dict["status_color"]] if leaf_dict else []) + [m["status_color"] for m in mod_dicts]
    return {
        "name": group.name,
        "has_modifications": bool(group.modifications),
        "leaf": leaf_dict,
        "modifications": mod_dicts,
        "status_color": rollup_status_color(colors),
    }


def list_cars(cars_dir: str) -> str:
    brands_tree = scan_cars(Path(cars_dir))
    brands = []
    for brand, groups in brands_tree.items():
        group_dicts = [_group_to_dict(g) for g in groups]
        brands.append({
            "name": brand,
            "groups": group_dicts,
            "status_color": rollup_status_color([g["status_color"] for g in group_dicts]),
            # cars/<brand>/logo.png уже качается вместе со скриптами (см.
            # content_sync.sync_scripts — logo.png не внутри files/
            # usb_files, значит не попадает под skip_dirs), путь тут — то,
            # что фронтенд подставит в WebViewAssetLoader "/data/" handler
            # (см. MainActivity.kt), картинка просто не отрисуется, если
            # ещё не скачалась (img onerror скрывает).
            "logo": f"cars/{quote(brand)}/logo.png",
        })
    return json.dumps({"brands": brands})


def select_model(key: str, brand: str, name: str, modification: str = None) -> str:
    """key — полный путь модели (см. _model_to_dict:"key"). brand/name/
    modification фронтенд передаёт сам (уже знает их из list_cars() —
    ре-парсить обратно из пути не нужно, там необязательный третий
    уровень модификации, легко ошибиться)."""
    model_dir = Path(key)
    if not model_dir.is_dir():
        return json.dumps({"error": "модель не найдена (папка отсутствует)"})
    revision, changelog, status, updated_at = _read_version(model_dir)
    stages_script = model_dir / "stages.py"
    no_instruction = (model_dir / "no_instruction.txt").exists()
    display_label = f"{brand} / {name} — {modification}" if modification else f"{brand} / {name}"
    return json.dumps({
        "key": key,
        "brand": brand,
        "name": name,
        "modification": modification,
        "display_label": display_label,
        "no_instruction": no_instruction,
        "has_wizard_spec": (model_dir / "_wizard_spec.json").exists(),
        "has_stages_script": stages_script.exists(),
        "status": status,
    })


def sync_payload(cars_dir: str, base_url: str, model_key: str) -> str:
    """Точечно скачивает ТОЛЬКО то, что нужно сразу при открытии модели —
    свои files/instruction_N/ подпапки (текст+картинки инструкций), а не всю
    files/+usb_files модели разом, как было раньше (портовая копия desktop
    app/web/api/install_api.py: load_stages() — тот же переход на ленивую
    докачку по этапам). APK apps-этапа/файлы usb-этапа/прикреплённые к
    adb-actions файлы качаются точечно прямо перед запуском соответствующего
    этапа (см. apk_library.ensure_apks_downloaded и sync_shared_folder_for
    ниже, вызываются из WebBridge.kt: adbInstallApks/usbRunStage/
    adbRunStage) — здесь их разом больше не трогаем."""
    lines = []
    log = lambda m: lines.append(m)
    cars_path = Path(cars_dir)
    model_dir = Path(model_key)
    downloaded = 0

    manifest = fetch_manifest(base_url)
    if manifest is None:
        log("Не удалось получить manifest.json с сервера — работаем с тем, что уже скачано локально.")
        return json.dumps({"downloaded": 0, "log": lines})

    # Читаем _wizard_spec.json напрямую (не через load_wizard_spec — та сама
    # читает содержимое instruction.html, то есть требует, чтобы файл уже
    # был на диске; здесь наоборот, решаем, что докачать, ДО чтения).
    try:
        raw = json.loads((model_dir / "_wizard_spec.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {"steps": []}

    files_dir = model_dir / "files"
    for i, step_data in enumerate(raw.get("steps", []), start=1):
        if step_data.get("type") == "instruction":
            instr_dir = files_dir / f"instruction_{i}"
            downloaded += sync_model_subfolder(base_url, cars_path, instr_dir, log=log,
                                                on_progress=_progress_cb("model"), manifest=manifest)

    return json.dumps({"downloaded": downloaded, "log": lines})


def sync_shared_folder_for(cars_dir: str, base_url: str, folder_name: str) -> str:
    """cars/_shared/<folder_name>/ целиком (см. content_sync.sync_shared_folder)
    — вызывается прямо перед записью usb-этапа с usb_shared_folder (см.
    WebBridge.kt: usbRunStage), а не при открытии модели."""
    lines = []
    log = lambda m: lines.append(m)
    downloaded = sync_shared_folder(base_url, Path(cars_dir), folder_name, log=log,
                                     on_progress=_progress_cb("model"))
    return json.dumps({"downloaded": downloaded, "log": lines})


def load_install_stages(model_key: str, files_root: str = "") -> str:
    """key -> нормализованный список этапов из _wizard_spec.json (см.
    wizard_spec.load_wizard_spec) для мастера установки. Файлы, на которые
    ссылаются этапы, должны быть уже скачаны (см. sync_payload выше) —
    здесь просто разрешаются пути, существование не проверяется. files_root
    — context.filesDir с Kotlin-стороны, только для картинок в
    instruction.html (см. wizard_spec._rewrite_instruction_images)."""
    model_dir = Path(model_key)
    spec = load_wizard_spec(model_dir, Path(files_root) if files_root else None)
    if spec is None:
        return json.dumps({"unsupported": True})
    return json.dumps(spec)


def list_apks(apk_dir: str, base_url: str) -> str:
    """Общая библиотека приложений (apk/) — см. apk_library.list_apks.
    Дёшево (имена/размеры из manifest.json), сами .apk не качает."""
    return _list_apks(Path(apk_dir), base_url)


def ensure_apks_downloaded(apk_dir: str, cars_dir: str, base_url: str, paths_json: str) -> str:
    """Докачивает то, чего ещё нет на диске, из paths_json — общую
    библиотеку (apk/) И "свои" файлы конкретной модели (files/pack*/...,
    files/adb_N/..., files/actions_i_j/...) теперь одинаково — см.
    apk_library.ensure_apks_downloaded. Вызывается перед adb_run_stage/
    adb_install_apks/usb_run_stage, для любых путей, которые этап реально
    использует. paths_json — JSON-массив абсолютных локальных путей (как и
    весь остальной мост, строка, не нативный список — см. WebBridge.kt)."""
    lines = []
    paths = json.loads(paths_json)
    downloaded = _ensure_apks_downloaded(Path(apk_dir), Path(cars_dir), base_url, paths,
                                          log=lambda m: lines.append(m))
    return json.dumps({"downloaded": downloaded, "log": lines})
