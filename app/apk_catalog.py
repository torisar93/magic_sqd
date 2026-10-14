"""Каталог APK целиком — для работы без интернета (владелец, 14.10.2026): кнопка «Каталог APK» рядом со «Скачать
заранее», только подписчикам Boosty; скачал — кнопка пропадает, каталог на сервере обновился — «Обновить каталог APK».

В каталог входят все APK общей библиотеки (apk/, кроме служебных папок на «_») и их описания (<файл>.json).
Скачанным считается только то, что скачала сама программа (apk_registry): положенный вручную каталог не засчитывается.
Отметка apk_catalog.json рядом с папкой apk/ — каталог однажды скачан целиком: недостающее потом — «обновить».

Файл общий для ПК (app/apk_catalog.py) и Android (android/app/src/main/python/apk_catalog.py) — побайтно."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

try:
    from . import apk_registry  # ПК: модуль пакета app
    from .content_sync import local_copy_is_current
except ImportError:  # pragma: no cover
    import apk_registry  # Android: плоские модули Chaquopy
    from content_sync import local_copy_is_current

MARKER_NAME = "apk_catalog.json"


def _marker(apk_dir) -> Path:
    return Path(apk_dir).parent / MARKER_NAME


def items(manifest) -> dict:
    """Файлы каталога из манифеста сервера: apk/… → {size, mtime}."""
    result = {}
    for path, item in (manifest or {}).items():
        if not path.startswith("apk/") or not isinstance(item, dict):
            continue
        parts = path[len("apk/"):].split("/")
        if any(part.startswith(("_", ".")) for part in parts) or not parts[-1].endswith((".apk", ".json")):
            continue
        result[path] = item
    return result


def _have(apk_dir: Path, rel: str, item: dict) -> bool:
    local = apk_dir / rel
    if not local.is_file() or not local_copy_is_current(local, item):
        return False
    return not rel.endswith(".apk") or apk_registry.is_trusted(apk_dir, rel, local)


def plan(manifest, apk_dir) -> dict:
    """{"files", "total_bytes", "missing": [{path, size, mtime, untrusted}], "missing_bytes"} — чего не хватает."""
    apk_dir = Path(apk_dir)
    missing, total = [], 0
    catalog = items(manifest)
    for path, item in sorted(catalog.items()):
        size = int(item.get("size") or 0)
        total += size
        rel = path[len("apk/"):]
        if not _have(apk_dir, rel, item):
            local = apk_dir / rel
            missing.append({"path": path, "size": size, "mtime": item.get("mtime"),
                            "untrusted": rel.endswith(".apk") and local.is_file()
                            and not apk_registry.is_trusted(apk_dir, rel, local)})
    return {"files": len(catalog), "total_bytes": total, "missing": missing,
            "missing_bytes": sum(entry["size"] for entry in missing)}


def downloaded_once(apk_dir) -> bool:
    return _marker(apk_dir).is_file()


def status(manifest, apk_dir) -> dict:
    """Состояние кнопки: none — не скачан (показать «Каталог APK · размер»), update — скачивали, на сервере новое
    («Обновить каталог APK · размер»), done — всё на месте (кнопки нет). Без сети — по отметке: скачан — done,
    иначе кнопки нет (скачать всё равно нельзя)."""
    if manifest is None:
        state = "done" if downloaded_once(apk_dir) else "none"
        return {"state": state, "available": False, "files": 0, "total_bytes": 0, "missing_bytes": 0}
    current = plan(manifest, apk_dir)
    if not current["missing"]:
        state = "done"
    else:
        state = "update" if downloaded_once(apk_dir) else "none"
    return {"state": state, "available": current["files"] > 0, "files": current["files"],
            "total_bytes": current["total_bytes"], "missing_bytes": current["missing_bytes"]}


def prepare(apk_dir, current: dict) -> None:
    """Перед скачиванием: файлы, положенные не программой, — в apk/_unverified/ (их заменят копии с сервера)."""
    for entry in current["missing"]:
        if entry.get("untrusted"):
            apk_registry.quarantine(apk_dir, entry["path"][len("apk/"):])


def write_marker(apk_dir) -> None:
    path = _marker(apk_dir)
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_text(json.dumps({"done_at": int(time.time())}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def forget(apk_dir) -> None:
    """Каталог стёрт («Очистить кэш» удаляет apk/) — отметки «скачивали» больше нет."""
    try:
        _marker(apk_dir).unlink()
    except OSError:
        pass
