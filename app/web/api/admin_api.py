"""Обёртка admin_client.py/admin_config.py для диалога "Выгрузить на сервер"
(только в админ-сборке) — портировано из app/admin_upload_dialog.py. Тот же
воркер-поток (логин -> upload_dir(cars) -> upload_dir(apk)), прогресс через
app/web/events.py вместо queue.Queue+self.after(100, ...).

Плюс диалог "Добавить APK в общую библиотеку..." (см. add_apk/
list_apk_categories/create_apk_category) — управление
cars-независимой общей папкой apk/ (категории — подпапки верхнего уровня,
см. app/scanner.py:scan_apks) прямо из программы, без похода руками в
проводник: копирует файл, пишет рядом <файл>.json с "красивым" именем/
описанием (тот же формат, что уже читает scan_apk_dir), и, если уже
выполнен вход через "Выгрузить на сервер..." в эту же сессию (см.
get_cached_session), публикует ТОЛЬКО этот файл на сервер в фоне (см.
_publish_apk_async/upload_single_apk) — не всю apk/ целиком: раньше каждое
добавление одного APK перезаливало всю библиотеку архивом (upload_dir), и
с ростом библиотеки каждая следующая загрузка становилась тяжелее и
дольше предыдущей."""
from __future__ import annotations
import json
import shutil
import threading
from pathlib import Path

from ..events import event_bridge
from ...admin_client import (AdminClientError, clear_cached_session, delete_cars_path, edit_apk_metadata,
                              get_cached_session, list_cars_path, login, set_cached_session, upload_dir,
                              upload_single_apk)
from ...admin_client import browse_tree as _remote_browse_tree
from ...admin_client import copy_path as _remote_copy_path
from ...admin_client import create_folder as _remote_create_folder
from ...admin_client import delete_tree_path as _remote_delete_tree_path
from ...admin_client import move_path as _remote_move_path
from ...admin_config import clear_saved_login, get_admin_base_url, load_saved_login, save_saved_login
from ...car_generator import INVALID_NAME_CHARS
from ...content_sync import list_shared_apk_catalog


