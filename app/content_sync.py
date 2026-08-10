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
    log получает по одной строке на папку ВЕРХНЕГО уровня (например каждую
    марку внутри cars/) — сам обход рекурсивный и на десятках марок/моделей
    может занять заметное время (отдельный HTTP-запрос на каждую подпапку),
    а до этого коммита список файлов возвращался только по завершении ВСЕГО
    обхода — в логе был один "Проверяю файлы..." и потом долгая тишина,
    выглядевшая как зависание при первом запуске на пустом cars/."""
    files: list[dict] = []
    top = subpath.strip("/")
    top_depth = top.count("/") + 1 if top else 0
    _walk(base_url, top, files, skip_dirs, no_recurse_dirs, log, top_depth)
    return files


def _walk(base_url: str, rel_path: str, files: list[dict], skip_dirs: tuple,
          no_recurse_dirs: tuple, log, top_depth: int) -> None:
    url = f"{base_url}/{_encode_path(rel_path)}/" if rel_path else f"{base_url}/"
    for entry in _get_json(url):
        name = entry.get("name", "")
        if not name:
            continue
        if entry.get("type") == "directory" and name in skip_dirs:
            continue
        child_rel = f"{rel_path}/{name}" if rel_path else name
        if entry.get("type") == "directory":
            if rel_path in no_recurse_dirs:
                continue  # payload-подпапка (например cars/_shared/<набор>/) — не разворачиваем
            if child_rel.count("/") + 1 == top_depth + 1:
                log(f"Проверяю {child_rel}...")
            _walk(base_url, child_rel, files, skip_dirs, no_recurse_dirs, log, top_depth)
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
              log=lambda m: None, check_cancelled=lambda: None, skip_dirs: tuple = (),
              no_recurse_dirs: tuple = ()) -> int:
    """Скачивает из remote_subpath в local_dir всё, чего там ещё нет (или
    что отличается по размеру). skip_dirs/no_recurse_dirs — см.
    list_files_recursive. Возвращает число скачанных файлов; сетевые ошибки
    не бросает наружу — только логирует, чтобы недоступный сервер не мешал
    работе программы. Исключение — 404 на листинге самой remote_subpath: это
    означает "такой папки на сервере просто нет" (например, техник в этапе
    ничего своего не добавлял, только общую библиотеку — см.
    _model_wants_own_files) и не более ошибка, чем пустая папка, поэтому не
    засоряет лог. Логирует ход дела (запрос списка, каждый реально
    скачиваемый файл, итог) — раньше молчала при успехе от начала до конца,
    из-за чего в момент первой закачки файлов модели/общей библиотеки
    технику казалось, что программа зависла, хотя она просто тихо качала."""
    remote_subpath = remote_subpath.strip("/")
    log(f"Проверяю файлы на сервере ({remote_subpath})...")
    try:
        items = list_files_recursive(base_url, remote_subpath, skip_dirs=skip_dirs,
                                      no_recurse_dirs=no_recurse_dirs, log=log)
    except ContentSyncError as exc:
        if exc.code != 404:
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
        log(f"Скачиваю {item['path']}...")
        try:
            download_file(base_url, item["path"], local_path, log=log,
                          check_cancelled=check_cancelled)
            downloaded += 1
        except ContentSyncError as exc:
            log(f"Не удалось скачать {item['path']}: {exc}")
    if downloaded:
        log(f"Скачано файлов ({remote_subpath}): {downloaded}.")
    return downloaded


def sync_scripts(base_dir: Path, cars_dir: Path, log=lambda m: None) -> int:
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
    настроен."""
    url = get_base_url(base_dir)
    if not url:
        return 0
    return sync_tree(url, "cars", cars_dir, log=log, skip_dirs=("files", "usb_files"),
                      no_recurse_dirs=("cars/_shared",))


def sync_shared_folder(base_dir: Path, name: str, log=lambda m: None,
                        check_cancelled=lambda: None) -> int:
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
    return sync_tree(url, f"cars/_shared/{name}", base_dir / "cars" / "_shared" / name,
                      log=log, check_cancelled=check_cancelled)


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


def sync_model_files(base_dir: Path, model, log=lambda m: None, check_cancelled=lambda: None) -> int:
    """Подтягивает files/ и usb_files/ конкретной модели с сервера — по
    кнопке "Скачать файлы модели" (gui.py) и на всякий случай ещё раз прямо
    перед установкой этой модели. Молча ничего не делает (возвращает 0),
    если server.json не настроен. Подпапку, которую модель по своей спеке
    вообще не использует (см. _model_wants_own_files), даже не запрашивает —
    иначе на сервере, где её никогда не было, каждый раз получали бы 404."""
    url = get_base_url(base_dir)
    if not url:
        return 0
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
                                 check_cancelled=check_cancelled)
    return downloaded


def sync_shared_apk_metadata(base_dir: Path, apk_dir: Path, log=lambda m: None) -> int:
    """Скачивает только *.json сайдкары общей библиотеки apk/ (имя/описание,
    см. app/scanner.py:_read_apk_meta) — лёгкие текстовые файлы, тянутся при
    каждом запуске вместе с list_shared_apk_catalog, в отличие от самих
    *.apk (тяжёлые, только по кнопке/JIT — см. sync_shared_apks/
    ensure_apks_downloaded). Без этого список ещё не скачанных общих
    приложений показывал бы голое имя файла вместо "красивого" имени из
    JSON (см. scanner.scan_apks: remote_only). Молча ничего не делает при
    сетевой ошибке или ненастроенном server.json."""
    url = get_base_url(base_dir)
    if not url:
        return 0
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
        if local_path.exists() and local_path.stat().st_size == item.get("size", -1):
            continue
        try:
            download_file(url, item["path"], local_path, log=log)
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
