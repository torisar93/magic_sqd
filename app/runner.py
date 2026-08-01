"""Динамическая загрузка и запуск install.py конкретной модели в фоновом потоке."""
import importlib.util
import threading
import traceback
from pathlib import Path

from .content_sync import ensure_apks_downloaded, sync_model_files
from .install_context import InstallContext, InstallCancelled


class InstallRunner:
    def __init__(self, adb_path, on_log, on_finished, base_dir=None, ask_input_fn=None):
        """
        on_log(str) вызывается из фонового потока при каждой строке лога.
        on_finished(success: bool, message: str) вызывается по завершении.
        Оба callback должны сами позаботиться о потокобезопасности (см. gui.py).
        base_dir, если задан, используется, чтобы перед установкой тихо
        подтянуть files/usb_files модели со своего сервера (см.
        content_sync.py, server/README.md), а также докачать те из
        выбранных общих APK (selected_apks), которых ещё нет в apk/
        локально (см. content_sync.ensure_apks_downloaded — дерево выбора
        уже показывает их благодаря list_shared_apk_catalog при запуске,
        но сами файлы качаются только сейчас) — так APK по факту
        скачиваются "по требованию", в момент нажатия "Установить".
        ask_input_fn(prompt, title) -> str | None, если задан, пробрасывается
        в ctx.ask_input() для install.py/stages.py, которым нужно спросить у
        пользователя что-то во время установки (например, IPv6-адрес). Тоже
        вызывается из фонового потока и должен сам позаботиться о
        потокобезопасности (см. gui.py._ask_input_threaded).
        """
        self.adb_path = adb_path
        self.on_log = on_log
        self.on_finished = on_finished
        self.base_dir = base_dir
        self.ask_input_fn = ask_input_fn
        self._thread = None
        self._cancel_flag = threading.Event()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def cancel(self):
        self._cancel_flag.set()

    def start(self, model, device_serial, selected_apks, run_fn=None):
        """Если run_fn не задан — запускает install.py модели (как раньше).
        Если задан — запускает run_fn(ctx) напрямую (используется мастером этапов)."""
        if self.running:
            raise RuntimeError("Установка уже выполняется.")
        if not run_fn and not model.install_script:
            raise RuntimeError(f"В папке модели нет install.py: {model.dir}")

        self._cancel_flag = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            args=(model, device_serial, selected_apks, run_fn),
            daemon=True,
        )
        self._thread.start()

    def _check_cancelled(self):
        if self._cancel_flag.is_set():
            raise InstallCancelled("Установка остановлена пользователем.")

    def _run(self, model, device_serial, selected_apks, run_fn=None):
        try:
            if self.base_dir:
                sync_model_files(self.base_dir, model, log=self.on_log,
                                  check_cancelled=self._check_cancelled)
                ensure_apks_downloaded(self.base_dir, self.base_dir / "apk", selected_apks,
                                        log=self.on_log, check_cancelled=self._check_cancelled)
            ctx = InstallContext(
                adb_path=self.adb_path,
                device_serial=device_serial,
                model_dir=model.dir,
                selected_apks=selected_apks,
                log_fn=self.on_log,
                cancel_flag=self._cancel_flag,
                ask_input_fn=self.ask_input_fn,
            )
            if run_fn:
                run_fn(ctx)
            else:
                module = self._load_module(model.install_script)
                if not hasattr(module, "run"):
                    raise RuntimeError("install.py должен содержать функцию run(ctx)")
                module.run(ctx)
        except InstallCancelled as exc:
            self.on_finished(False, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку скрипта
            self.on_log(traceback.format_exc())
            self.on_finished(False, f"Ошибка установки: {exc}")
            return
        self.on_finished(True, "Установка завершена успешно.")

    @staticmethod
    def _load_module(script_path: Path):
        spec = importlib.util.spec_from_file_location(
            f"car_install_script_{abs(hash(str(script_path)))}", script_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
