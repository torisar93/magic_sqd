"""Какие APK общей библиотеки (папка apk/) скачала сама программа на этом устройстве.

Владелец, 14.10.2026: каталог APK для работы без интернета — только подписчикам и только скачанный программой;
«если я сам руками в папку запихну каталог — чтобы он не работал». Файлы не шифруются (это чужие приложения), но
программа помнит, что скачала сама: после скачивания файла apk/… в реестр (apk_registry.json рядом с папкой apk/)
пишутся его путь, размер и номер файла на диске (inode; на дисках без него — время изменения) с подписью ключом этой
установки (catalog_key: секрет сборки + случайный секрет установки). Файл без записи — положенный вручную,
скопированный с другого компьютера вместе с чужим реестром, подменённый — программа скачанным не считает: в списке
приложений он «на сервере», перед установкой перекачивается, а не скачался — уходит в apk/_unverified/ и не ставится.

Без ключа (запуск из исходников) реестр не действует — всё как раньше.
Файл общий для ПК (app/apk_registry.py) и Android (android/app/src/main/python/apk_registry.py) — побайтно."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import threading
from pathlib import Path, PurePosixPath

try:
    from . import catalog_key  # ПК: модуль пакета app
except ImportError:  # pragma: no cover
    import catalog_key  # Android: плоский модуль Chaquopy

REGISTRY_NAME = "apk_registry.json"
QUARANTINE_DIR = "_unverified"  # категории с «_» списки приложений пропускают
_KEY_LABEL = b"msqd-apk-registry-v1"
_lock = threading.Lock()  # кэш прочитанного реестра
_write_lock = threading.Lock()  # чтение-изменение-запись реестра целиком
_cache: dict = {"path": None, "mtime": None, "data": None}


def enabled() -> bool:
    return catalog_key.subkey(_KEY_LABEL) is not None


def _registry_path(apk_dir) -> Path:
    return Path(apk_dir).parent / REGISTRY_NAME


def _rel(rel) -> str:
    return PurePosixPath(str(rel).replace("\\", "/").strip("/")).as_posix()


def _identity(st) -> int:
    """Номер файла на диске: копия — новый файл с новым номером. Нет номера (FAT) — время изменения."""
    return int(getattr(st, "st_ino", 0) or 0) or int(st.st_mtime_ns)


def _signature(key: bytes, rel: str, size: int, identity: int) -> str:
    return hmac.new(key, f"{rel}\n{size}\n{identity}".encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def _load(apk_dir) -> dict:
    path = _registry_path(apk_dir)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {}
    with _lock:
        if _cache["path"] == str(path) and _cache["mtime"] == mtime and _cache["data"] is not None:
            return _cache["data"]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    files = data.get("files") if isinstance(data, dict) else None
    files = files if isinstance(files, dict) else {}
    with _lock:
        _cache.update(path=str(path), mtime=mtime, data=files)
    return files


def _save(apk_dir, files: dict) -> None:
    path = _registry_path(apk_dir)
    tmp = path.with_name(path.name + ".part")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps({"files": files}, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        return  # не записать — файл просто перекачается в следующий раз
    with _lock:
        _cache.update(path=None, mtime=None, data=None)


def record(apk_dir, rel, path=None) -> None:
    """Программа скачала (или администратор добавил) apk/<rel> — запомнить."""
    key = catalog_key.subkey(_KEY_LABEL)
    if key is None:
        return
    rel = _rel(rel)
    target = Path(path) if path is not None else Path(apk_dir) / rel
    try:
        st = target.stat()
    except OSError:
        return
    with _write_lock:
        files = dict(_load(apk_dir))
        files[rel] = {"size": st.st_size, "id": _identity(st), "sig": _signature(key, rel, st.st_size, _identity(st))}
        _save(apk_dir, files)


def forget(apk_dir, rel) -> None:
    with _write_lock:
        files = dict(_load(apk_dir))
        if files.pop(_rel(rel), None) is not None:
            _save(apk_dir, files)


def is_trusted(apk_dir, rel, path=None) -> bool:
    """Файл apk/<rel> скачан программой на этом устройстве и с тех пор не подменён. Без ключа — всегда да."""
    key = catalog_key.subkey(_KEY_LABEL)
    if key is None:
        return True
    rel = _rel(rel)
    entry = _load(apk_dir).get(rel)
    if not isinstance(entry, dict):
        return False
    target = Path(path) if path is not None else Path(apk_dir) / rel
    try:
        st = target.stat()
    except OSError:
        return False
    if entry.get("size") != st.st_size or entry.get("id") != _identity(st):
        return False
    return hmac.compare_digest(str(entry.get("sig") or ""), _signature(key, rel, st.st_size, _identity(st)))


def quarantine(apk_dir, rel, path=None) -> Path | None:
    """Положенный не программой файл, который не удалось перекачать, — в apk/_unverified/<rel>: ставить его нельзя,
    но и молча удалять чужой файл не будем. Возвращает новый путь (или None, если файла нет)."""
    rel = _rel(rel)
    source = Path(path) if path is not None else Path(apk_dir) / rel
    if not source.exists():
        return None
    target = Path(apk_dir) / QUARANTINE_DIR / rel
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.unlink()
        shutil.move(str(source), str(target))
    except OSError:
        return None
    forget(apk_dir, rel)
    return target


def apk_dir_and_rel(dest, remote_path: str):
    """Для download_file: файл apk/<rel> → (папка apk/, rel); остальное — None."""
    remote = _rel(remote_path)
    if not remote.startswith("apk/"):
        return None
    parts = PurePosixPath(remote).parts
    dest = Path(dest)
    if len(parts) < 2 or len(dest.parents) < len(parts) - 1:
        return None
    return dest.parents[len(parts) - 2], remote[len("apk/"):]
