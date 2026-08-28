"""Очередь заявок клиентов ("На модерации") в админ-сборке — обёртка над
admin_client.py (тот же протокол, что уже использует веб-админка, см.
server/backend.py: GET /admin/api/submissions[/peek], GET /admin/download/
<имя>, DELETE /admin/api/submissions) плюс локальный стейджинг
(app/pending_submissions.py). Просмотр/правка застейдженной заявки идёт
через УЖЕ существующий визуальный редактор (car_editor_api.py) — заявка
регистрируется в ScannerApi как обычная ModelInfo с is_pending=True (см.
scanner_api.py:register_pending), car_load_spec/car_save её не отличают от
обычной модели, кроме пропуска автопубликации при сохранении (см.
car_editor_api.py:_worker).

Публикация НЕ использует серверный POST /admin/api/submissions/approve —
он распаковывает исходно присланный .zip как есть, без учёта правок,
внесённых в редакторе. Вместо этого publish() заливает ТЕКУЩЕЕ содержимое
застейдженной папки через upload_model_as() (тот же эндпоинт, что и
обычное "Сохранить" в редакторе) и только потом убирает исходную заявку
из очереди — так правки и byte-identical "одобрить как есть" идут одним и
тем же путём, без отдельного состояния "редактировали/не редактировали".

Скачивание/публикация могут быть небыстрыми (заявка — целая папка модели,
иногда с прошивками в usb_files/) — по образцу AdminApi (admin_api.py):
фоновый поток + прогресс/результат через app/web/events.py."""
from __future__ import annotations
import threading
from pathlib import Path

from ..events import event_bridge
from ... import pending_submissions
from ...admin_client import (AdminClientError, clear_cached_session, delete_submission,
                              download_submission, get_cached_session, list_submissions,
                              peek_submission, upload_model_as)
from ...admin_config import get_admin_base_url
from ...scanner import ModelInfo


