"""Объект ctx, передаваемый в install.py каждой модели."""
import time
from pathlib import Path

from .adb_utils import Adb, AdbError


class InstallCancelled(RuntimeError):
    """Пользователь нажал "Стоп"."""


class InstallContext:
    def __init__(self, adb_path, device_serial, model_dir: Path, selected_apks,
                 log_fn, cancel_flag, ask_input_fn=None, shared_dir: Path | None = None):
        self.model_dir = Path(model_dir)
        self.files_dir = self.model_dir / "files"
        # cars/_shared/ — общие для МНОГИХ моделей файлы (не только Python-
        # модули вроде wifi_adb.py, которые уже подключаются через sys.path
        # в сгенерированном stages.py, но и произвольный payload — скрипты,
        # прошивки и т.п., см. StepSpec.usb_shared_folder в car_generator.py).
        # Синхронизируется на клиент автоматически как часть cars/ (см.
        # content_sync.sync_scripts), одной копией на всех — не дублируется
        # в каждую модель. None, если вызывающий не передал base_dir
        # (не должно происходить в реальной установке, только в тестах).
        self.shared_dir = Path(shared_dir) if shared_dir is not None else None
        self.selected_apks = [Path(p) for p in selected_apks]
        self.device = device_serial
        self._log_fn = log_fn
        self._cancel_flag = cancel_flag
        self._ask_input_fn = ask_input_fn
        self._adb = Adb(adb_path, device_serial, log=self._log_fn)

    # --- служебное -------------------------------------------------
    def log(self, message):
        self._log_fn(str(message))

    def check_cancelled(self):
        if self._cancel_flag.is_set():
            raise InstallCancelled("Установка остановлена пользователем.")

    def ask_input(self, prompt, title="Ввод данных"):
        """Запрашивает у пользователя строку (например, IPv6-адрес магнитолы)
        через диалоговое окно. Вызывается из фонового потока установки и
        блокирует его до ответа пользователя — сам диалог показывается на
        главном потоке через ask_input_fn (см. gui.py/stage_wizard.py).
        Бросает InstallCancelled, если пользователь нажал "Отмена" или
        оставил поле пустым."""
        self.check_cancelled()
        if not self._ask_input_fn:
            raise RuntimeError("В этом режиме запуска ask_input недоступен")
        value = self._ask_input_fn(prompt, title)
        self.check_cancelled()
        if not value:
            raise InstallCancelled("Ввод отменён пользователем.")
        return value

    def sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.check_cancelled()
            time.sleep(min(0.3, max(0, end - time.time())))

    # --- файлы модели ------------------------------------------------
    def file(self, relative_path):
        """Путь к файлу внутри files/ данной модели."""
        return self.files_dir / relative_path

    # --- adb-обёртки ---------------------------------------------------
    def adb(self, *args, **kwargs):
        self.check_cancelled()
        return self._adb.run(*args, **kwargs)

    def shell(self, command, **kwargs):
        self.check_cancelled()
        return self._adb.shell(command, **kwargs)

    def install_apk(self, path, reinstall=True, extra_args=None, timeout=180):
        self.check_cancelled()
        self.log(f"Установка APK: {Path(path).name}")
        return self._adb.install(path, reinstall=reinstall, extra_args=extra_args, timeout=timeout)

    def install_selected_apks(self, extra_args=None):
        for apk in self.selected_apks:
            self.install_apk(apk, extra_args=extra_args)

    def uninstall(self, package, check=False):
        return self._adb.uninstall(package, check=check)

    def push(self, local, remote, timeout=180):
        self.check_cancelled()
        return self._adb.push(local, remote, timeout=timeout)

    def pull(self, remote, local, timeout=180):
        self.check_cancelled()
        return self._adb.pull(remote, local, timeout=timeout)

    def reboot(self, wait=True, boot_timeout=120):
        self.log("Перезагрузка устройства...")
        self._adb.reboot()
        if wait:
            self.check_cancelled()
            self._adb.wait_for_device()
            self._adb.wait_boot_completed(timeout=boot_timeout)

    def wait_for_device(self, timeout=90):
        self._adb.wait_for_device(timeout=timeout)
