"""Общая библиотека приложений (apk/ в корне репозитория, порт
app/scanner.py:scan_apks + app/content_sync.py:list_shared_apk_catalog/
ensure_apks_downloaded) — приложения, не привязанные к конкретной модели,
которые техник может доустановить/докопировать по желанию поверх
model-specific standard_apks/standard_apks_optional (см. wizard_spec.py).
Список показывается сразу (дёшево — только имена/размеры из manifest.json),
сами .apk скачиваются только для того, что техник реально отметит
(см. ensure_apks_downloaded) — тот же принцип, что и на desktop."""
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from content_sync import ContentSyncError, _encode_path, download_file, fetch_manifest

_META_FETCH_WORKERS = 8


@dataclass
class ApkInfo:
    path: str
    name: str
    description: str = ""
    category: str = ""  # "" = "Без категории" (лежит прямо в apk/)
    remote_only: bool = False  # есть на сервере, но ещё не скачан локально
    size: int = -1


def _read_local_apk_meta(apk_path: Path):
    meta_path = apk_path.with_suffix(".json")
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return str(data.get("name") or apk_path.stem), str(data.get("description") or "")
    except (OSError, json.JSONDecodeError):
        return apk_path.stem, ""


def _scan_local_dir(dir_path: Path, category: str) -> list:
    if not dir_path.is_dir():
        return []
    items = []
    for f in sorted(dir_path.glob("*.apk")):
        name, description = _read_local_apk_meta(f)
        items.append(ApkInfo(path=str(f), name=name, description=description, category=category))
    return items


def _scan_local_apks(apk_dir: Path) -> list:
    items = _scan_local_dir(apk_dir, "")
    if apk_dir.is_dir():
        for sub in sorted(apk_dir.iterdir(), key=lambda p: p.name.lower()):
            if sub.is_dir() and not sub.name.startswith("_"):
                items.extend(_scan_local_dir(sub, sub.name))
    return items


def list_apks(apk_dir: Path, base_url: str) -> str:
    """Локальные + известные с сервера, но ещё не скачанные (remote_only)
    .apk из общей библиотеки — нормализованный JSON-список ApkInfo."""
    local = _scan_local_apks(apk_dir)
    local_paths = {a.path for a in local}
    result = [vars(a) for a in local]

    manifest = fetch_manifest(base_url)
    if manifest:
        remote_apks = {}
        remote_jsons = set()
        for path, entry_info in manifest.items():
            if not path.startswith("apk/"):
                continue
            rel = path[len("apk/"):]
            if rel.endswith(".apk"):
                remote_apks[rel] = entry_info["size"]
            elif rel.endswith(".json"):
                remote_jsons.add(rel)

        remote_entries = []
        meta_fetch_jobs = []
        for rel, size in remote_apks.items():
            local_path = apk_dir / rel
            if str(local_path) in local_paths:
                continue  # уже учтён среди local (скачан раньше)
            category = rel.split("/")[0] if "/" in rel else ""
            if category.startswith("_"):
                continue
            entry = {
                "path": str(local_path), "name": Path(rel).stem, "description": "",
                "category": category, "remote_only": True, "size": size,
            }
            remote_entries.append(entry)
            json_rel = rel[:-4] + ".json"
            if json_rel in remote_jsons:
                meta_fetch_jobs.append((json_rel, entry))

        if meta_fetch_jobs:
            def fetch_meta(job):
                json_rel, entry = job
                try:
                    url = f"{base_url}/{_encode_path('apk/' + json_rel)}"
                    with urllib.request.urlopen(url, timeout=15) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    entry["name"] = str(data.get("name") or entry["name"])
                    entry["description"] = str(data.get("description") or "")
                except (urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError):
                    pass

            with ThreadPoolExecutor(max_workers=_META_FETCH_WORKERS) as executor:
                list(executor.map(fetch_meta, meta_fetch_jobs))

        result.extend(remote_entries)

    result.sort(key=lambda a: (a["category"] != "", a["category"].lower(), a["name"].lower()))
    return json.dumps(result)


def ensure_apks_downloaded(apk_dir: Path, base_url: str, paths, log=lambda m: None) -> int:
    """Докачивает те .apk из paths (абсолютные локальные пути к общей
    библиотеке), которых ещё нет на диске — вызывается прямо перед
    исполнением apps/usb-этапа, использующего отмеченные техником
    приложения (см. WebBridge.kt: adbInstallApks/usbRunStage)."""
    downloaded = 0
    for p in paths:
        local_path = Path(p)
        if local_path.exists():
            continue
        try:
            rel = local_path.relative_to(apk_dir).as_posix()
        except ValueError:
            continue  # не из общей библиотеки (например model-specific apk, уже должен быть на диске)
        log(f"Скачиваю {local_path.name}...")
        try:
            download_file(base_url, f"apk/{rel}", local_path)
            downloaded += 1
        except ContentSyncError as exc:
            log(f"Не удалось скачать {local_path.name}: {exc}")
    return downloaded
