"""«Скачать заранее» на ПК — кнопка рядом с «Открыть инструкцию» (подписчики Boosty, владелец 2026-10-05). Состав
пакета, отметка на диске и удаление — app/offline_pack.py (общий с Android); здесь — скачивание с ходом дела.

События для интерфейса (js/offline.js, общий с Android): offline_progress {key, done, total} — байты;
offline_finished {key, ok, state, failed, cancelled, error}.

Каталог APK целиком (владелец 14.10.2026, только подписчикам) — app/apk_catalog.py; события apk_catalog_progress
{done, total} и apk_catalog_finished {ok, state, failed, cancelled, error, locked}."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from ..events import event_bridge
from ... import apk_catalog, offline_pack
from ...content_sync import (ContentSyncError, fetch_manifest, get_base_url, model_on_server,
                              prune_model_stale_files, sync_tree)

_MANIFEST_TTL_SECONDS = 20.0


class OfflineCancelled(Exception):
    pass


class OfflineApi:
    def __init__(self, base_dir: Path, scanner_api, auth_api):
        self.base_dir = Path(base_dir)
        self.cars_dir = self.base_dir / "cars"
        self._scanner = scanner_api
        self._auth = auth_api
        self._lock = threading.Lock()
        self._running_key: str | None = None
        self._cancel = threading.Event()
        self._manifest: dict | None = None
        self._manifest_at = 0.0
        self.apk_dir = self.base_dir / "apk"
        self._catalog_running = False
        self._catalog_cancel = threading.Event()

    def _get_manifest(self, fresh: bool = False) -> dict | None:
        url = get_base_url(self.base_dir)
        if not url:
            return None
        now = time.monotonic()
        if fresh or self._manifest is None or now - self._manifest_at > _MANIFEST_TTL_SECONDS:
            manifest = fetch_manifest(url)
            if manifest is not None or fresh:
                self._manifest, self._manifest_at = manifest, now
            return manifest
        return self._manifest

    def _model_dir(self, model_key: str) -> Path | None:
        model = self._scanner.get_model(model_key)
        return model.dir if model is not None and not model.early_locked else None

    # ------------------------------------------------------------------
    def status(self, model_key: str) -> dict:
        """Состояние кнопки: state none|done|update|progress, размеры пакета; available — пакет есть на сервере
        (своя модель техника, неотправленная правка или модель без файлов — кнопки нет)."""
        model_dir = self._model_dir(model_key)
        if model_dir is None:
            return {"state": "none", "available": False}
        if self._running_key == model_key:
            return {"state": "progress", "available": True}
        manifest = self._get_manifest()
        result = offline_pack.status(manifest, self.cars_dir, model_dir)
        if manifest is None:
            result["available"] = result["state"] != "none"
        else:
            result["available"] = model_on_server(self.base_dir, model_dir, manifest) and result.get("files", 0) > 0
        return result

    def download(self, model_key: str) -> dict:
        model_dir = self._model_dir(model_key)
        if model_dir is None:
            return {"ok": False, "error": "Модель не найдена."}
        # Платная функция (решение владельца, 2026-10-05): проверяем по серверу, а не только по цвету кнопки —
        # отметку подписчика ставят и снимают в админке, пока программа открыта.
        auth = self._auth.refresh_subscriber()
        if not auth.get("ok"):
            return {"ok": False, "error": "Нет связи с сервером или вы не вошли в аккаунт.", "locked": True}
        if not auth.get("subscriber"):
            return {"ok": False, "locked": True}
        with self._lock:
            if self._running_key is not None:
                return {"ok": False, "error": "Уже скачивается другая модель — дождитесь окончания."}
            self._running_key = model_key
            self._cancel.clear()
        threading.Thread(target=self._download_worker, args=(model_key, model_dir), daemon=True).start()
        return {"ok": True}

    def cancel(self) -> dict:
        self._cancel.set()
        return {"ok": True}

    def delete_info(self, model_key: str) -> dict:
        model_dir = self._model_dir(model_key)
        if model_dir is None:
            return {"bytes": 0}
        return {"bytes": offline_pack.delete_size(self.cars_dir, model_dir)}

    def delete(self, model_key: str) -> dict:
        model_dir = self._model_dir(model_key)
        if model_dir is None:
            return {"ok": False, "error": "Модель не найдена."}
        if self._running_key == model_key:
            return {"ok": False, "error": "Модель ещё скачивается."}
        freed = offline_pack.delete(self.cars_dir, model_dir)
        self._log(f"Скачанные заранее файлы удалены ({model_dir.relative_to(self.cars_dir).as_posix()}): "
                  f"освобождено {freed / 1048576:.0f} МБ.")
        return {"ok": True, "freed": freed, "state": "none"}

    # ------------------------------------------------------------------
    def _download_worker(self, model_key: str, model_dir: Path) -> None:
        result = {"kind": "offline_finished", "key": model_key, "ok": False}
        try:
            result.update(self.download_pack(model_dir))
        except OfflineCancelled:
            result.update(cancelled=True, state=offline_pack.marker_state(model_dir) or "none")
        except Exception as exc:  # noqa: BLE001 - любая ошибка — в окно, не в тишину
            self._log(f"Не удалось скачать модель заранее: {exc}")
            result.update(error=str(exc), state=offline_pack.marker_state(model_dir) or "none")
        finally:
            with self._lock:
                self._running_key = None
        event_bridge.push(result)

    def download_pack(self, model_dir: Path) -> dict:
        url = get_base_url(self.base_dir)
        manifest = self._get_manifest(fresh=True) if url else None
        if manifest is None:
            return {"ok": False, "error": "Нет связи с сервером — скачать заранее можно, пока есть интернет."}
        key = str(model_dir)
        label = model_dir.relative_to(self.cars_dir).as_posix()

        def check_cancelled() -> None:
            if self._cancel.is_set():
                raise OfflineCancelled()

        # Сначала сверка с сервером, как при открытии модели: старые версии файлов убираются
        prune_model_stale_files(self.base_dir, model_dir, manifest, log=self._log)
        current = offline_pack.plan(manifest, self.cars_dir, model_dir)
        total = current["missing_bytes"]
        event_bridge.push({"kind": "offline_progress", "key": key, "done": 0, "total": total})
        if current["missing"]:
            self._log(f"Скачиваю заранее {label}: {len(current['missing'])} файлов, {total / 1048576:.0f} МБ")
            missing = {item["path"]: {"size": item["size"], "mtime": item["mtime"]} for item in current["missing"]}
            last = [0.0]

            def on_progress(done, all_bytes, *_files) -> None:
                now = time.monotonic()
                if done >= all_bytes or now - last[0] >= 0.15:
                    last[0] = now
                    event_bridge.push({"kind": "offline_progress", "key": key, "done": done, "total": all_bytes})

            try:
                # Пакет — список путей от content/: корень сайта = папка программы (там же cars/)
                sync_tree(url, "", self.base_dir, log=self._log, check_cancelled=check_cancelled,
                          manifest=missing, on_progress=on_progress)
            except ContentSyncError as exc:
                self._log(f"Скачивание заранее прервалось: {exc}")
        check_cancelled()
        after = offline_pack.plan(manifest, self.cars_dir, model_dir)
        state = offline_pack.write_marker(model_dir, after)
        failed = len(after["missing"])
        if failed:
            self._log(f"{label}: не скачалось файлов — {failed}. Нажмите «Обновить», чтобы докачать.")
        else:
            self._log(f"{label}: модель доступна без интернета.")
        return {"ok": not failed, "state": state, "failed": failed,
                "total_bytes": after["total_bytes"], "missing_bytes": after["missing_bytes"]}

    # ------------------------------------------------------------------
    # Каталог APK целиком — подписчикам (владелец 14.10.2026)
    def apk_catalog_status(self) -> dict:
        if self._catalog_running:
            return {"state": "progress", "available": True}
        return apk_catalog.status(self._get_manifest(), self.apk_dir)

    def apk_catalog_download(self) -> dict:
        auth = self._auth.refresh_subscriber()  # по серверу: отметку подписчика ставят и снимают в админке
        if not auth.get("ok"):
            return {"ok": False, "error": "Нет связи с сервером или вы не вошли в аккаунт.", "locked": True}
        if not auth.get("subscriber"):
            return {"ok": False, "locked": True}
        with self._lock:
            if self._catalog_running:
                return {"ok": False, "error": "Каталог уже скачивается."}
            self._catalog_running = True
            self._catalog_cancel.clear()
        threading.Thread(target=self._catalog_worker, daemon=True).start()
        return {"ok": True}

    def apk_catalog_cancel(self) -> dict:
        self._catalog_cancel.set()
        return {"ok": True}

    def _catalog_state(self) -> str:
        return "update" if apk_catalog.downloaded_once(self.apk_dir) else "none"

    def _catalog_worker(self) -> None:
        result = {"kind": "apk_catalog_finished", "ok": False}
        try:
            result.update(self.download_apk_catalog())
        except OfflineCancelled:
            result.update(cancelled=True, state=self._catalog_state())
        except Exception as exc:  # noqa: BLE001 - любая ошибка — в окно, не в тишину
            self._log(f"Не удалось скачать каталог APK: {exc}")
            result.update(error=str(exc), state=self._catalog_state())
        finally:
            with self._lock:
                self._catalog_running = False
        event_bridge.push(result)

    def download_apk_catalog(self) -> dict:
        url = get_base_url(self.base_dir)
        manifest = self._get_manifest(fresh=True) if url else None
        if manifest is None:
            return {"ok": False, "error": "Нет связи с сервером — каталог можно скачать, пока есть интернет.",
                    "state": self._catalog_state()}

        def check_cancelled() -> None:
            if self._catalog_cancel.is_set():
                raise OfflineCancelled()

        current = apk_catalog.plan(manifest, self.apk_dir)
        apk_catalog.prepare(self.apk_dir, current)  # положенное не программой — в apk/_unverified/, качаем заново
        total = current["missing_bytes"]
        event_bridge.push({"kind": "apk_catalog_progress", "done": 0, "total": total})
        if current["missing"]:
            self._log(f"Скачиваю каталог APK: {len(current['missing'])} файлов, {total / 1048576:.0f} МБ")
            missing = {item["path"]: {"size": item["size"], "mtime": item["mtime"]} for item in current["missing"]}
            last = [0.0]

            def on_progress(done, all_bytes, *_files) -> None:
                now = time.monotonic()
                if done >= all_bytes or now - last[0] >= 0.15:
                    last[0] = now
                    event_bridge.push({"kind": "apk_catalog_progress", "done": done, "total": all_bytes})

            try:
                sync_tree(url, "", self.base_dir, log=self._log, check_cancelled=check_cancelled,
                          manifest=missing, on_progress=on_progress)
            except ContentSyncError as exc:
                self._log(f"Скачивание каталога APK прервалось: {exc}")
        check_cancelled()
        after = apk_catalog.plan(manifest, self.apk_dir)
        failed = len(after["missing"])
        if failed:
            self._log(f"Каталог APK: не скачалось файлов — {failed}. Нажмите «Обновить каталог APK», чтобы докачать.")
        else:
            apk_catalog.write_marker(self.apk_dir)
            self._log("Каталог APK скачан: все приложения доступны без интернета.")
        return {"ok": not failed, "state": "done" if not failed else self._catalog_state(), "failed": failed,
                "total_bytes": after["total_bytes"], "missing_bytes": after["missing_bytes"]}

    @staticmethod
    def _log(message) -> None:
        event_bridge.push({"kind": "log", "text": str(message)})
