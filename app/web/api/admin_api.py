"""Обёртка admin_client.py/admin_config.py для диалога "Выгрузить на сервер"
(только в админ-сборке) — портировано из app/admin_upload_dialog.py. Тот же
воркер-поток (логин -> upload_dir(cars) -> upload_dir(apk)), прогресс через
app/web/events.py вместо queue.Queue+self.after(100, ...)."""
import threading

from ..events import event_bridge
from ...admin_client import AdminClientError, login, set_cached_session, upload_dir
from ...admin_config import get_admin_base_url


class AdminApi:
    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.base_url = get_admin_base_url(base_dir)
        self._cancel_flag = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def _running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_info(self) -> dict:
        return {"available": self.base_url is not None, "base_url": self.base_url}

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