class AdminApi:
    def __init__(self, base_dir, apk_dir):
        self.base_dir = base_dir
        self.apk_dir = apk_dir
        self.base_url = get_admin_base_url(base_dir)
        self._cancel_flag = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def _running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_info(self) -> dict:
        return {"available": self.base_url is not None, "base_url": self.base_url}

    def login_only(self, username: str, password: str, remember: bool = False) -> dict:
        """Логин — используется и опциональной кнопкой "Войти в админку..."
        (получить кешированную сессию для "Добавить APK.../Файлы на
        сервере..." без похода в тяжёлую "Выгрузить на сервер..."), и
        обязательным экраном логина при старте admin-сборки (см. app.js:
        pywebviewready/dialogs.js:adminLogin.requireLogin). remember — если
        True, логин/пароль сохраняются локально (см. admin_config.
        save_saved_login) для автовхода при следующих запусках (см.
        try_saved_login ниже); явный снятый чекбокс НЕ стирает уже
        сохранённые данные сам по себе — для этого есть forget_saved_login."""
        if self.base_url is None:
            return {"ok": False, "error": "admin.json не настроен."}
        if not username or not password:
            return {"ok": False, "error": "Введите логин и пароль."}
        try:
            cookie = login(self.base_url, username, password)
        except AdminClientError as exc:
            return {"ok": False, "error": str(exc)}
        set_cached_session(self.base_url, cookie)
        if remember:
            save_saved_login(self.base_dir, username, password)
        return {"ok": True}

    def try_saved_login(self) -> dict:
        """Тихая попытка входа сохранёнными логином/паролем (см.
        admin_config.load_saved_login) — вызывается один раз при старте
        admin-сборки ДО показа обязательного диалога логина (см. app.js).
        Неудача (файла нет, пароль сменился на сервере, сеть недоступна) —
        просто {"ok": False}, без текста ошибки: дальше всё равно покажется
        обычный диалог логина, объяснять тут нечего."""
        if self.base_url is None:
            return {"ok": False}
        saved = load_saved_login(self.base_dir)
        if saved is None:
            return {"ok": False}
        try:
            cookie = login(self.base_url, saved["username"], saved["password"])
        except AdminClientError:
            return {"ok": False}
        set_cached_session(self.base_url, cookie)
        return {"ok": True, "username": saved["username"]}

    def forget_saved_login(self) -> dict:
        clear_saved_login(self.base_dir)
        return {"ok": True}

    def logout(self) -> dict:
        """Полный выход из режима администратора — в отличие от
        forget_saved_login (только чистит сохранённые логин/пароль на
        диске, не трогая текущую сессию), тут ещё чистится кешированная
        cookie-сессия (см. get_cached_session/upload_dir и т.п. — без этого
        уже скрытые кнопки "Выгрузить на сервер"/публикация заявок всё
        равно продолжили бы работать с той же сессией, если бы админ
        включил их обратно в этом же запуске программы без повторного
        входа). admin_mode на самой WebApi сбрасывает вызывающий (см.
        bridge.py: admin_logout) — отсюда наружу он не виден."""
        clear_saved_login(self.base_dir)
        if self.base_url:
            clear_cached_session(self.base_url)
        return {"ok": True}

    def start_upload(self, username: str, password: str) -> dict:
        if self.base_url is None:
            return {"ok": False, "error": "admin.json не настроен."}
        if not username or not password:
            return {"ok": False, "error": "Введите логин и пароль."}
        if self._running:
            return {"ok": False, "error": "Загрузка уже выполняется."}
        self._cancel_flag = threading.Event()
        self._thread = threading.Thread(target=self._worker, args=(username, password), daemon=True)
        self._thread.start()
        return {"ok": True}

    def cancel_upload(self) -> dict:
        if self._running:
            self._cancel_flag.set()
        return {"ok": True}

    def _check_cancelled(self):
        if self._cancel_flag.is_set():
            raise AdminClientError("Загрузка остановлена пользователем.")

    def _worker(self, username: str, password: str) -> None:
        try:
            self._log("Вхожу в админку...")
            cookie = login(self.base_url, username, password)
            set_cached_session(self.base_url, cookie)
            self._log("Вход выполнен.")

            cars_count = upload_dir(self.base_url, cookie, "cars", self.base_dir / "cars",
                                     log=self._log, check_cancelled=self._check_cancelled)
            apk_count = upload_dir(self.base_url, cookie, "apk", self.base_dir / "apk",
                                    log=self._log, check_cancelled=self._check_cancelled)
            self._finish(True, f"Готово: cars/ — {cars_count} файлов, apk/ — {apk_count} файлов.")
        except AdminClientError as exc:
            self._finish(False, str(exc))
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку
            self._finish(False, f"Неожиданная ошибка: {exc}")

    def _log(self, message) -> None:
        event_bridge.push({"kind": "admin_log", "text": str(message)})

    def _finish(self, success: bool, message: str) -> None:
        event_bridge.push({"kind": "admin_finished", "success": success, "message": message})

    # -- общая библиотека apk/ (см. app/scanner.py:scan_apks) --------------
    def list_apk_categories(self) -> list[str]:
        """Папки-категории apk/ — не только уже скачанные локально (на
        свежепоставленном админ-приложении apk/ ещё почти пуст: тяжёлые
        *.apk и целые категории без единого локального файла не тянутся
        автоматически, см. content_sync.py), но и те, что реально есть НА
        СЕРВЕРЕ (список файлов — лёгкий обход, см.
        content_sync.list_shared_apk_catalog, тот же, которым уже пользуется
        обычный список приложений в scanner_api.py) — иначе в диалоге
        "Добавить APK..." категории, для которых на этой машине ещё нет ни
        одного скачанного файла, были бы просто не видны."""
        categories: set[str] = set()
        if self.apk_dir.is_dir():
            categories.update(
                p.name for p in self.apk_dir.iterdir()
                if p.is_dir() and not p.name.startswith(("_", "."))
            )
        for entry in list_shared_apk_catalog(self.base_dir):
            category, _, _ = entry["rel_path"].rpartition("/")
            if category:
                categories.add(category)
        return sorted(categories)

    def create_apk_category(self, name: str) -> dict:
        name = name.strip()
        if not name:
            return {"ok": False, "error": "Введите название папки."}
        if any(ch in name for ch in INVALID_NAME_CHARS):
            return {"ok": False, "error": f"Название не должно содержать: {' '.join(INVALID_NAME_CHARS)}"}
        (self.apk_dir / name).mkdir(parents=True, exist_ok=True)
        return {"ok": True, "name": name}

    def add_apk(self, file_path: str, name: str, description: str, category: str) -> dict:
        """Копирует выбранный APK в apk/<category>/ (или в корень apk/, если
        category пуста) и пишет рядом <файл>.json с "красивым" именем и
        описанием — тот же формат, что читает scan_apk_dir(). Затем в фоне
        пытается опубликовать ТОЛЬКО этот файл на сервер, если уже есть
        кешированная сессия (см. _publish_apk_async) — сам не логинит,
        чтобы не спрашивать пароль на каждый добавленный APK, только
        использует сессию, полученную через "Выгрузить на сервер..." в эту
        же сессию программы."""
        category = category.strip()
        if any(ch in category for ch in INVALID_NAME_CHARS):
            return {"ok": False, "error": f"Название папки не должно содержать: {' '.join(INVALID_NAME_CHARS)}"}
        src = Path(file_path)
        if not src.exists():
            return {"ok": False, "error": "Файл не найден."}
        name = name.strip()
        if not name:
            return {"ok": False, "error": "Введите название приложения."}
        description = description.strip()

        dest_dir = (self.apk_dir / category) if category else self.apk_dir
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        try:
            shutil.copy2(src, dest)
            meta = {"name": name, "description": description}
            dest.with_suffix(".json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "error": f"Не удалось сохранить файл: {exc}"}

        self._publish_apk_async(category, dest.name, dest, name, description)
        return {"ok": True}

    def _publish_apk_async(self, category: str, filename: str, path: Path,
                            name: str, description: str) -> None:
        cookie = get_cached_session(self.base_url) if self.base_url else None
        if not (self.base_url and cookie):
            event_bridge.push({
                "kind": "apk_upload_log",
                "text": "Сохранено локально. Не опубликовано на сервере — сначала войдите "
                        'через "Войти в админку..."/"Выгрузить на сервер..." '
                        "(сессия входа переиспользуется).",
            })
            event_bridge.push({"kind": "apk_upload_finished", "success": True})
            return
        threading.Thread(
            target=self._publish_apk_worker, args=(cookie, category, filename, path, name, description),
            daemon=True).start()

    def _publish_apk_worker(self, cookie: str, category: str, filename: str, path: Path,
                             name: str, description: str) -> None:
        try:
            upload_single_apk(self.base_url, cookie, category, filename, path, log=self._apk_log)
            edit_apk_metadata(self.base_url, cookie, category, filename, name, description)
            self._apk_log(f"Опубликовано на сервере: {filename}.")
            event_bridge.push({"kind": "apk_upload_finished", "success": True})
        except AdminClientError as exc:
            if "истекла" in str(exc):
                clear_cached_session(self.base_url)
            self._apk_log(f"Не опубликовано на сервере: {exc}")
            event_bridge.push({"kind": "apk_upload_finished", "success": False})

    def _apk_log(self, message) -> None:
        event_bridge.push({"kind": "apk_upload_log", "text": str(message)})

    # -- файловый браузер по content/cars/ на СЕРВЕРЕ (см. server/backend.py:
    # /admin/api/cars/list, /admin/api/cars) — единственный способ убрать
    # что-то уже опубликованное: upload_dir/upload_model льют строго
    # слиянием и никогда сами не удаляют лишнее с сервера. Требует уже
    # выполненного входа через "Выгрузить на сервер..." в эту же сессию
    # (см. get_cached_session) — сам не логинит.
    def _require_session(self) -> tuple[str, str] | dict:
        if not self.base_url:
            return {"ok": False, "error": "admin.json не настроен."}
        cookie = get_cached_session(self.base_url)
        if not cookie:
            return {"ok": False, "error": 'Сначала войдите через "Выгрузить на сервер...".'}
        return self.base_url, cookie

    def browse_server_cars(self, rel_path: str) -> dict:
        session = self._require_session()
        if isinstance(session, dict):
            return session
        base_url, cookie = session
        try:
            items = list_cars_path(base_url, cookie, rel_path)
        except AdminClientError as exc:
            if "истекла" in str(exc):
                clear_cached_session(base_url)
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "items": items}

    def delete_server_cars_path(self, rel_path: str) -> dict:
        session = self._require_session()
        if isinstance(session, dict):
            return session
        base_url, cookie = session
        try:
            delete_cars_path(base_url, cookie, rel_path)
        except AdminClientError as exc:
            if "истекла" in str(exc):
                clear_cached_session(base_url)
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    # -- единый файловый менеджер (см. server/backend.py: GET/DELETE
    # /admin/api/browse, POST /admin/api/move) — заменяет диалог "Файлы на
    # сервере" (browse_server_cars/delete_server_cars_path выше — оставлены
    # как есть для обратной совместимости, но новый UI ими не пользуется):
    # работает и с cars/, и с apk/, умеет перенос/переименование, а не
    # только просмотр/удаление. После успешной операции на сервере —
    # best-effort то же самое над ЛОКАЛЬНОЙ копией этой машины (если файла
    # тут вообще нет — например, ещё не скачивали — просто пропускаем).
    def _local_root_dir(self, root: str) -> Path:
        return (self.base_dir / "cars") if root == "cars" else self.apk_dir

    @staticmethod
    def _local_apk_sibling_json(root: str, path: Path) -> Path | None:
        """Зеркало server/backend.py:_apk_sibling_json — <файл>.json рядом
        с .apk в дереве apk/ (не показывается в списке, см. browse_tree,
        сервер уже фильтрует), которую нужно перенести/удалить заодно с
        самим .apk, иначе локально на машине админа она осиротеет."""
        if root != "apk" or path.suffix.lower() != ".apk":
            return None
        return path.with_suffix(".json")

    def browse_tree(self, root: str, rel_path: str) -> dict:
        session = self._require_session()
        if isinstance(session, dict):
            return session
        base_url, cookie = session
        try:
            items = _remote_browse_tree(base_url, cookie, root, rel_path)
        except AdminClientError as exc:
            if "истекла" in str(exc):
                clear_cached_session(base_url)
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "items": items}

    def delete_tree_path(self, root: str, rel_path: str) -> dict:
        session = self._require_session()
        if isinstance(session, dict):
            return session
        base_url, cookie = session
        try:
            _remote_delete_tree_path(base_url, cookie, root, rel_path)
        except AdminClientError as exc:
            if "истекла" in str(exc):
                clear_cached_session(base_url)
            return {"ok": False, "error": str(exc)}
        local_target = self._local_root_dir(root) / rel_path
        try:
            if local_target.is_dir():
                shutil.rmtree(local_target)
            elif local_target.is_file():
                local_target.unlink()
                sibling = self._local_apk_sibling_json(root, local_target)
                if sibling is not None:
                    sibling.unlink(missing_ok=True)
        except OSError:
            pass  # не критично — при следующем запуске content_sync сам подчистит (prune_removed_*)
        return {"ok": True}

    def move_path(self, root: str, from_rel: str, to_rel: str) -> dict:
        session = self._require_session()
        if isinstance(session, dict):
            return session
        base_url, cookie = session
        try:
            _remote_move_path(base_url, cookie, root, from_rel, to_rel)
        except AdminClientError as exc:
            if "истекла" in str(exc):
                clear_cached_session(base_url)
            return {"ok": False, "error": str(exc)}
        root_dir = self._local_root_dir(root)
        local_from = root_dir / from_rel
        local_to = root_dir / to_rel
        try:
            if local_from.exists() and not local_to.exists():
                local_to.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(local_from), str(local_to))
                sibling = self._local_apk_sibling_json(root, local_from)
                if sibling is not None and sibling.is_file():
                    sibling_target = local_to.with_suffix(".json")
                    if not sibling_target.exists():
                        shutil.move(str(sibling), str(sibling_target))
        except OSError:
            pass  # не критично — та же причина, что и в delete_tree_path выше
        return {"ok": True}

    def copy_path(self, root: str, from_rel: str, to_rel: str) -> dict:
        session = self._require_session()
        if isinstance(session, dict):
            return session
        base_url, cookie = session
        try:
            _remote_copy_path(base_url, cookie, root, from_rel, to_rel)
        except AdminClientError as exc:
            if "истекла" in str(exc):
                clear_cached_session(base_url)
            return {"ok": False, "error": str(exc)}
        root_dir = self._local_root_dir(root)
        local_from = root_dir / from_rel
        local_to = root_dir / to_rel
        try:
            if local_from.exists() and not local_to.exists():
                local_to.parent.mkdir(parents=True, exist_ok=True)
                if local_from.is_dir():
                    shutil.copytree(local_from, local_to)
                else:
                    shutil.copy2(local_from, local_to)
                    sibling = self._local_apk_sibling_json(root, local_from)
                    if sibling is not None and sibling.is_file():
                        sibling_target = local_to.with_suffix(".json")
                        if not sibling_target.exists():
                            shutil.copy2(sibling, sibling_target)
        except OSError:
            pass  # не критично — та же причина, что и в delete_tree_path выше
        return {"ok": True}

    def create_folder(self, root: str, rel_path: str) -> dict:
        session = self._require_session()
        if isinstance(session, dict):
            return session
        base_url, cookie = session
        try:
            _remote_create_folder(base_url, cookie, root, rel_path)
        except AdminClientError as exc:
            if "истекла" in str(exc):
                clear_cached_session(base_url)
            return {"ok": False, "error": str(exc)}
        try:
            (self._local_root_dir(root) / rel_path).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # не критично — та же причина, что и в delete_tree_path выше
        return {"ok": True}
