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
from __future__ import annotations
import json
import os
import shutil
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

from .content_config import get_base_url

CHUNK_SIZE = 1024 * 1024

# Обход дерева cars/ на сервере — сколько параллельных запросов листинга
# папок держим одновременно (см. list_files_recursive). cars/ у нас всего
# 2 уровня вложенности (марка/модель), так что обход идёт волнами по
# уровням, а не рекурсивно по потокам — ограничение здесь просто чтобы не
# бомбардировать маленький VPS/nginx сотней одновременных соединений сразу.
_LISTING_WORKERS = 16

# Собственно скачивание файлов (см. sync_tree) — при первом запуске
# программы (пустой cars/) это сотни мелких файлов (stages.py/install.py/
# instruction.html на модель), каждый — отдельное HTTP-соединение. Раньше
# качались строго по одному — при round-trip до VPS в 100-200мс это и
# давало те самые 30-40 секунд первого запуска, хотя сам объём данных
# крошечный. Тот же пул, что и для листинга, по той же причине (не больше
# одновременных соединений, чем нужно, чтобы не забить маленький nginx).
_DOWNLOAD_WORKERS = 16


class ContentSyncError(RuntimeError):
    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code  # код HTTP-ответа, если ошибка из-за него (см. _get_json) — иначе None


def _encode_path(path: str) -> str:
    return "/".join(quote(part) for part in path.split("/") if part)


