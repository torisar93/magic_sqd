"""«Скачать заранее» — функция подписчиков Boosty (владелец, 2026-10-05): все файлы модели заранее на устройство,
чтобы у машины работать без интернета. Обычно файлы этапа качаются прямо перед ним (см. content_sync), а у машины
связи часто нет — Wi-Fi телефона занят магнитолой, на парковке не ловит.

Пакет модели — то, что её этапы берут с сервера: files/ и usb_files/ самой модели (APK этапа «Приложения», файлы
флешки, прошивки, инструкции с картинками, сертификат переподписи) и общие наборы cars/_shared/<имя>, на которые
ссылаются её этапы записи флешки. Приложения из общей библиотеки apk/ сюда не входят: их техник выбирает сам у каждой
машины, а вся библиотека — гигабайты; однажды скачанные остаются на диске и так.

Отметка MARKER в папке модели: список файлов пакета и нужно ли обновить (stale) — по ней значок в списке моделей
виден и без интернета. Пересчитывается по свежему манифесту при каждой синхронизации каталога (refresh_all).

Один и тот же файл на ПК (app/offline_pack.py) и Android (android/app/src/main/python/offline_pack.py) — тест
tests/test_offline_pack.py сверяет копии. Здесь только расчёты и диск; скачивание у каждой платформы своё."""
from __future__ import annotations

import json
import time
from pathlib import Path

MARKER = "_offline.json"
_OWN_SUBFOLDERS = ("files", "usb_files")
_SHARED_KEYS = ("usb_shared_folder", "shared_folder")


def model_rel(cars_dir: Path, model_dir: Path) -> str | None:
    """«cars/Марка/Модель[/Модификация]» — путь модели в манифесте сервера."""
    try:
        rel = Path(model_dir).resolve().relative_to(Path(cars_dir).resolve()).as_posix()
    except ValueError:
        return None
    return f"cars/{rel}" if rel and rel != "." else None


def _safe_folder_name(name) -> bool:
    return (isinstance(name, str) and bool(name.strip()) and "/" not in name and "\\" not in name
            and name not in (".", "..") and not name.startswith("."))


