"""Обёртка admin_client.py/admin_config.py для диалога "Выгрузить на сервер"
(только в админ-сборке) — портировано из app/admin_upload_dialog.py. Тот же
воркер-поток (логин -> upload_dir(cars) -> upload_dir(apk)), прогресс через
app/web/events.py вместо queue.Queue+self.after(100, ...).

Плюс диалог "Добавить APK в общую библиотеку..." (см. add_apk/
list_apk_categories/create_apk_category/delete_apk_category) — управление
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
from ...admin_client import (AdminClientError, clear_cached_session, delete_cars_path,
                              edit_apk_metadata, get_cached_session, list_cars_path, login,
                              set_cached_session, upload_dir, upload_single_apk)
from ...admin_config import get_admin_base_url
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

    def login_only(self, username: str, password: str) -> dict:
        """Логин без выгрузки cars/apk — для отдельной кнопки "Войти в
        админку...", чтобы получить кешированную сессию (см.
        get_cached_session/_publish_apk_async/_require_session выше) без
        обязательной полной публикации через "Выгрузить на сервер..."."""
        if self.base_url is None:
            return {"ok": False, "error": "admin.json не настроен."}
        if not username or not password:
            return {"ok": False, "error": "Введите логин и пароль."}
        try:
            cookie = login(self.base_url, username, password)
        except AdminClientError as exc:
            return {"ok": False, "error": str(exc)}
        set_cached_session(self.base_url, cookie)
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

    def delete_apk_category(self, name: str) -> dict:
        path = self.apk_dir / name.strip()
        if not name.strip() or not path.is_dir():
            return {"ok": False, "error": "Папка не найдена."}
        shutil.rmtree(path)
        return {"ok": True}

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
