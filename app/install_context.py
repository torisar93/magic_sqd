"""Объект ctx, передаваемый в install.py каждой модели."""
import time
from pathlib import Path

from .adb_utils import Adb, AdbError


class InstallCancelled(RuntimeError):
    """Пользователь нажал "Стоп"."""


class InstallContext:
    def __init__(self, adb_path, device_serial, model_dir: Path, selected_apks,
                 log_fn, cancel_flag):
        self.model_dir = Path(model_dir)
        self.files_dir = self.model_dir / "files"
        self.selected_apks = [Path(p) for p in selected_apks]
        self.device = device_serial
        self._log_fn = log_fn
        self._cancel_flag = cancel_flag
        self._adb = Adb(adb_path, device_serial, log=self._log_fn)

    # --- служебное -------------------------------------------------
    def log(self, message):
        self._log_fn(str(message))

    def check_cancelled(self):
        if self._cancel_flag.is_set():
            raise InstallCancelled("Установка остановлена пользователем.")

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

    def install_selected_apks(self):
        for apk in self.selected_apks:
            self.install_apk(apk)

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
