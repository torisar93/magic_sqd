"""Тихая синхронизация с собственным сервером (см. server/README.md) —
замена старой синхронизации с публичной папкой Я.Диска. Адрес настраивается
один раз через server.json (см. content_config.py), дальше программа сама
решает, что и когда скачивать:
    - скрипты/инструкции моделей (cars/) — автоматически при каждом
      запуске программы, сами APK при этом не трогаются;
    - файлы конкретной модели (files/, usb_files/) — прямо перед
      установкой этой модели (то есть по факту запроса пользователя).

Сервер отдаёт содержимое папки через nginx `autoindex_format json;`
(см. server/nginx_magicsqd.conf) — вместо API конкретного облака тут
достаточно обычного рекурсивного обхода JSON-листингов директорий.

Тяжёлые payload'ы (APK, прошивки) НЕ подтягиваются автоматически при
запуске — только по явному действию пользователя (кнопка "Скачать файлы
модели"/"Скачать всё" в gui.py) или непосредственно перед использованием
конкретного файла (см. model_needs_download/sync_model_files/
sync_shared_apks/ensure_apks_downloaded). Исключение — список (не сами
файлы) общей библиотеки apk/: его лёгкий обход (list_shared_apk_catalog)
тоже идёт при каждом запуске, как и cars/-скрипты, — иначе дерево выбора
приложений было бы пустым, пока пользователь не нажмёт "Скачать" сам."""
import json
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from .content_config import get_base_url

CHUNK_SIZE = 1024 * 1024


class ContentSyncError(RuntimeError):
    pass


def _encode_path(path: str) -> str:
    return "/".join(quote(part) for part in path.split("/") if part)


