"""Динамическая загрузка и запуск install.py конкретной модели в фоновом потоке."""
import importlib.util
import threading
import traceback
from pathlib import Path

from .install_context import InstallContext, InstallCancelled


class InstallRunner:
    def __init__(self, adb_path, on_log, on_finished):
        """
        on_log(str) вызывается из фонового потока при каждой строке лога.
        on_finished(success: bool, message: str) вызывается по завершении.
        Оба callback должны сами позаботиться о потокобезопасности (см. gui.py).
        """
        self.adb_path = adb_path
        self.on_log = on_log
        self.on_finished = on_finished
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

    def _run(self, model, device_serial, selected_apks, run_fn=None):
        try:
            ctx = InstallContext(
                adb_path=self.adb_path,
                device_serial=device_serial,
                model_dir=model.dir,
                selected_apks=selected_apks,
                log_fn=self.on_log,
                cancel_flag=self._cancel_flag,
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
