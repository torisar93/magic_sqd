"""Низкоуровневые обёртки над adb.exe."""
import os
import subprocess
import sys
import time
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def find_adb_path(base_dir: Path) -> str:
    """Ищет adb.exe сначала в tools/ рядом с приложением, потом в PATH."""
    bundled = base_dir / "tools" / "adb.exe"
    if bundled.exists():
        return str(bundled)
    return "adb"


class AdbError(RuntimeError):
    pass


class Adb:
    def __init__(self, adb_path: str, device: str | None = None, log=None):
        self.adb_path = adb_path
        self.device = device
        self._log = log or (lambda msg: None)

    def _base_args(self):
        args = [self.adb_path]
        if self.device:
            args += ["-s", self.device]
        return args

    def run(self, *args, check=True, timeout=120):
        cmd = self._base_args() + list(args)
        self._log("$ " + " ".join(cmd))
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
            )
        except FileNotFoundError as exc:
            raise AdbError(
                f"adb.exe не найден ({self.adb_path}). Положите platform-tools в папку tools/."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"Команда не ответила за {timeout} сек: {' '.join(cmd)}") from exc

        if result.stdout:
            self._log(result.stdout.rstrip())
        if result.stderr:
            self._log(result.stderr.rstrip())

        if check and result.returncode != 0:
            raise AdbError(f"Команда завершилась с ошибкой ({result.returncode}): {' '.join(cmd)}")
        return result

    def shell(self, command: str, check=True, timeout=120):
        return self.run("shell", command, check=check, timeout=timeout)

    def install(self, apk_path, reinstall=True, extra_args=None, timeout=180):
        apk_path = str(apk_path)
        args = ["install"]
        if reinstall:
            args.append("-r")
        if extra_args:
            args += list(extra_args)
        args.append(apk_path)
        return self.run(*args, timeout=timeout)

    def uninstall(self, package, check=False):
        return self.run("uninstall", package, check=check)

    def push(self, local, remote, timeout=180):
        return self.run("push", str(local), remote, timeout=timeout)

    def pull(self, remote, local, timeout=180):
        return self.run("pull", remote, str(local), timeout=timeout)

    def reboot(self):
        return self.run("reboot", check=False)

    def wait_for_device(self, timeout=90):
        self._log(f"Ожидание устройства (до {timeout} сек)...")
        self.run("wait-for-device", timeout=timeout)

    def wait_boot_completed(self, timeout=120, poll_interval=2):
        self._log("Ожидание полной загрузки системы...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self.run("shell", "getprop sys.boot_completed", check=False, timeout=10)
            if result.stdout and result.stdout.strip() == "1":
                self._log("Система загружена.")
                return
            time.sleep(poll_interval)
        raise AdbError("Не дождались полной загрузки системы (sys.boot_completed).")


def list_devices(adb_path: str) -> list[dict]:
    """Возвращает список подключённых устройств: [{'serial': ..., 'state': ..., 'model': ...}]."""
    try:
        result = subprocess.run(
            [adb_path, "devices", "-l"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        return []
    except subprocess.TimeoutExpired:
        return []

    devices = []
    for line in result.stdout.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        model = ""
        for token in parts[2:]:
            if token.startswith("model:"):
                model = token.split(":", 1)[1]
        devices.append({"serial": serial, "state": state, "model": model})
    return devices