def _get_json(url: str) -> list[dict]:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ContentSyncError(f"Сервер вернул ошибку {exc.code} для {url}", code=exc.code) from exc
    except urllib.error.URLError as exc:
        raise ContentSyncError(f"Не удалось обратиться к серверу: {exc.reason}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ContentSyncError(f"Сервер вернул не-JSON для {url}: {exc}") from exc


def list_files_recursive(base_url: str, subpath: str = "", skip_dirs: tuple = (),
                          no_recurse_dirs: tuple = (), log=lambda m: None) -> list[dict]:
    """Плоский список всех файлов (без папок), рекурсивно по всем подпапкам.
    skip_dirs — имена подпапок, в которые не заходим вовсе, где бы они ни
    встретились (не просто фильтруем результат — экономим отдельный запрос
    листинга на каждую). no_recurse_dirs — точные пути (относительно
    subpath/корня), у которых листингуются только СВОИ файлы, но не их
    подпапки — в отличие от skip_dirs, сама эта папка не пропускается
    целиком, просто её подпапки не разворачиваются вглубь (см. sync_scripts:
    cars/_shared/ — лёгкие *.py-хелперы лежат прямо в ней, а тяжёлые
    payload-наборы техника — в её подпапках, см. usb_shared_folder).
    Каждый элемент: {"path": "<путь от корня content/>", "size": int}.

    Обход идёт ВОЛНАМИ по уровням вложенности (level-order), а не строго
    рекурсивно по одной папке за раз: все листинги очередного уровня (все
    марки разом, потом все модели разом и т.д.) запрашиваются параллельно
    через пул потоков (см. _LISTING_WORKERS) — при паре десятков марок и
    ~сотне моделей это ~100 листингов, и последовательно (старое поведение,
    по HTTP-запросу за раз) обход cars/ при старте программы становится
    заметно долгим по мере роста каталога моделей. cars/ у нас всего 2
    уровня вложенности (марка/модель), так что этот подход почти всегда
    укладывается в 2-3 волны запросов вместо ~100 запросов подряд.
    log получает по одной строке на папку ВЕРХНЕГО уровня (например каждую
    марку внутри cars/), как и раньше, просто теперь пачкой перед началом
    следующей волны, а не по одной непосредственно перед заходом в неё."""
    top = subpath.strip("/")
    top_depth = top.count("/") + 1 if top else 0
    files: list[dict] = []

    # Листинг САМОГО subpath — как и раньше, ошибка (в т.ч. 404 "такой папки
    # на сервере нет вовсе") пробрасывается вызывающему как есть (см.
    # sync_tree: различает 404 корня от прочих ошибок). Ошибки где-то ГЛУБЖЕ
    # дерева (одна подпапка недоступна) — уже не повод обрывать весь обход,
    # см. _list_one ниже, это не было принципиальной гарантией и раньше
    # такое поведение (одна плохая подпапка рушит весь sync_tree) было скорее
    # побочным эффектом рекурсии, чем осознанным решением.
    root_entries = _get_json(f"{base_url}/{_encode_path(top)}/" if top else f"{base_url}/")

    def _consume(rel_path: str, entries: list[dict], next_level: list[str]) -> None:
        no_recurse = rel_path in no_recurse_dirs
        for entry in entries:
            name = entry.get("name", "")
            if not name:
                continue
            child_rel = f"{rel_path}/{name}" if rel_path else name
            if entry.get("type") == "directory":
                if name in skip_dirs or no_recurse:
                    continue
                next_level.append(child_rel)
            else:
                files.append({"path": child_rel, "size": entry.get("size", -1)})

    level_dirs: list[str] = []
    _consume(top, root_entries, level_dirs)

    with ThreadPoolExecutor(max_workers=_LISTING_WORKERS) as executor:
        while level_dirs:
            if top_depth and level_dirs[0].count("/") + 1 == top_depth + 1:
                for rel_path in level_dirs:
                    log(f"Проверяю {rel_path}...")
            next_level: list[str] = []
            listings = executor.map(lambda rp: _list_one(base_url, rp), level_dirs)
            for rel_path, entries in zip(level_dirs, listings):
                if entries is None:
                    continue  # 404/сетевая ошибка на этой подпапке — просто пропускаем её
                _consume(rel_path, entries, next_level)
            level_dirs = next_level
    return files


def _list_one(base_url: str, rel_path: str) -> list[dict] | None:
    """Листинг одной папки — None при 404/сетевой ошибке (папки может не
    быть на сервере вовсе, это не повод рушить обход остального дерева,
    см. list_files_recursive/sync_tree: 404 самого корня — отдельный
    случай "такой папки нет", а 404 где-то в глубине дерева просто
    пропускаем, как и раньше делал бы вызывающий на верхнем уровне)."""
    url = f"{base_url}/{_encode_path(rel_path)}/" if rel_path else f"{base_url}/"
    try:
        return _get_json(url)
    except ContentSyncError:
        return None


def fetch_manifest(base_url: str) -> dict[str, dict] | None:
    """Скачивает единый манифест content/manifest.json (см. server/backend.py:
    write_manifest) — ОДИН HTTP-запрос вместо рекурсивного обхода директорий
    через nginx autoindex (см. list_files_recursive), который иначе
    приходится повторять для каждого вида синхронизации отдельно (cars-
    скрипты при старте, files/usb_files конкретной модели перед установкой,
    cars/_shared/<набор> перед USB-этапом, каталог apk/ и его *.json
    сайдкары) — на разросшемся дереве моделей это было десятки-сотни
    запросов на один запуск программы. Возвращает {"<путь от content/>":
    {"size": int, "mtime": float | None}} или None, если манифеста на
    сервере нет (старый бэкенд, ещё не обновлённый) или сеть недоступна —
    тогда вызывающий код откатывается на list_files_recursive для конкретно
    нужного ему поддерева, как раньше."""
    try:
        with urllib.request.urlopen(f"{base_url}/manifest.json", timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    files = data.get("files")
    if not isinstance(files, dict):
        return None
    result = {}
    for path, entry in files.items():
        if isinstance(entry, dict) and isinstance(entry.get("size"), int):
            result[path] = {"size": entry["size"], "mtime": entry.get("mtime")}
    return result


def filter_manifest(manifest: dict[str, dict], subpath: str, skip_dirs: tuple = (),
                     no_recurse_dirs: tuple = ()) -> list[dict]:
    """То же самое, что list_files_recursive(base_url, subpath, skip_dirs,
    no_recurse_dirs), но без единого сетевого запроса — берёт срез уже
    скачанного манифеста (см. fetch_manifest). skip_dirs — имя ЛЮБОГО
    компонента пути (кроме самого файла) исключает запись, где бы он ни
    встретился на пути к файлу. no_recurse_dirs — точные пути (от корня
    content/), у которых в результат попадают только файлы ПРЯМО в них, но
    не из более глубоких подпапок — семантика идентична list_files_recursive,
    манифест просто уже содержит всё дерево целиком, фильтрация локальная."""
    prefix = subpath.strip("/")
    result = []
    for path, entry in manifest.items():
        if prefix and path != prefix and not path.startswith(f"{prefix}/"):
            continue
        dir_segments = path.split("/")[:-1]
        if any(seg in skip_dirs for seg in dir_segments):
            continue
        if any(path.startswith(f"{nr}/") and "/" in path[len(nr) + 1:] for nr in no_recurse_dirs):
            continue
        result.append({"path": path, "size": entry["size"], "mtime": entry.get("mtime")})
    return result


def _is_stale(local_path: Path, item: dict) -> bool:
    """Файл нужно перекачать, если его нет локально, отличается размер,
    либо (при известном mtime с сервера) сохранённая при прошлой закачке
    mtime не совпадает с текущей mtime на сервере — раньше сверяли только
    size, из-за чего правка файла без изменения байтовой длины (например
    правка instruction.html той же длины) тихо не перекачивалась, см.
    download_file (штампует mtime сервера на скачанный файл)."""
    if not local_path.exists():
        return True
    st = local_path.stat()
    if st.st_size != item.get("size", -1):
        return True
    remote_mtime = item.get("mtime")
    if remote_mtime is not None and abs(st.st_mtime - remote_mtime) > 2:
        return True
    return False


def download_file(base_url: str, remote_path: str, dest: Path,
                   log=lambda m: None, chunk_size: int = CHUNK_SIZE,
                   check_cancelled=lambda: None, mtime: float | None = None,
                   on_progress=lambda done, total, *args: None) -> None:
    """Скачивает один файл в dest (атомарно — через .part)."""
    url = f"{base_url}/{_encode_path(remote_path)}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest.with_name(dest.name + ".part")
    try:
        with urllib.request.urlopen(url, timeout=60) as resp, open(tmp_dest, "wb") as f:
            try:
                total_bytes = int(resp.headers.get("Content-Length") or 0)
            except ValueError:
                total_bytes = 0
            received = 0
            last_report = 0.0
            if total_bytes:
                on_progress(0, total_bytes)
            while True:
                check_cancelled()
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
                now = time.monotonic()
                # Не засоряем очередь UI тысячами событий, но обновляем
                # длинный APK достаточно часто, чтобы процент был живым.
                if total_bytes and (received >= total_bytes or now - last_report >= 0.12):
                    on_progress(received, total_bytes)
                    last_report = now
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        tmp_dest.unlink(missing_ok=True)
        raise ContentSyncError(f"Ошибка скачивания {remote_path}: {exc}") from exc
    except BaseException:
        tmp_dest.unlink(missing_ok=True)
        raise
    tmp_dest.replace(dest)
    if mtime is not None:
        # Штампуем mtime сервера на локальный файл — это то, с чем следующий
        # sync сравнит manifest.json (см. _is_stale), а не время скачивания.
        try:
            os.utime(dest, (mtime, mtime))
        except OSError:
            pass


def _download_one(base_url: str, remote_path: str, local_path: Path,
                   log, check_cancelled, mtime: float | None = None,
                   on_progress=lambda done, total, *args: None) -> bool:
    """Один файл для параллельного скачивания в sync_tree — обёртка над
    download_file с логом и обработкой ошибки конкретно этого файла (не
    должна рушить скачивание остальных, см. вызов через ThreadPoolExecutor
    ниже). Раньше логировала "Скачиваю <файл>..." на КАЖДЫЙ файл — при
    первой синхронизации модели (сотни мелких файлов) это превращало лог в
    сплошной технический список; теперь ход дела показывает прогресс-бар
    (см. on_progress в sync_tree), а не текст построчно."""
    check_cancelled()
    try:
        download_file(base_url, remote_path, local_path, log=log, check_cancelled=check_cancelled,
                      mtime=mtime, on_progress=on_progress)
        return True
    except ContentSyncError as exc:
        log(f"Не удалось скачать {remote_path}: {exc}")
        return False


LOCAL_EDIT_MARKER_FILENAME = "_local_edit.json"


def mark_local_edit(model_dir: Path) -> None:
    """Ставится car_editor_api.py сразу после локального сохранения правки
    моделью, которая НЕ ушла напрямую на сервер (обычный техник — заявка
    уходит на модерацию, сама папка модели на сервере пока не тронута, см.
    _worker() в car_editor_api.py). Пока маркер стоит, sync_model_files/
    sync_scripts ниже не перекачивают файлы этой модели с сервера — иначе
    старая (домодерационная) версия с сервера тихо перезаписывала бы поверх
    только что сделанную правку при следующей же попытке установки или при
    следующем запуске программы (реальный баг: техник убрал APK из
    обязательных, сохранил, а этап установки продолжал его ставить — файл
    возвращался обратно из sync_model_files прямо перед стартом этапа)."""
    try:
        (model_dir / LOCAL_EDIT_MARKER_FILENAME).write_text(
            json.dumps({"saved_at": time.time()}), encoding="utf-8")
    except OSError:
        pass  # не смертельно — просто в редком случае старое содержимое может вернуться из sync


def clear_local_edit_marker(model_dir: Path) -> None:
    """Обратное к mark_local_edit — вызывается после успешной публикации
    (см. _worker(): ветка admin_base_url and admin_session_cookie), когда
    локальная копия и так только что стала совпадать с сервером."""
    try:
        (model_dir / LOCAL_EDIT_MARKER_FILENAME).unlink()
    except OSError:
        pass


def _has_local_edit(model_dir: Path) -> bool:
    return (model_dir / LOCAL_EDIT_MARKER_FILENAME).exists()


def _locally_edited_prefixes(cars_dir: Path) -> set[str]:
    """Все "cars/<Марка>/<Модель>[/<Модификация>]" с маркером локальной
    правки (см. mark_local_edit) — для sync_scripts ниже, который в отличие
    от sync_model_files обходит все модели разом одним sync_tree."""
    prefixes = set()
    for marker_path in cars_dir.rglob(LOCAL_EDIT_MARKER_FILENAME):
        model_dir = marker_path.parent
        prefixes.add("cars/" + model_dir.relative_to(cars_dir).as_posix())
    return prefixes


def sync_tree(base_url: str, remote_subpath: str, local_dir: Path,
              log=lambda m: None, check_cancelled=lambda: None, skip_dirs: tuple = (),
              no_recurse_dirs: tuple = (), manifest: dict[str, dict] | None = None,
              on_progress=lambda done, total, *args: None,
              skip_prefixes: tuple = ()) -> int:
    """Скачивает из remote_subpath в local_dir всё, чего там ещё нет (или
    что отличается по размеру). skip_dirs/no_recurse_dirs — см.
    list_files_recursive. Возвращает число скачанных файлов; сетевые ошибки
    не бросает наружу — только логирует, чтобы недоступный сервер не мешал
    работе программы. Исключение — 404 на листинге самой remote_subpath: это
    означает "такой папки на сервере просто нет" (например, техник в этапе
    ничего своего не добавлял, только общую библиотеку — см.
    _model_wants_own_files) и не более ошибка, чем пустая папка, поэтому не
    засоряет лог.

    Ход дела — через on_progress(done, total), а не построчным логом на
    каждый файл (раньше "Скачиваю <файл>..." на сотнях мелких файлов
    превращало лог в стену технического текста — см. UI-слой, который рисует
    по этому колбэку прогресс-бар, например app/web/api/install_api.py).
    on_progress(0, total) вызывается один раз перед стартом скачивания (если
    вообще есть что качать), дальше — после каждого завершённого файла.

    manifest — уже скачанный content/manifest.json (см. fetch_manifest),
    если он есть у вызывающего — тогда список получаем локальной фильтрацией
    (filter_manifest) вместо отдельного сетевого обхода remote_subpath.
    None (по умолчанию) — старое поведение, обходим сами через
    list_files_recursive (для старых серверов без манифеста и как фолбэк).

    skip_prefixes — полные "cars/<Марка>/<Модель>[/<Модификация>]" моделей с
    несогласованной с сервером локальной правкой (см. mark_local_edit) —
    их содержимое целиком пропускаем, а не только сверяем по размеру/mtime,
    иначе домодерационная версия с сервера тихо перезаписывала бы правку."""
    remote_subpath = remote_subpath.strip("/")
    if manifest is not None:
        items = filter_manifest(manifest, remote_subpath, skip_dirs=skip_dirs,
                                 no_recurse_dirs=no_recurse_dirs)
    else:
        log(f"Проверяю файлы на сервере ({remote_subpath})...")
        try:
            items = list_files_recursive(base_url, remote_subpath, skip_dirs=skip_dirs,
                                          no_recurse_dirs=no_recurse_dirs, log=log)
        except ContentSyncError as exc:
            if exc.code != 404:
                log(f"Не удалось получить список файлов с сервера ({remote_subpath}): {exc}")
            return 0

    if skip_prefixes:
        items = [item for item in items
                 if not any(item["path"] == p or item["path"].startswith(p + "/") for p in skip_prefixes)]

    to_download: list[tuple[str, Path, float | None, int]] = []
    for item in items:
        check_cancelled()
        rel = item["path"][len(remote_subpath):].lstrip("/") if remote_subpath else item["path"]
        if not rel:
            continue
        local_path = local_dir / rel
        if not _is_stale(local_path, item):
            continue
        to_download.append((item["path"], local_path, item.get("mtime"), item.get("size", 0)))

    downloaded = 0
    if to_download:
        total_files = len(to_download)
        total_bytes = sum(size for _, _, _, size in to_download)
        byte_progress = total_bytes > 0
        if byte_progress:
            # Каждый поток обновляет только свой счётчик; общий процент
            # складываем под lock, поэтому несколько параллельных APK не
            # заставляют индикатор прыгать назад.
            received = [0] * total_files
            progress_lock = threading.Lock()

            def report(index: int, done: int, _total: int) -> None:
                with progress_lock:
                    received[index] = min(done, to_download[index][3])
                    bytes_done = sum(received)
                    files_done = sum(
                        value >= to_download[i][3] for i, value in enumerate(received)
                    )
                on_progress(bytes_done, total_bytes, files_done, total_files)

            on_progress(0, total_bytes, 0, total_files)
        else:
            on_progress(0, total_files, 0, total_files)
        # Параллельно (см. _DOWNLOAD_WORKERS) — иначе первый запуск на
        # пустом cars/ качает сотни мелких файлов по одному, каждый со
        # своим round-trip до VPS. check_cancelled() внутри _download_one
        # при отмене бросает исключение в future — оно всплывёт наружу из
        # цикла ниже, как и раньше прерывая sync_tree.
        with ThreadPoolExecutor(max_workers=_DOWNLOAD_WORKERS) as executor:
            futures = [executor.submit(
                _download_one, base_url, path, local_path, log, check_cancelled, mtime,
                (lambda done, total, index=index: report(index, done, total)) if byte_progress else (lambda *_: None),
            ) for index, (path, local_path, mtime, _size) in enumerate(to_download)]
            # as_completed, а не порядок отправки — иначе один медленный файл
            # в начале списка держит done на месте, пока десятки уже готовых
            # за ним ждут своей очереди, и бар потом разом доскакивает до
            # конца вместо равномерного хода (см. отчёт пользователя: "на
            # половине долго стоит и потом резко доходит до конца").
            done = 0
            for future in as_completed(futures):
                done += 1
                if future.result():
                    downloaded += 1
                if not byte_progress:
                    on_progress(done, total_files, done, total_files)
    if downloaded:
        log(f"Скачано файлов ({remote_subpath}): {downloaded}.")
    return downloaded


def sync_scripts(base_dir: Path, cars_dir: Path, log=lambda m: None,
                  manifest: dict[str, dict] | None = None,
                  on_progress=lambda done, total, *args: None) -> int:
    """Автообновление скриптов/инструкций всех моделей (cars/) при
    запуске программы — install.py/stages.py/instruction.html и т.п., но
    БЕЗ содержимого files/ и usb_files/ (там как раз и лежат тяжёлые
    payload'ы — APK, прошивки — их только по кнопке "Скачать", см.
    sync_model_files/sync_shared_apks). cars/_shared/ — особый случай:
    сами *.py-хелперы (load_sibling.py и т.п.) лежат прямо в ней и нужны
    ВСЕГДА (stages.py каждой модели их импортирует), а вот ПОДПАПКИ внутри
    неё — это общие payload-наборы техника (см. StepSpec.usb_shared_folder,
    app/web/api/car_editor_api.py:save_shared_usb_files) произвольного
    размера и с произвольной структурой (не обязательно "files"/
    "usb_files" — сам техник называет папки как нужно его скрипту), поэтому
    их не ловит skip_dirs выше — используем no_recurse_dirs, чтобы не
    разворачивать их вглубь: они подтягиваются точечно прямо перед
    использованием конкретного USB-этапа (см. sync_shared_folder,
    app/web/api/usb_api.py). Молча ничего не делает, если server.json не
    настроен. Модели с несогласованной локальной правкой (см.
    mark_local_edit/_locally_edited_prefixes) пропускаются целиком — иначе
    домодерационная версия stages.py/_wizard_spec.json с сервера тихо
    затёрла бы правку уже при следующем запуске программы."""
    url = get_base_url(base_dir)
    if not url:
        return 0
    return sync_tree(url, "cars", cars_dir, log=log, skip_dirs=("files", "usb_files"),
                      no_recurse_dirs=("cars/_shared",), manifest=manifest, on_progress=on_progress,
                      skip_prefixes=tuple(_locally_edited_prefixes(cars_dir)))


_KNOWN_MODELS_FILENAME = "known_models.json"
_MODEL_MARKER = "_wizard_spec.json"  # см. car_generator.py: SPEC_FILENAME — не импортируем
# оттуда напрямую, чтобы content_sync.py оставался лёгким и порт-совместимым
# с android/app/src/main/python/content_sync.py (та же константа продублирована там).


def _model_prefixes_from_manifest(manifest: dict[str, dict]) -> set[str]:
    """Множество путей вида "cars/<Марка>/<Модель>[/<Модификация>]" — по
    одному на каждую реальную модель в манифесте. Узнаём модель по наличию
    _MODEL_MARKER — единственного файла, который пишет ТОЛЬКО этот редактор
    (car_generator.py:_write_model_files), есть у каждой модели, созданной
    через него (подтверждено регрессией по всем моделям проекта)."""
    suffix = f"/{_MODEL_MARKER}"
    return {path[:-len(suffix)] for path in manifest
            if path.startswith("cars/") and path.endswith(suffix)}


def _known_models_path(base_dir: Path) -> Path:
    return base_dir / _KNOWN_MODELS_FILENAME


def _load_known_models(base_dir: Path) -> set[str]:
    try:
        data = json.loads(_known_models_path(base_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return set(data) if isinstance(data, list) else set()


def _save_known_models(base_dir: Path, prefixes: set[str]) -> None:
    try:
        _known_models_path(base_dir).write_text(
            json.dumps(sorted(prefixes), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass  # не смертельно — просто не подчистим один раз, попробуем снова в следующий запуск


def _prune_empty_ancestors(start: Path, stop_at: Path) -> None:
    """Как car_generator.py:_prune_empty_parents (независимая копия — по той
    же причине, что и _MODEL_MARKER выше: этот модуль не должен зависеть от
    car_generator.py)."""
    stop_at = stop_at.resolve()
    current = start.resolve()
    while current != stop_at and stop_at in current.parents:
        try:
            if any(current.iterdir()):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent


def prune_removed_models(base_dir: Path, cars_dir: Path, manifest: dict[str, dict] | None,
                          log=lambda m: None) -> list[str]:
    """После sync_scripts (который только докачивает — см. его докстринг:
    "тяжёлые payload'ы... НЕ подтягиваются автоматически", то же верно и для
    удаления — sync_tree в принципе никогда ничего не стирает) убирает
    локальные папки моделей, пропавших из манифеста сервера с прошлого
    успешного запуска — например, админ переименовал модель в редакторе
    (см. car_generator.py:update_car — физически переносит папку и на
    сервере при публикации), а у уже установленных техников старое название
    продолжало бы висеть в списке марок вечно, задваивая машину.

    Осторожность: сравниваем не "есть в cars_dir, но нет в манифесте
    СЕЙЧАС" (тогда под удаление попала бы и модель, которую сам техник
    только что создал локально через редактор/мастер и ещё не опубликовал —
    её тоже пока нет в манифесте), а с сохранённым СНИМКОМ прошлого
    манифеста (base_dir/known_models.json) — удаляем только то, что раньше
    реально БЫЛО в манифесте и с тех пор из него пропало. Первый запуск
    после обновления программы (снимка ещё нет) ничего не удаляет — только
    заводит базовую линию, чтобы само обновление не могло неожиданно снести
    что-то знакомое технику."""
    if manifest is None:
        return []
    current = _model_prefixes_from_manifest(manifest)
    previous = _load_known_models(base_dir)
    removed: list[str] = []
    if previous:
        for prefix in sorted(previous - current):
            model_dir = base_dir / prefix
            if not (model_dir / _MODEL_MARKER).exists():
                continue  # уже не похоже на синхронизированную модель — не трогаем
            log(f"Модель больше не публикуется на сервере, убираю локальную копию: {prefix}")
            shutil.rmtree(model_dir, ignore_errors=True)
            _prune_empty_ancestors(model_dir.parent, cars_dir)
            removed.append(prefix)
    _save_known_models(base_dir, current)
    return removed


_KNOWN_APKS_FILENAME = "known_apks.json"


def _known_apks_path(base_dir: Path) -> Path:
    return base_dir / _KNOWN_APKS_FILENAME


def _load_known_apks(base_dir: Path) -> set[str]:
    try:
        data = json.loads(_known_apks_path(base_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return set(data) if isinstance(data, list) else set()


def _save_known_apks(base_dir: Path, paths: set[str]) -> None:
    try:
        _known_apks_path(base_dir).write_text(
            json.dumps(sorted(paths), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def prune_removed_apks(base_dir: Path, apk_dir: Path, manifest: dict[str, dict] | None,
                        log=lambda m: None) -> list[str]:
    """Аналог prune_removed_models, но на уровне отдельных файлов общей
    библиотеки apk/ — sync_shared_apks/sync_shared_apk_metadata (см. выше)
    только докачивают, никогда не удаляют, а scanner.py:scan_apks() при
    построении списка приложений всегда отдаёт предпочтение уже скачанному
    локальному файлу перед записью из манифеста сервера — значит, APK,
    удалённый на сервере (например через веб-админку), продолжал бы
    показываться и предлагаться к установке до посинения, пока кто-то не
    удалит его на диске руками (реальный случай: админ удалил Fmplay из не
    той категории на сервере, а в программе он остался).

    Снимок предыдущего манифеста — base_dir/known_apks.json (тот же приём,
    что known_models.json у prune_removed_models): удаляем только то, что
    раньше реально БЫЛО в манифесте и с тех пор пропало, а не "есть
    локально, но нет в манифесте прямо сейчас" — иначе под удаление попал
    бы файл, который админ только что добавил локально и ещё не
    опубликовал. Первый запуск после обновления программы (снимка ещё нет)
    ничего не удаляет — только заводит базовую линию."""
    if manifest is None:
        return []
    current = {path for path in manifest if path == "apk" or path.startswith("apk/")}
    previous = _load_known_apks(base_dir)
    removed: list[str] = []
    if previous:
        for path in sorted(previous - current):
            rel = path[len("apk/"):] if path.startswith("apk/") else ""
            if not rel:
                continue
            local_path = apk_dir / rel
            if not local_path.is_file():
                continue
            log(f"Файл удалён на сервере, убираю локальную копию: {path}")
            try:
                local_path.unlink()
            except OSError:
                continue
            removed.append(path)
            _prune_empty_ancestors(local_path.parent, apk_dir)
    _save_known_apks(base_dir, current)
    return removed


_KNOWN_MODEL_FILES_FILENAME = "_known_files.json"


def prune_model_stale_files(base_dir: Path, model_dir: Path, manifest: dict[str, dict] | None,
                             log=lambda m: None) -> list[str]:
    """Тот же приём, что prune_removed_apks, но для files/ и usb_files/
    ОДНОЙ модели — install_api.py:standard_apks() (через
    scanner.scan_apk_dir_with_remote) точно так же отдаёт предпочтение уже
    скачанному локальному файлу: APK, убранный из "обязательных"/
    "необязательных" уже опубликованной модели, у техника, который его уже
    когда-то скачивал, продолжал бы показываться в списке навсегда.

    Снимок — скрытый model_dir/_known_files.json (рядом с _local_edit.json)
    — своя папка, а не общий файл в base_dir, как у моделей/apk/. Пропускаем
    целиком при активном маркере локальной правки (см. _has_local_edit) —
    та же защита, что уже есть у sync_model_files."""
    if manifest is None or _has_local_edit(model_dir):
        return []
    try:
        remote_base = "cars/" + model_dir.relative_to(base_dir / "cars").as_posix()
    except ValueError:
        return []
    prefix = remote_base + "/"

    def _relevant(path: str) -> str | None:
        if not path.startswith(prefix):
            return None
        rel = path[len(prefix):]
        return rel if rel.startswith("files/") or rel.startswith("usb_files/") else None

    current = {path for path in manifest if _relevant(path) is not None}
    marker_path = model_dir / _KNOWN_MODEL_FILES_FILENAME
    try:
        previous = set(json.loads(marker_path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        previous = set()
    removed: list[str] = []
    if previous:
        for path in sorted(previous - current):
            rel = _relevant(path)
            if not rel:
                continue
            local_path = model_dir / rel
            if not local_path.is_file():
                continue
            log(f"Файл удалён на сервере, убираю локальную копию: {path}")
            try:
                local_path.unlink()
            except OSError:
                continue
            removed.append(path)
            _prune_empty_ancestors(local_path.parent, model_dir)
    try:
        marker_path.write_text(json.dumps(sorted(current), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return removed


def sync_shared_folder(base_dir: Path, name: str, log=lambda m: None,
                       check_cancelled=lambda: None, manifest: dict[str, dict] | None = None,
                       on_progress=lambda done, total, *args: None) -> int:
    """Подтягивает cars/_shared/<name>/ целиком с сервера — точечно, прямо
    перед выполнением USB-этапа, который на неё ссылается (см.
    StepSpec.usb_shared_folder, app/web/api/usb_api.py), а НЕ при каждом
    старте программы (см. sync_scripts: no_recurse_dirs специально не
    разворачивает такие папки — они могут быть сколь угодно тяжёлыми, как
    files/usb_files конкретной модели). Молча ничего не делает, если
    server.json не настроен."""
    url = get_base_url(base_dir)
    if not url:
        return 0
    if manifest is None:
        manifest = fetch_manifest(url)
    return sync_tree(url, f"cars/_shared/{name}", base_dir / "cars" / "_shared" / name,
                     log=log, check_cancelled=check_cancelled, manifest=manifest,
                     on_progress=on_progress)


def _model_wants_own_files(model_dir: Path) -> tuple[bool, bool]:
    """Смотрит в _wizard_spec.json (если модель сделана мастером "Добавить/
    Изменить машину") и решает, декларирует ли она СВОИ файлы в files/ и/или
    usb_files/ — то есть есть ли вообще смысл спрашивать сервер об этих
    подпапках. Если техник в usb-этапе выбрал только общую библиотеку
    (usb_copy_selected_apks/usb_shared_folder), а свои файлы не добавлял —
    usb_files/ никогда не создаётся car_generator._write_model_files, и
    попытка её синхронизировать всегда упрётся в 404 (папки нет и на
    сервере). Читаем "сырой" JSON напрямую, а не через
    car_generator.load_car_spec — тот для этапов "instruction" перечитывает
    instruction.html С ДИСКА, а на клиенте, который ещё не скачал files/,
    этого файла ещё нет: получилась бы курица-и-яйцо (вопрос как раз в том,
    нужно ли качать files/). Для "instruction" поэтому консервативно
    считаем files/ нужным всегда — надёжно определить это без похода на
    сервер нельзя, а у usb/apps/exe/adb список имён файлов и так лежит прямо
    в JSON (см. car_generator._render_spec_json), их непустота — надёжный
    сигнал безо всякого обращения к диску.
    Возвращает (needs_files, needs_usb_files); (True, True), если спеки нет
    или её не удалось прочитать — не рискуем и ведём себя как раньше."""
    spec_path = model_dir / "_wizard_spec.json"
    if not spec_path.exists():
        return True, True
    try:
        data = json.loads(spec_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True, True
    needs_files = False
    needs_usb_files = False
    for step in data.get("steps", []):
        step_type = step.get("type", "manual")
        variants = step.get("variants") or []
        if step_type == "usb":
            if step.get("usb_files") or any(v.get("usb_files") for v in variants):
                needs_usb_files = True
        elif step_type == "apps":
            if step.get("standard_apks") or any(v.get("standard_apks") for v in variants):
                needs_files = True
        elif step_type == "exe" and step.get("exe_file"):
            needs_files = True
        elif step_type == "adb" and step.get("adb_files"):
            needs_files = True
        elif step_type == "instruction":
            needs_files = True
    return needs_files, needs_usb_files


def sync_model_files(base_dir: Path, model, log=lambda m: None, check_cancelled=lambda: None,
                      manifest: dict[str, dict] | None = None,
                      on_progress=lambda done, total, *args: None) -> int:
    """Подтягивает files/ и usb_files/ конкретной модели с сервера — по
    кнопке "Скачать файлы модели" (gui.py) и на всякий случай ещё раз прямо
    перед установкой этой модели. Молча ничего не делает (возвращает 0),
    если server.json не настроен. Подпапку, которую модель по своей спеке
    вообще не использует (см. _model_wants_own_files), даже не запрашивает —
    иначе на сервере, где её никогда не было, каждый раз получали бы 404.

    manifest — см. sync_tree; если не передан, сами один раз запрашиваем
    content/manifest.json (см. fetch_manifest) — так files/ и usb_files/
    (когда нужны оба) достаются из одного запроса вместо двух отдельных
    обходов, а на сервере без манифеста (старый бэкенд) fetch_manifest
    вернёт None и sync_tree сам откатится на обход по HTTP, как раньше."""
    url = get_base_url(base_dir)
    if not url:
        return 0
    if _has_local_edit(model.dir):
        # См. mark_local_edit — модель только что отредактирована локально
        # и ещё не одобрена/опубликована, старая версия с сервера не должна
        # затирать правку прямо перед установкой.
        return 0
    if manifest is None:
        manifest = fetch_manifest(url)
    # Путь строим из model.dir (а не brand+name), чтобы одинаково работать
    # и для обычных моделей (cars/<Марка>/<Модель>/), и для модификаций
    # (cars/<Марка>/<Модель>/<Модификация>/, см. scanner.py:ModelGroup) —
    # реальная глубина папки модели на диске тут единственный источник
    # истины, brand+name её не определяют однозначно.
    remote_base = "cars/" + model.dir.relative_to(base_dir / "cars").as_posix()
    needs_files, needs_usb_files = _model_wants_own_files(model.dir)
    downloaded = 0
    for subfolder, needed in (("files", needs_files), ("usb_files", needs_usb_files)):
        if not needed:
            continue
        local_dir = model.dir / subfolder
        downloaded += sync_tree(url, f"{remote_base}/{subfolder}", local_dir, log=log,
                                 check_cancelled=check_cancelled, manifest=manifest,
                                 on_progress=on_progress)
    # См. prune_model_stale_files — APK/файл, убранный из спеки уже
    # опубликованной модели, иначе продолжал бы висеть локально у техника,
    # который его уже когда-то скачивал (та же причина, что и
    # prune_removed_apks выше для общей библиотеки apk/).
    prune_model_stale_files(base_dir, model.dir, manifest, log=log)
    return downloaded


def sync_model_subfolder(base_dir: Path, local_dir: Path, log=lambda m: None,
                          check_cancelled=lambda: None, manifest: dict[str, dict] | None = None,
                          on_progress=lambda done, total, *args: None) -> int:
    """Синхронизирует ОДНУ конкретную подпапку модели (например
    files/instruction_2, files/exe_1) — в отличие от sync_model_files (вся
    files/+usb_files модели разом), для случаев, когда заранее известно, что
    нужен только конкретный этап (см. app/web/api/install_api.py:
    load_stages/run_exe — инструкция показывается сразу, .exe-инсталлятор
    докачивается по клику "Запустить", а не вся модель целиком при простом
    открытии — раньше "выбор приложений"/усб-этап/прошивка качались заодно,
    даже если техник до них ещё не дошёл)."""
    url = get_base_url(base_dir)
    if not url:
        return 0
    if manifest is None:
        manifest = fetch_manifest(url)
    remote_subpath = "cars/" + local_dir.relative_to(base_dir / "cars").as_posix()
    return sync_tree(url, remote_subpath, local_dir, log=log, check_cancelled=check_cancelled,
                     manifest=manifest, on_progress=on_progress)


def sync_model_apk_metadata(base_dir: Path, folders: list[Path], log=lambda m: None,
                            check_cancelled=lambda: None,
                            manifest: dict[str, dict] | None = None,
                            on_progress=lambda done, total: None) -> int:
    """Подтягивает только JSON-сайдкары APK конкретной модели.

    Сами APK остаются ленивыми: они скачиваются лишь по нажатию «Установить».
    Но имя и описание нужны уже в списке выбора, поэтому лёгкие ``*.json``
    рядом с required/optional забираем отдельно. Это аналог
    :func:`sync_shared_apk_metadata` для пакетов внутри cars/.
    """
    url = get_base_url(base_dir)
    if not url:
        return 0
    if manifest is None:
        manifest = fetch_manifest(url)
    if manifest is None:
        return 0

    cars_dir = (base_dir / "cars").resolve()
    pending: list[tuple[str, Path, dict]] = []
    for folder in folders:
        check_cancelled()
        folder = Path(folder).resolve()
        try:
            remote_dir = "cars/" + folder.relative_to(cars_dir).as_posix()
        except ValueError:
            continue
        prefix = remote_dir.rstrip("/") + "/"
        for item in filter_manifest(manifest, remote_dir):
            remote_path = item["path"]
            if not remote_path.lower().endswith(".json"):
                continue
            relative_path = remote_path[len(prefix):]
            local_path = folder / relative_path
            if not _is_stale(local_path, item):
                continue
            pending.append((remote_path, local_path, item))

    if not pending:
        return 0
    on_progress(0, len(pending), 0, len(pending))
    downloaded = 0
    for done, (remote_path, local_path, item) in enumerate(pending, start=1):
        check_cancelled()
        relative_path = local_path.name
        try:
            download_file(url, remote_path, local_path, log=log,
                          check_cancelled=check_cancelled, mtime=item.get("mtime"))
            downloaded += 1
        except ContentSyncError as exc:
            log(f"Не удалось скачать метаданные {relative_path}: {exc}")
        finally:
            on_progress(done, len(pending), done, len(pending))
    return downloaded


def sync_shared_apk_metadata(base_dir: Path, apk_dir: Path, log=lambda m: None,
                              items: list[dict] | None = None) -> int:
    """Скачивает только *.json сайдкары общей библиотеки apk/ (имя/описание,
    см. app/scanner.py:_read_apk_meta) — лёгкие текстовые файлы, тянутся при
    каждом запуске вместе с list_shared_apk_catalog, в отличие от самих
    *.apk (тяжёлые, только по кнопке/JIT — см. sync_shared_apks/
    ensure_apks_downloaded). Без этого список ещё не скачанных общих
    приложений показывал бы голое имя файла вместо "красивого" имени из
    JSON (см. scanner.scan_apks: remote_only). Молча ничего не делает при
    сетевой ошибке или ненастроенном server.json.

    items — уже полученный список файлов apk/ (см. list_files_recursive),
    если он у вызывающего уже есть (см. sync_api.startup_sync: та же папка
    иначе обходилась бы ПОВТОРНО следом за list_shared_apk_catalog — на
    крупной библиотеке apk/ это удваивает время обхода при каждом запуске
    программы без всякой пользы). None — обойти самостоятельно, как раньше
    (сохраняет обратную совместимость для остальных вызывающих)."""
    url = get_base_url(base_dir)
    if not url:
        return 0
    if items is None:
        try:
            items = list_files_recursive(url, "apk")
        except ContentSyncError as exc:
            log(f"Не удалось получить список файлов с сервера (apk метаданные): {exc}")
            return 0
    downloaded = 0
    for item in items:
        if not item["path"].lower().endswith(".json"):
            continue
        rel = item["path"][len("apk"):].lstrip("/")
        if not rel:
            continue
        local_path = apk_dir / rel
        if not _is_stale(local_path, item):
            continue
        try:
            download_file(url, item["path"], local_path, log=log, mtime=item.get("mtime"))
            downloaded += 1
        except ContentSyncError as exc:
            log(f"Не удалось скачать {item['path']}: {exc}")
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


def list_shared_apk_catalog(base_dir: Path, items: list[dict] | None = None) -> list[dict]:
    """Список файлов общей библиотеки apk/ на сервере — ИМЕНА/РАЗМЕРЫ,
    без скачивания (лёгкий обход через autoindex_format json, см.
    list_files_recursive). Вызывается при каждом запуске (см. gui.py), в
    отличие от sync_shared_apks (полное скачивание — только по кнопке или
    непосредственно перед установкой конкретных выбранных APK, см.
    ensure_apks_downloaded) — иначе дерево выбора приложений было бы пустым
    для ещё не скачанных APK. Каждый элемент — {"rel_path": "<путь
    относительно apk/, например 'Категория/файл.apk'>", "size": int}.
    Молча возвращает [] при сетевой ошибке или ненастроенном server.json —
    отсутствие удалённого каталога не должно ломать запуск.

    items — уже полученный список файлов apk/, см. sync_shared_apk_metadata
    (та же папка при старте программы иначе обходилась бы дважды подряд)."""
    url = get_base_url(base_dir)
    if not url:
        return []
    if items is None:
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
                            check_cancelled=lambda: None,
                            on_progress=lambda done, total, *args: None) -> int:
    """Докачивает из paths (обычно ctx.selected_apks) только те файлы,
    которых ещё нет локально, — по одному, а не всю папку разом (см.
    list_shared_apk_catalog/scanner.scan_apks/scan_apk_dir_with_remote:
    ApkInfo.remote_only). Вызывается прямо перед фактическим использованием
    — из runner.py и usb_api.py. Понимает пути и из общей библиотеки apk/,
    и из "своих" файлов конкретного apps-этапа модели (files/pack*/..., см.
    app/car_generator.py: StepSpec.standard_apks) — качает именно то, что
    отмечено галочками, а не всю папку required/optional разом (см.
    app/web/api/install_api.py: standard_apks — список строится по
    манифесту, без докачки, реальные файлы попадают на диск только здесь).
    Молча ничего не делает, если server.json не настроен."""
    url = get_base_url(base_dir)
    if not url:
        return 0
    apk_dir = apk_dir.resolve()
    cars_dir = (base_dir / "cars").resolve()
    manifest = fetch_manifest(url)
    pending: list[tuple[Path, str, Path, int]] = []
    for raw_path in paths:
        check_cancelled()
        path = Path(raw_path).resolve()
        if path.exists():
            continue
        try:
            rel = path.relative_to(apk_dir)
            remote_path = f"apk/{rel.as_posix()}"
        except ValueError:
            try:
                rel = path.relative_to(cars_dir)
                remote_path = f"cars/{rel.as_posix()}"
            except ValueError:
                continue
        size = (manifest or {}).get(remote_path, {}).get("size", 0)
        pending.append((path, remote_path, rel, size))

    if not pending:
        return 0
    total_files = len(pending)
    total_bytes = sum(size for _, _, _, size in pending)
    byte_progress = total_bytes > 0
    if byte_progress:
        on_progress(0, total_bytes, 0, total_files)
    else:
        on_progress(0, total_files, 0, total_files)
    downloaded = 0
    completed_bytes = 0
    for done, (path, remote_path, rel, size) in enumerate(pending, start=1):
        check_cancelled()
        try:
            def report(file_done: int, _file_total: int, *, base=completed_bytes, file_size=size) -> None:
                if byte_progress:
                    on_progress(base + min(file_done, file_size), total_bytes, done - 1, total_files)

            download_file(url, remote_path, path, log=log, check_cancelled=check_cancelled,
                          on_progress=report)
            downloaded += 1
        except ContentSyncError as exc:
            log(f"Не удалось скачать {rel.as_posix()}: {exc}")
        finally:
            if byte_progress:
                completed_bytes += size
                on_progress(completed_bytes, total_bytes, done, total_files)
            else:
                on_progress(done, total_files, done, total_files)
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