def shared_folders(model_dir: Path) -> list[str]:
    """Общие наборы cars/_shared/<имя>, которые пишут на флешку этапы модели: usb_shared_folder у этапа «Флешка»
    (и у его вариантов), shared_folder у блоков записи этапа из блоков — где бы в спеке ни встретились."""
    try:
        spec = json.loads((Path(model_dir) / "_wizard_spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in _SHARED_KEYS and _safe_folder_name(value):
                    if value.strip() not in found:
                        found.append(value.strip())
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(spec.get("steps") if isinstance(spec, dict) else None)
    return found


def pack_items(manifest: dict, cars_dir: Path, model_dir: Path) -> list[dict]:
    """Файлы пакета из манифеста: [{"path": путь от content/, "size", "mtime"}]."""
    rel = model_rel(cars_dir, model_dir)
    if rel is None or not manifest:
        return []
    prefixes = tuple(f"{rel}/{sub}/" for sub in _OWN_SUBFOLDERS) + tuple(
        f"cars/_shared/{name}/" for name in shared_folders(model_dir))
    items = []
    for path, entry in manifest.items():
        if not path.startswith(prefixes) or path.endswith(".part") or not isinstance(entry, dict):
            continue
        items.append({"path": path, "size": int(entry.get("size") or 0), "mtime": entry.get("mtime")})
    items.sort(key=lambda item: item["path"])
    return items


def local_path(cars_dir: Path, remote_path: str) -> Path:
    """Путь манифеста «cars/…» на диске: cars/ лежит в той же папке, что и на сервере (content/)."""
    return Path(cars_dir).parent / remote_path


def is_current(path: Path, item: dict) -> bool:
    """Та же версия, что на сервере: размер и время изменения (время сервера ставится при скачивании). Скачан позже
    последней правки на сервере — тоже та же (как content_sync.local_copy_is_current: старые закачки без времени)."""
    try:
        st = path.stat()
    except OSError:
        return False
    if st.st_size != item.get("size", -1):
        return False
    remote_mtime = item.get("mtime")
    return remote_mtime is None or abs(st.st_mtime - remote_mtime) <= 2 or st.st_mtime > remote_mtime


def plan(manifest: dict, cars_dir: Path, model_dir: Path) -> dict:
    """Что в пакете и чего на диске нет (или там старая версия)."""
    items = pack_items(manifest, cars_dir, model_dir)
    missing = [item for item in items if not is_current(local_path(cars_dir, item["path"]), item)]
    return {"items": items, "missing": missing,
            "total_bytes": sum(item["size"] for item in items),
            "missing_bytes": sum(item["size"] for item in missing)}


def read_marker(model_dir: Path) -> dict | None:
    try:
        data = json.loads((Path(model_dir) / MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def marker_state(model_dir: Path) -> str | None:
    """Для значка в списке моделей: "done" — скачана, "update" — на сервере новые файлы, None — не скачивали."""
    marker = read_marker(model_dir)
    if marker is None:
        return None
    return "update" if marker.get("stale") else "done"


def write_marker(model_dir: Path, current_plan: dict) -> str:
    missing = current_plan["missing"]
    data = {
        "saved_at": time.time(),
        "files": [item["path"] for item in current_plan["items"]],
        "shared": shared_folders(model_dir),
        "bytes": current_plan["total_bytes"],
        "stale": bool(missing),
        "update_bytes": current_plan["missing_bytes"],
    }
    target = Path(model_dir) / MARKER
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    tmp.replace(target)
    return "update" if missing else "done"


def refresh_marker(manifest: dict | None, cars_dir: Path, model_dir: Path) -> str | None:
    """Сверка скачанной заранее модели со свежим манифестом. Без сети — как было."""
    if read_marker(model_dir) is None:
        return None
    if manifest is None:
        return marker_state(model_dir)
    return write_marker(model_dir, plan(manifest, cars_dir, model_dir))


def offline_model_dirs(cars_dir: Path) -> list[Path]:
    cars_dir = Path(cars_dir)
    if not cars_dir.is_dir():
        return []
    return sorted(marker.parent for marker in cars_dir.rglob(MARKER)
                  if "_shared" not in marker.relative_to(cars_dir).parts)


def refresh_all(manifest: dict | None, cars_dir: Path, log=lambda m: None) -> None:
    """После синхронизации каталога: у скачанных заранее моделей — «скачано» или «обновить» по новому манифесту."""
    if manifest is None:
        return
    for model_dir in offline_model_dirs(cars_dir):
        try:
            refresh_marker(manifest, cars_dir, model_dir)
        except OSError as exc:
            log(f"Не удалось сверить скачанную заранее модель {model_dir.name}: {exc}")


def status(manifest: dict | None, cars_dir: Path, model_dir: Path) -> dict:
    """Для кнопки «Скачать заранее»: state none|done|update; total_bytes — весь пакет, missing_bytes — сколько
    докачать; online — есть ли манифест (без него сказать о новых файлах нельзя)."""
    marker = read_marker(model_dir)
    if manifest is None:
        if marker is None:
            return {"state": "none", "online": False, "total_bytes": 0, "missing_bytes": 0}
        return {"state": "update" if marker.get("stale") else "done", "online": False,
                "total_bytes": int(marker.get("bytes") or 0), "missing_bytes": int(marker.get("update_bytes") or 0)}
    current = plan(manifest, cars_dir, model_dir)
    state = write_marker(model_dir, current) if marker is not None else "none"
    return {"state": state, "online": True, "files": len(current["items"]),
            "total_bytes": current["total_bytes"], "missing_bytes": current["missing_bytes"]}


def _own_prefixes(cars_dir: Path, model_dir: Path) -> tuple[str, ...]:
    rel = model_rel(cars_dir, model_dir)
    return tuple(f"{rel}/{sub}/" for sub in _OWN_SUBFOLDERS) if rel else ()


def delete_targets(cars_dir: Path, model_dir: Path) -> list[Path]:
    """Что удалить по «Удалить скачанные файлы»: файлы пакета из отметки, что лежат на диске (рядом с APK — и его
    переподписанная копия X_resigned.apk), общие наборы — только если они не нужны другой скачанной заранее модели."""
    marker = read_marker(model_dir) or {}
    own = _own_prefixes(cars_dir, model_dir)
    kept_shared: set[str] = set()
    for other in offline_model_dirs(cars_dir):
        if other.resolve() != Path(model_dir).resolve():
            kept_shared.update((read_marker(other) or {}).get("shared") or [])
    targets: list[Path] = []
    for remote in marker.get("files") or []:
        if not isinstance(remote, str) or ".." in remote.split("/"):
            continue
        if remote.startswith("cars/_shared/"):
            name = remote.split("/")[2] if remote.count("/") >= 3 else ""
            if not name or name in kept_shared:
                continue
        elif not remote.startswith(own):
            continue
        path = local_path(cars_dir, remote)
        if path.is_file():
            targets.append(path)
        if path.suffix.lower() == ".apk":
            resigned = path.with_name(path.stem + "_resigned.apk")
            if resigned.is_file():
                targets.append(resigned)
    return targets


def delete_size(cars_dir: Path, model_dir: Path) -> int:
    total = 0
    for path in delete_targets(cars_dir, model_dir):
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def _prune_empty_dirs(start: Path, stop_at: Path) -> None:
    current = start
    stop_at = stop_at.resolve()
    while current.resolve() != stop_at and stop_at in current.resolve().parents:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def delete(cars_dir: Path, model_dir: Path) -> int:
    """Убирает скачанное заранее и отметку. Инструкции и прочее нужное снова скачаются при открытии модели, как у
    обычной. Возвращает, сколько байт освобождено."""
    cars_dir = Path(cars_dir)
    freed = 0
    for path in delete_targets(cars_dir, model_dir):
        try:
            size = path.stat().st_size
            path.unlink()
        except OSError:
            continue
        freed += size
        _prune_empty_dirs(path.parent, cars_dir)
    (Path(model_dir) / MARKER).unlink(missing_ok=True)
    return freed
