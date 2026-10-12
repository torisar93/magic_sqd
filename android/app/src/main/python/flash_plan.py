"""План записи на флешку: какие файлы скачать и куда положить (владелец, 12.10.2026: «делай вынос» — такие ошибки,
как папки комплекта, которые Android писал файлами (логи №244, №529, №617, №5420), чинятся правкой этого файла, без
выпуска программы).

Один и тот же файл в трёх местах: cars/_shared/flash_plan.py (с сервера, с подписью — scripts/publish_shared.py),
app/flash_plan.py (встроен в программу ПК) и android/app/src/main/python/flash_plan.py (встроен в Android). Программа
берёт серверный, если подпись сошлась и PLAN_API тот же, иначе встроенный (flash_writer.make_plan). В программе — только
простые операции: скачать файл, записать файл по пути на флешке (недостающие папки создаются, файл на месте нужной папки
убирается), удалить файл на флешке.

Только стандартная библиотека. Ничего не пишет и не скачивает — только смотрит на файлы и манифест сервера.
"""
from __future__ import annotations

import bisect
import os
from pathlib import Path, PurePosixPath

PLAN_API = 1  # программа берёт серверный план только с тем же номером
# Файлы-триггеры магнитолы: запись, в которой есть один из них, убирает с флешки остальные — иначе магнитола сработает
# на триггер прошлого шага (Haval Jolion 2026, владелец 24.09; было в app/usb_context.py и UsbFlashWrite.kt).
TRIGGER_FLAGS = ("svengmode.flag", "svlog.flag")
# Метка «папка скачана не вся» (Android 1.1.6, apk_library.py) — служебная, на флешку не идёт.
SERVICE_NAMES = (".msqd_incomplete",)


def plan(request: dict) -> dict:
    """request:
        {"roots": {"cars": "<папка cars>", "apk": "<папка apk>"},
         "manifest": {"cars/…": {"size": int, "mtime": float}, …} или None (без сети — только то, что на диске),
         "files": ["<путь>", …],          — каждый под своим именем в корень флешки (файл или папка целиком),
         "selected_apks": ["<путь>", …],  — отмеченные техником приложения,
         "apks_dest": "<папка на флешке>" — куда их («» — в корень),
         "shared": "<папка>" или None}     — общий набор cars/_shared/<имя>: содержимое в корень, со структурой.
    Ответ: {"api": 1,
            "files": [{"local": путь на диске, "remote": путь на сервере или None, "dest": путь на флешке, "size": байт}],
            "local_cleanup": [пути на диске, которые убрать до скачивания — папка, сохранённая файлом],
            "remove_on_flash": [имена файлов в корне флешки, которые убрать до записи]}."""
    roots = request.get("roots") or {}
    manifest = request.get("manifest") or None
    keys = sorted(manifest) if manifest else []  # один раз: файлы папки — диапазоном (в манифесте тысячи путей)
    files: list[dict] = []
    cleanup: list[str] = []

    def add(local: str, dest_dir: str, whole: bool) -> None:
        source = Path(local)
        remote = _remote(source, roots)
        kind, entries, stale = _expand(local, remote, manifest, keys)
        if stale:
            cleanup.append(local)
        for rel, path, remote_path, size in entries:
            parts = [dest_dir]
            if kind == "file" or whole:
                parts.append(source.name)
            parts.append(rel)
            files.append({"local": path, "remote": remote_path, "dest": _join(*parts), "size": size})

    for local in request.get("files") or []:
        add(str(local), "", whole=True)
    for local in request.get("selected_apks") or []:
        add(str(local), str(request.get("apks_dest") or ""), whole=True)
    if request.get("shared"):
        add(str(request["shared"]), "", whole=False)

    root_names = {PurePosixPath(item["dest"]).name.lower() for item in files if "/" not in item["dest"]}
    remove = [name for name in TRIGGER_FLAGS if root_names.intersection(TRIGGER_FLAGS) and name not in root_names]
    return {"api": PLAN_API, "files": files, "local_cleanup": cleanup, "remove_on_flash": remove}


def _remote(local: Path, roots: dict) -> str | None:
    """Путь того же файла на сервере: apk/… или cars/… (как content_sync.ensure_apks_downloaded)."""
    for prefix in ("apk", "cars"):
        root = roots.get(prefix)
        if not root:
            continue
        try:
            rel = local.resolve().relative_to(Path(root).resolve())
        except (ValueError, OSError):
            continue
        return f"{prefix}/{rel.as_posix()}" if rel.parts else prefix
    return None


def _expand(local: str, remote: str | None, manifest: dict | None, keys: list):
    """→ (вид, [(путь внутри, путь на диске, путь на сервере, размер)], прошлый «файл» на месте папки).

    Папка — если на сервере под этим путём есть файлы (сама папка в манифесте не значится) или она есть на диске.
    Раньше Android качал папку как файл: на адрес папки сервер отдаёт её список (autoindex json) — такой «файл» на
    диске убираем (local_cleanup) и качаем её файлы."""
    path = Path(local)
    if manifest and remote and remote in manifest and not path.is_dir():
        return "file", [("", local, remote, _size(path, manifest.get(remote)))], False
    found: dict[str, tuple] = {}
    if manifest and remote:
        prefix = remote.rstrip("/") + "/"
        for key in keys[bisect.bisect_left(keys, prefix):]:
            if not key.startswith(prefix):
                break
            rel = key[len(prefix):]
            if _safe(rel):
                inner = os.path.join(local, *rel.split("/"))
                found[rel] = (inner, key, _size(Path(inner), manifest.get(key)))
    if path.is_dir():
        for top, dirs, names in os.walk(path):
            dirs.sort()
            for name in sorted(names):
                if name in SERVICE_NAMES:
                    continue
                inner = os.path.join(top, name)
                rel = Path(inner).relative_to(path).as_posix()
                found.setdefault(rel, (inner, f"{remote}/{rel}" if remote else None, _size(Path(inner), None)))
    if found or path.is_dir():
        return "folder", [(rel,) + found[rel] for rel in sorted(found)], path.is_file()
    return "file", [("", local, remote, _size(path, None))], False


def _size(path: Path, entry) -> int:
    try:
        return path.stat().st_size if path.is_file() else int((entry or {}).get("size") or 0)
    except (OSError, TypeError, ValueError):
        return 0


def _safe(rel: str) -> bool:
    return bool(rel) and not rel.startswith("/") and ".." not in rel.split("/") and "\\" not in rel


def _join(*parts: str) -> str:
    return "/".join(part.strip("/") for part in parts if part and part.strip("/"))
