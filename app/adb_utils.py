"""Низкоуровневые обёртки над adb.exe."""
from __future__ import annotations
import concurrent.futures
import ipaddress
import os
import socket
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


def find_powershell_path() -> str:
    """Полный путь к powershell.exe вместо голого имени — на части машин
    (политики, ограничивающие PATH, сторонний софт, переписавший переменную
    окружения) subprocess.run(["powershell", ...]) падает с [WinError 2]
    "Не удается найти указанный файл", хотя сам powershell.exe у
    пользователя стоит штатно, просто не резолвится через PATH (реальный
    случай — техник словил это именно на форматировании флешки, см.
    usb_utils.format_drive). Путь стандартный для любой версии Windows,
    меняться не должен; на всякий случай всё равно проверяем, что файл
    реально существует, и откатываемся на голое имя, если нет (пусть
    Windows сама поищет — лучше, чем упасть здесь на пустом месте)."""
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(windir) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.exists() else "powershell"


def get_default_gateway_ip() -> str | None:
    """IP шлюза по умолчанию активного сетевого адаптера — на магнитолах с
    Wi-Fi ADB ноутбук обычно подключается к собственной Wi-Fi-сети
    магнитолы, и этот шлюз и есть её IP (не нужно спрашивать у техника
    руками). Независимая копия той же логики, что и в
    cars/_shared/wifi_adb.py:get_default_gateway (тот модуль подгружается
    отдельно из cars/_shared при установке конкретной модели, без доступа к
    app/ — здесь та же idea для кнопки «Подключить Wi-Fi» под логом
    главного окна, которая должна работать независимо от конкретной
    модели). Возвращает None вместо исключения — вызывающий сам решает, как
    показать ошибку технику."""
    try:
        result = subprocess.run(
            [find_powershell_path(), "-NoProfile", "-NonInteractive", "-Command",
             "(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } "
             "| Select-Object -First 1 -ExpandProperty IPv4DefaultGateway).NextHop"],
            capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    ip = (result.stdout or "").strip()
    return ip or None


def scan_for_adb_hosts(port: int, timeout: float = 0.25) -> list[str]:
    """Независимая копия cars/_shared/wifi_adb.py:scan_for_adb_hosts — для
    кнопки "Подключить Wi-Fi" под логом главного окна (см.
    app/web/api/install_api.py:scan_wifi), которая не привязана к
    конкретной модели и не может импортировать cars/_shared (тот
    подгружается отдельно, только при установке конкретной модели). Ищет
    хосты локальной подсети с открытым port — нужен, когда магнитола сама
    подключается к сети/точке доступа ноутбука (её IP тогда заранее
    неизвестен, в отличие от случая, покрытого get_default_gateway_ip)."""
    try:
        result = subprocess.run(
            [find_powershell_path(), "-NoProfile", "-NonInteractive", "-Command",
             "$c = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } "
             "| Select-Object -First 1; "
             "if ($c) { \"$($c.IPv4Address.IPAddress)/$($c.IPv4Address.PrefixLength)\" }"],
            capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    output = (result.stdout or "").strip()
    if not output:
        return []
    try:
        iface = ipaddress.ip_interface(output)
    except ValueError:
        return []
    # Не крупнее /24 — см. обоснование в wifi_adb.py:_get_local_ipv4_and_subnet.
    prefix = max(iface.network.prefixlen, 24)
    network = ipaddress.ip_network(f"{iface.ip}/{prefix}", strict=False)
    own_ip = str(iface.ip)
    hosts = [str(h) for h in network.hosts() if str(h) != own_ip]

    def probe(ip: str) -> str | None:
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return ip
        except OSError:
            return None

    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
        for ip in pool.map(probe, hosts):
            if ip:
                found.append(ip)
    return sorted(found, key=lambda ip: tuple(int(part) for part in ip.split(".")))


class AdbError(RuntimeError):
    pass


# Команды верхнего уровня adb (управляют самим adb/подключением) в отличие
# от произвольного текста, который должен выполниться ВНУТРИ шелла
# устройства. Мини-консоль под логом главного окна (см.
# app/web/api/install_api.py: console_send) раньше всегда оборачивала ввод в
# "adb shell <...>" — из-за этого "connect <ip>:<port>" пытался выполниться
# КАК ШЕЛЛ-КОМАНДА НА УСТРОЙСТВЕ (там такой команды нет), а не как сама
# adb-команда подключения, поэтому Wi-Fi ADB через консоль не работал вообще
# — устройство никогда не подключалось и не появлялось в списке.
TOP_LEVEL_COMMANDS = {
    "connect", "disconnect", "pair", "tcpip", "usb", "root", "unroot",
    "remount", "reboot", "wait-for-device", "kill-server", "start-server",
    "devices", "get-state", "get-serialno", "push", "pull", "install",
    "uninstall", "logcat", "forward", "reverse", "sideload",
}
# Из них команды уровня adb-сервера (не конкретного устройства) — "-s
# <serial>" им не нужен, а для connect/pair устройство ещё и не может быть
# известно заранее (в этом весь смысл команды), поэтому выполняются без
# привязки к выбранному в консоли устройству, даже если оно выбрано.
SERVER_LEVEL_COMMANDS = {"connect", "disconnect", "pair", "devices", "kill-server", "start-server"}


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
        # Раньше здесь безусловно логировалось "$ <команда>" + сырой stdout/
        # stderr НА КАЖДЫЙ вызов — на цепочках из десятков adb-команд (см.
        # cars/_shared/adb_permissions.py: grant_all_permissions) это
        # превращало лог в стену технического текста, за которой не видно
        # результата (реальная жалоба — выдача разрешений выглядела как
        # сломанная установка, хотя всё прошло успешно). Человеку интересен
        # итог, а не сам факт вызова adb — осмысленные фразы ("Выдаю
        # разрешения...", "Установка APK: ...") уже пишет вызывающий код
        # через ctx.log(), а ошибка и так долетает до техника через
        # AdbError/install_finished. Сырой вывод больше никуда не льём.
        cmd = self._base_args() + list(args)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
                stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError as exc:
            raise AdbError(
                f"adb.exe не найден ({self.adb_path}). Положите platform-tools в папку tools/."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"Команда не ответила за {timeout} сек: {' '.join(cmd)}") from exc

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


def kill_server(adb_path: str) -> None:
    """Останавливает локальный adb-сервер (adb kill-server) — вызывается при
    закрытии программы, иначе adb.exe висит в процессах и после закрытия
    держит файлы (мешает пересборке/обновлению, см. память проекта)."""
    try:
        subprocess.run(
            [adb_path, "kill-server"],
            capture_output=True,
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass


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
            stdin=subprocess.DEVNULL,
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