class SubmissionsApi:
    def __init__(self, base_dir: Path, cars_dir: Path, scanner_api):
        self.base_dir = base_dir
        self.cars_dir = cars_dir
        self._scanner_api = scanner_api
        self.base_url = get_admin_base_url(base_dir)
        self._thread: threading.Thread | None = None

    @property
    def _running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- сессия (та же кешированная cookie, что и "Выгрузить на сервер...") --
    def _require_session(self):
        if not self.base_url:
            return {"ok": False, "error": "admin.json не настроен."}
        cookie = get_cached_session(self.base_url)
        if not cookie:
            return {"ok": False, "error": 'Сначала войдите через "Выгрузить на сервер...".'}
        return self.base_url, cookie

    # -- список/превью — короткие GET, без фонового потока -------------------
    def list(self) -> dict:
        session = self._require_session()
        if isinstance(session, dict):
            return session
        base_url, cookie = session
        try:
            items = list_submissions(base_url, cookie)
        except AdminClientError as exc:
            if "истекла" in str(exc):
                clear_cached_session(base_url)
            return {"ok": False, "error": str(exc)}
        for item in items:
            item["staged"] = pending_submissions.is_staged(self.base_dir, item["name"])
        return {"ok": True, "items": items}

    def peek(self, name: str) -> dict:
        session = self._require_session()
        if isinstance(session, dict):
            return session
        base_url, cookie = session
        try:
            items = peek_submission(base_url, cookie, name)
        except AdminClientError as exc:
            if "истекла" in str(exc):
                clear_cached_session(base_url)
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "items": items}

    # -- стейджинг: скачать + распаковать + зарегистрировать в ScannerApi ---
    def stage(self, name: str, brand: str | None, model: str | None, modification: str | None) -> dict:
        """brand/model/modification приходят из строки списка (см. list())
        — фронтенд просто передаёт их обратно, ничего заново не парсит."""
        if self._running:
            return {"ok": False, "error": "Уже выполняется операция с заявками."}
        if not brand or not model:
            # Очень старая заявка без сайдкар-метаданных (см.
            # server/backend.py:read_submission_metadata) — публиковать
            # некуда (нет марки/модели), а значит и открывать в редакторе
            # незачем: тот же отказ, что и у серверного /submissions/approve
            # для таких заявок. Остаётся только "Отклонить" из списка.
            return {"ok": False, "error": (
                "У этой заявки нет данных о марке/модели (прислана до появления "
                "этой возможности) — открыть в редакторе нельзя, доступно только "
                '"Отклонить" или скачать вручную через "Файлы на сервере...".'
            )}
        session = self._require_session()
        if isinstance(session, dict):
            return session
        base_url, cookie = session
        self._thread = threading.Thread(
            target=self._stage_worker, args=(base_url, cookie, name, brand, model, modification),
            daemon=True)
        self._thread.start()
        return {"ok": True}

    def _stage_worker(self, base_url, cookie, name, brand, model, modification) -> None:
        tmp_zip = self.base_dir / pending_submissions.PENDING_DIRNAME / f"{Path(name).stem}.download.zip"
        try:
            download_submission(base_url, cookie, name, tmp_zip, log=self._log)
            self._log("Распаковываю...")
            model_dir = pending_submissions.stage(self.base_dir, name, tmp_zip)
            model_info = ModelInfo(
                brand=brand or "?", name=model or "?", dir=model_dir,
                stages_script=(model_dir / "stages.py") if (model_dir / "stages.py").exists() else None,
                modification=modification or None, is_pending=True, submission_name=name,
            )
            model_dict = self._scanner_api.register_pending(model_info)
            self._finish("stage", True, "Готово.", extra={"model": model_dict})
        except AdminClientError as exc:
            if "истекла" in str(exc):
                clear_cached_session(base_url)
            self._finish("stage", False, str(exc))
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку
            self._finish("stage", False, f"Неожиданная ошибка: {exc}")
        finally:
            tmp_zip.unlink(missing_ok=True)

    # -- публикация застейдженной (возможно, отредактированной) заявки ------
    def publish(self, key: str) -> dict:
        if self._running:
            return {"ok": False, "error": "Уже выполняется операция с заявками."}
        model = self._scanner_api.get_model(key)
        if model is None or not model.is_pending:
            return {"ok": False, "error": "Заявка не застейджена — сначала откройте её."}
        session = self._require_session()
        if isinstance(session, dict):
            return session
        base_url, cookie = session
        self._thread = threading.Thread(
            target=self._publish_worker, args=(base_url, cookie, model, key), daemon=True)
        self._thread.start()
        return {"ok": True}

    def _publish_worker(self, base_url, cookie, model: ModelInfo, key: str) -> None:
        try:
            self._log(f"Публикую {model.brand} / {model.name}...")
            upload_model_as(base_url, cookie, self.cars_dir, model.dir,
                             model.brand, model.name, model.modification or "", log=self._log)
            delete_submission(base_url, cookie, model.submission_name)
            pending_submissions.discard(self.base_dir, model.submission_name)
            self._scanner_api.unregister_pending(key)
            self._finish("publish", True, "Опубликовано.")
        except AdminClientError as exc:
            if "истекла" in str(exc):
                clear_cached_session(base_url)
            self._finish("publish", False, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._finish("publish", False, f"Неожиданная ошибка: {exc}")

    # -- отклонение — по имени заявки, вне зависимости от того, была ли она
    # застейджена (кнопка есть и прямо в списке, и внутри просмотра/правки) --
    def reject(self, name: str) -> dict:
        if self._running:
            return {"ok": False, "error": "Уже выполняется операция с заявками."}
        session = self._require_session()
        if isinstance(session, dict):
            return session
        base_url, cookie = session
        self._thread = threading.Thread(
            target=self._reject_worker, args=(base_url, cookie, name), daemon=True)
        self._thread.start()
        return {"ok": True}

    def _reject_worker(self, base_url, cookie, name: str) -> None:
        try:
            delete_submission(base_url, cookie, name)
            pending_submissions.discard(self.base_dir, name)
            self._scanner_api.unregister_pending_by_submission(name)
            self._finish("reject", True, "Отклонено.")
        except AdminClientError as exc:
            clear_cached_session(base_url)
            self._finish("reject", False, str(exc))
        except Exception as exc:  # noqa: BLE001
            self._finish("reject", False, f"Неожиданная ошибка: {exc}")

    def _log(self, message) -> None:
        event_bridge.push({"kind": "submissions_log", "text": str(message)})

    def _finish(self, op: str, success: bool, message: str, extra: dict | None = None) -> None:
        event_bridge.push({"kind": "submissions_finished", "op": op, "success": success,
                            "message": message, **(extra or {})})