def _get_json(url: str) -> list[dict]:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ContentSyncError(f"Сервер вернул ошибку {exc.code} для {url}") from exc
    except urllib.error.URLError as exc:
        raise ContentSyncError(f"Не удалось обратиться к серверу: {exc.reason}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ContentSyncError(f"Сервер вернул не-JSON для {url}: {exc}") from exc


def list_files_recursive(base_url: str, subpath: str = "", skip_dirs: tuple = ()) -> list[dict]:
    """Плоский список всех файлов (без папок), рекурсивно по всем подпапкам.
    skip_dirs — имена подпапок, в которые не заходим вовсе (не просто
    фильтруем результат — экономим отдельный запрос листинга на каждую).
    Каждый элемент: {"path": "<путь от корня content/>", "size": int}."""
    files: list[dict] = []
    _walk(base_url, subpath.strip("/"), files, skip_dirs)
    return files


def _walk(base_url: str, rel_path: str, files: list[dict], skip_dirs: tuple) -> None:
    url = f"{base_url}/{_encode_path(rel_path)}/" if rel_path else f"{base_url}/"
    for entry in _get_json(url):
        name = entry.get("name", "")
        if not name:
            continue
        if entry.get("type") == "directory" and name in skip_dirs:
            continue
        child_rel = f"{rel_path}/{name}" if rel_path else name
        if entry.get("type") == "directory":
            _walk(base_url, child_rel, files, skip_dirs)
        else:
            files.append({"path": child_rel, "size": entry.get("size", -1)})


def download_file(base_url: str, remote_path: str, dest: Path,
                   log=lambda m: None, chunk_size: int = CHUNK_SIZE,
                   check_cancelled=lambda: None) -> None:
    """Скачивает один файл в dest (атомарно — через .part)."""
    url = f"{base_url}/{_encode_path(remote_path)}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(tmp_dest, "wb") as f:
            while True:
                check_cancelled()
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        tmp_dest.unlink(missing_ok=True)
        raise ContentSyncError(f"Ошибка скачивания {remote_path}: {exc}") from exc
    except BaseException:
        tmp_dest.unlink(missing_ok=True)
        raise
    tmp_dest.replace(dest)


def sync_tree(base_url: str, remote_subpath: str, local_dir: Path,
              log=lambda m: None, check_cancelled=lambda: None, skip_dirs: tuple = ()) -> int:
    """Скачивает из remote_subpath в local_dir всё, чего там ещё нет (или
    что отличается по размеру). skip_dirs — см. list_files_recursive.
    Возвращает число скачанных файлов; сетевые ошибки не бросает наружу —
    только логирует, чтобы недоступный сервер не мешал работе программы."""
    remote_subpath = remote_subpath.strip("/")
    try:
        items = list_files_recursive(base_url, remote_subpath, skip_dirs=skip_dirs)
    except ContentSyncError as exc:
        log(f"Не удалось получить список файлов с сервера ({remote_subpath}): {exc}")
        return 0

    downloaded = 0
    for item in items:
        check_cancelled()
        rel = item["path"][len(remote_subpath):].lstrip("/") if remote_subpath else item["path"]
        if not rel:
            continue
        local_path = local_dir / rel
        if local_path.exists() and local_path.stat().st_size == item.get("size", -1):
            continue
        try:
            download_file(base_url, item["path"], local_path, log=log,
                          check_cancelled=check_cancelled)
            downloaded += 1
        except ContentSyncError as exc:
            log(f"Не удалось скачать {item['path']}: {exc}")
    return downloaded


def sync_scripts(base_dir: Path, cars_dir: Path, log=lambda m: None) -> int:
    """Автообновление скриптов/инструкций всех моделей (cars/) при
    запуске программы — install.py/stages.py/instruction.html и т.п., но
    БЕЗ содержимого files/ и usb_files/ (там как раз и лежат тяжёлые
    payload'ы — APK, прошивки — их только по кнопке "Скачать", см.
    sync_model_files/sync_shared_apks). Молча ничего не делает, если
    server.json не настроен."""
    url = get_base_url(base_dir)
    if not url:
        return 0
    return sync_tree(url, "cars", cars_dir, log=log, skip_dirs=("files", "usb_files"))


def sync_model_files(base_dir: Path, model, log=lambda m: None, check_cancelled=lambda: None) -> int:
    """Подтягивает files/ и usb_files/ конкретной модели с сервера — по
    кнопке "Скачать файлы модели" (gui.py) и на всякий случай ещё раз прямо
    перед установкой этой модели. Молча ничего не делает (возвращает 0),
    если server.json не настроен."""
    url = get_base_url(base_dir)
    if not url:
        return 0
    # Путь строим из model.dir (а не brand+name), чтобы одинаково работать
    # и для обычных моделей (cars/<Марка>/<Модель>/), и для модификаций
    # (cars/<Марка>/<Модель>/<Модификация>/, см. scanner.py:ModelGroup) —
    # реальная глубина папки модели на диске тут единственный источник
    # истины, brand+name её не определяют однозначно.
    remote_base = "cars/" + model.dir.relative_to(base_dir / "cars").as_posix()
    downloaded = 0
    for subfolder in ("files", "usb_files"):
        local_dir = model.dir / subfolder
        downloaded += sync_tree(url, f"{remote_base}/{subfolder}", local_dir, log=log,
                                 check_cancelled=check_cancelled)
    return downloaded


def sync_shared_apks(base_dir: Path, apk_dir: Path, log=lambda m: None,
                      check_cancelled=lambda: None) -> int:
    """Подтягивает общую библиотеку apk/ (категории/файлы) с сервера — по
    кнопке "Скачать" (gui.py). Молча ничего не делает, если server.json не
    настроен."""
    url = get_base_url(base_dir)
    if not url:
        return 0
    return sync_tree(url, "apk", apk_dir, log=log, check_cancelled=check_cancelled)


def list_shared_apk_catalog(base_dir: Path) -> list[dict]:
    """Список файлов общей библиотеки apk/ на сервере — ИМЕНА/РАЗМЕРЫ,
    без скачивания (лёгкий обход через autoindex_format json, см.
    list_files_recursive). Вызывается при каждом запуске (см. gui.py), в
    отличие от sync_shared_apks (полное скачивание — только по кнопке или
    непосредственно перед установкой конкретных выбранных APK, см.
    ensure_apks_downloaded) — иначе дерево выбора приложений было бы пустым
    для ещё не скачанных APK. Каждый элемент — {"rel_path": "<путь
    относительно apk/, например 'Категория/файл.apk'>", "size": int}.
    Молча возвращает [] при сетевой ошибке или ненастроенном server.json —
    отсутствие удалённого каталога не должно ломать запуск."""
    url = get_base_url(base_dir)
    if not url:
        return []
    try:
        items = list_files_recursive(url, "apk")
    except ContentSyncError:
        return []
    catalog = []
    for item in items:
        rel = item["path"][len("apk"):].lstrip("/")
        if rel:
            catalog.append({"rel_path": rel, "size": item.get("size", -1)})
    return catalog


def ensure_apks_downloaded(base_dir: Path, apk_dir: Path, paths, log=lambda m: None,
                            check_cancelled=lambda: None) -> int:
    """Докачивает из paths (обычно ctx.selected_apks) только те файлы,
    которых ещё нет локально, — по одному, а не всю библиотеку apk/ разом
    (см. list_shared_apk_catalog/scanner.scan_apks: ApkInfo.remote_only).
    Вызывается прямо перед фактическим использованием — из runner.py и
    usb_dialog.py, рядом с уже существующим sync_model_files. Пути вне
    apk_dir (например, "стандартный набор" конкретного этапа — свои
    файлы в files/pack, уже подтянутые sync_model_files) тихо пропускает.
    Молча ничего не делает, если server.json не настроен."""
    url = get_base_url(base_dir)
    if not url:
        return 0
    apk_dir = apk_dir.resolve()
    downloaded = 0
    for raw_path in paths:
        check_cancelled()
        path = Path(raw_path)
        if path.exists():
            continue
        try:
            rel = path.resolve().relative_to(apk_dir)
        except ValueError:
            continue
        remote_path = f"apk/{rel.as_posix()}"
        try:
            download_file(url, remote_path, path, log=log, check_cancelled=check_cancelled)
            downloaded += 1
        except ContentSyncError as exc:
            log(f"Не удалось скачать {rel.as_posix()}: {exc}")
    return downloaded


def model_needs_download(model) -> bool:
    """Локальная эвристика без обращения к серверу (чтобы не дёргать сеть
    при каждом выборе модели в списке): похоже ли, что тяжёлые файлы этой
    модели ещё не скачаны. Проверяем не просто "папка существует" — у
    каждой модели уже есть files/pack/README.txt-заглушка даже без единого
    реального APK (см. cars/*/files/pack/README.txt) — а есть ли там
    что-то настоящее. Не отличает "всё скачано" от "на сервере появилось
    новое" — точная проверка требует сетевого запроса, кнопка просто
    перезапускает sync и в худшем случае скачивает 0 файлов."""
    pack_root = model.dir / "files"
    has_apks = pack_root.exists() and any(pack_root.rglob("*.apk"))

    usb_root = model.dir / "usb_files"
    has_usb_files = usb_root.exists() and any(
        p for p in usb_root.rglob("*") if p.is_file() and p.name.lower() != "readme.txt"
    )
    return not has_apks and not has_usb_files
