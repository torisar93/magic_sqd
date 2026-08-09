"""Обёртка usb_utils.py/usb_context.py для диалога "Через USB-флешку" внутри
stage wizard (этап type="usb"). Портировано из app/usb_dialog.py — тот же
воркер-поток и порядок операций (докачка files/выбранных APK, опциональное
форматирование, затем run_fn(ctx)), только прогресс идёт через
app/web/events.py вместо queue.Queue+self.after(100, ...)."""
import threading
import traceback
from pathlib import Path

from ..events import event_bridge
from ...content_sync import ensure_apks_downloaded, sync_model_files
from ...install_context import InstallCancelled
from ...stage_runner import load_stages
from ...usb_context import UsbContext
from ...usb_utils import list_removable_drives, format_drive, UsbSafetyError


def _drive_to_dict(d) -> dict:
    return {"letter": d.letter, "label": d.label, "total_bytes": d.total_bytes,
            "free_bytes": d.free_bytes, "display": d.display}


class UsbApi:
    def __init__(self, base_dir, scanner_api):
        self.base_dir = base_dir
        self._scanner_api = scanner_api
        self._cancel_flag = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def _running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def list_drives(self) -> list[dict]:
        return [_drive_to_dict(d) for d in list_removable_drives()]

    def start(self, model_key: str, stage_index: int, variant, selected_apk_paths: list[str],
              drive_letter: str, do_format: bool, filesystem: str) -> dict:
        if self._running:
            return {"ok": False, "error": "Копирование уже выполняется."}
        model = self._scanner_api.get_model(model_key)
        if model is None:
            return {"ok": False, "error": "unknown model key"}
        stage = load_stages(model)[stage_index]

        self._cancel_flag = threading.Event()
        self._thread = threading.Thread(
            target=self._worker,
            args=(model, stage, variant, selected_apk_paths, drive_letter, do_format, filesystem),
            daemon=True,
        )
        self._thread.start()
        return {"ok": True}

    def cancel(self) -> dict:
        if self._running:
            self._cancel_flag.set()
            self._log("Останавливаю... (завершится на ближайшей проверке)")
        return {"ok": True}

    def _check_cancelled(self):
        if self._cancel_flag.is_set():
            raise InstallCancelled("Копирование остановлено пользователем.")

    def _worker(self, model, stage, variant, selected_apk_paths, drive_letter, do_format, filesystem):
        try:
            sync_model_files(self.base_dir, model, log=self._log, check_cancelled=self._check_cancelled)
            ensure_apks_downloaded(self.base_dir, self.base_dir / "apk", selected_apk_paths,
                                    log=self._log, check_cancelled=self._check_cancelled)

            if do_format:
                try:
                    format_drive(drive_letter, filesystem, model.name, self.base_dir, log=self._log)
                except UsbSafetyError as exc:
                    self._finish(False, str(exc))
                    return
                if self._cancel_flag.is_set():
                    self._finish(False, "Остановлено пользователем после форматирования.")
                    return

            ctx = UsbContext(
                drive_root=Path(f"{drive_letter}\\"),
                model_dir=model.dir,
                selected_apks=selected_apk_paths,
                log_fn=self._log,
                cancel_flag=self._cancel_flag,
                variant=variant,
                shared_dir=self.base_dir / "cars" / "_shared",
            )
            stage["run"](ctx)
        except InstallCancelled as exc:
            self._finish(False, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку
            self._log(traceback.format_exc())
            self._finish(False, f"Ошибка: {exc}")
            return
        self._finish(True, "Копирование на флешку завершено.")

    def _log(self, message) -> None:
        event_bridge.push({"kind": "usb_log", "text": str(message)})

    def _finish(self, success: bool, message: str) -> None:
        event_bridge.push({"kind": "usb_finished", "success": success, "message": message})
