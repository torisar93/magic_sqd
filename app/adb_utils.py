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

from . import mdns_scan

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def find_adb_path(base_dir: Path) -> str:
    """Ищет adb сначала в tools/ (Windows, adb.exe) или tools_mac/ (macOS,
    Mach-O без расширения — см. tools_mac/README.txt) рядом с приложением,
    потом в PATH."""
    if sys.platform == "win32":
        bundled = base_dir / "tools" / "adb.exe"
    else:
        bundled = base_dir / "tools_mac" / "adb"
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


def _default_gateway_and_iface_mac() -> tuple[str, str] | None:
    """macOS/BSD-аналог Get-NetIPConfiguration — 'route -n get default'
    печатает и шлюз, и интерфейс активного маршрута по умолчанию отдельными
    строками ("gateway: 192.168.1.1", "interface: en0"). Возвращает None,
    если активного подключения нет (нет строк в выводе)."""
    try:
        result = subprocess.run(
            ["route", "-n", "get", "default"],
            capture_output=True, text=True, timeout=15, stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    gateway = iface = None
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("gateway:"):
            gateway = line.split(":", 1)[1].strip()
        elif line.startswith("interface:"):
            iface = line.split(":", 1)[1].strip()
    return (gateway, iface) if gateway and iface else None


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
    if sys.platform != "win32":
        info = _default_gateway_and_iface_mac()
        return info[0] if info else None
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


def _local_ipv4_and_subnet_mac() -> tuple[str, "ipaddress.IPv4Network"] | None:
    """IP и подсеть (максимум /24 — см. обоснование ниже) активного
    интерфейса на macOS. 'route -n get default' даёт имя интерфейса
    (en0/...), 'ifconfig <iface>' — его IPv4-адрес и маску вида
    "inet 192.168.1.23 netmask 0xffffff00 broadcast ..."."""
    info = _default_gateway_and_iface_mac()
    if info is None:
        return None
    _, iface = info
    try:
        result = subprocess.run(
            ["ifconfig", iface], capture_output=True, text=True, timeout=15,
            stdin=subprocess.DEVNULL,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    ip = netmask_hex = None
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("inet ") and not line.startswith("inet6"):
            parts = line.split()
            try:
                ip = parts[1]
                netmask_hex = parts[parts.index("netmask") + 1]
            except (ValueError, IndexError):
                continue
            break
    if not ip or not netmask_hex:
        return None
    try:
        prefix = bin(int(netmask_hex, 16)).count("1")
        # Не крупнее /24 — см. обоснование в wifi_adb.py:_get_local_ipv4_and_subnet.
        prefix = max(prefix, 24)
        network = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    except ValueError:
        return None
    return ip, network


def _local_ipv4_and_subnet() -> tuple[str, "ipaddress.IPv4Network"] | None:
    """Платформенный диспетчер — вынесено из scan_for_adb_hosts отдельной
    функцией, чтобы scan_network_for_wifi_adb (см. ниже) могла получить тот
    же список хостов подсети для ping-скана, не пересчитывая его дважды по
    разным путям для Windows/macOS."""
    if sys.platform != "win32":
        return _local_ipv4_and_subnet_mac()
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
        return None
    output = (result.stdout or "").strip()
    if not output:
        return None
    try:
        iface = ipaddress.ip_interface(output)
    except ValueError:
        return None
    # Не крупнее /24 — см. обоснование в wifi_adb.py:_get_local_ipv4_and_subnet.
    prefix = max(iface.network.prefixlen, 24)
    network = ipaddress.ip_network(f"{iface.ip}/{prefix}", strict=False)
    return str(iface.ip), network


def scan_for_adb_hosts(port: int, timeout: float = 0.25) -> list[str]:
    """Независимая копия cars/_shared/wifi_adb.py:scan_for_adb_hosts — для
    кнопки "Подключить Wi-Fi" под логом главного окна (см.
    app/web/api/install_api.py:scan_wifi), которая не привязана к
    конкретной модели и не может импортировать cars/_shared (тот
    подгружается отдельно, только при установке конкретной модели). Ищет
    хосты локальной подсети с открытым port — нужен, когда магнитола сама
    подключается к сети/точке доступа ноутбука (её IP тогда заранее
    неизвестен, в отличие от случая, покрытого get_default_gateway_ip)."""
    info = _local_ipv4_and_subnet()
    if info is None:
        return []
    own_ip, network = info
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


def _ping_sweep(hosts: list[str], timeout_ms: int = 400) -> list[str]:
    """Реальный список живых хостов подсети через системный ping — не
    зависит от того, какой (если вообще какой-то) порт у них открыт, в
    отличие от scan_for_adb_hosts (см. её докстринг и cars/_shared/
    wifi_adb.py:_ping_sweep — независимая копия той же idea)."""
    ping_cmd = ["ping", "-n", "1", "-w", str(timeout_ms)] if sys.platform == "win32" \
        else ["ping", "-c", "1", "-W", str(timeout_ms)]

    def probe(ip: str) -> str | None:
        try:
            result = subprocess.run(
                ping_cmd + [ip], capture_output=True, timeout=timeout_ms / 1000 + 2,
                creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        return ip if result.returncode == 0 else None

    found = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=64) as pool:
        for ip in pool.map(probe, hosts):
            if ip:
                found.append(ip)
    return found


def scan_network_for_wifi_adb(port: int) -> list:
    """Независимая копия cars/_shared/wifi_adb.py:scan_network_for_wifi_adb
    — объединённый список кандидатов для кнопки "Подключить Wi-Fi" (ping-
    скан + скан заданного порта + mDNS-резолв "android.local"/"_adb-tls-
    connect._tcp", см. её докстринг и android/.../WebBridge.kt:scanHosts —
    тот же принцип "объединение источников, а не пересечение"). Кандидаты,
    подтверждённые через mDNS (это ТОЧНО Android, не просто "что-то ответило
    на пинг") идут первыми и с "recommended": True — фронтенд (см.
    app/web/frontend/js/refinement05.js: LabUI.connection) подсвечивает их
    отдельно, как раньше подсвечивался хост с открытым портом 5555."""
    ip_candidates: set[str] = set()

    info = _local_ipv4_and_subnet()
    if info is not None:
        own_ip, network = info
        hosts = [str(h) for h in network.hosts() if str(h) != own_ip]
        ip_candidates.update(_ping_sweep(hosts))
        ip_candidates.update(scan_for_adb_hosts(port))

    endpoints = mdns_scan.resolve_adb_tls_connect_endpoints()
    endpoint_hosts = {host for host, _ in endpoints}
    android_local_ip = mdns_scan.resolve_android_local()
    if android_local_ip in endpoint_hosts:
        android_local_ip = None  # уже покажется отдельно, со своим портом — не дублируем
    ip_candidates -= endpoint_hosts
    if android_local_ip:
        ip_candidates.discard(android_local_ip)

    results: list = [{"host": host, "port": found_port, "recommended": True}
                      for host, found_port in sorted(endpoints)]
    if android_local_ip:
        results.append({"host": android_local_ip, "recommended": True})
    results += sorted(ip_candidates, key=lambda ip: tuple(int(part) for part in ip.split(".")))
    return results


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
    # "shell" — тоже сама команда adb, не шелл-команда устройства: раньше
    # явный ввод "shell <команда>" в мини-консоли не попадал в этот набор
    # (первое слово — "shell", его тут не было) и уходил в ветку по
    # умолчанию, которая САМА уже оборачивает ввод в "adb shell ..." — в
    # итоге получалось "adb shell 'shell <команда>'" (шелл внутри шелла,
    # устройство не знает такой команды).
    "shell",
}
# Из них команды уровня adb-сервера (не конкретного устройства) — "-s
# <serial>" им не нужен, а для connect/pair устройство ещё и не может быть
# известно заранее (в этом весь смысл команды), поэтому выполняются без
# привязки к выбранному в консоли устройству, даже если оно выбрано.
SERVER_LEVEL_COMMANDS = {"connect", "disconnect", "pair", "devices", "kill-server", "start-server"}


def normalize_console_command(command: str) -> str:
    """Срезает лишние префиксы, с которыми и человек по привычке к
    настоящему терминалу, и ИИ-чат (см. app/web/api/chat_api.py) иногда
    пишут команду — оба канала ожидают ввод БЕЗ них:
    - "adb " в начале (сама консоль уже подразумевает adb) — иначе первым
      словом становится "adb", в TOP_LEVEL_COMMANDS такого нет, и вся
      команда вместо adb.exe на ПК уходит КАК ЕСТЬ в шелл подключённого
      устройства ("/system/bin/sh: adb: inaccessible or not found").
    - "-s <serial>"/"--serial <serial>" сразу после (не)срезанного "adb " —
      устройство уже выбрано через Adb(self.adb_path, device) на стороне
      Python, эту привязку не нужно (и рискованно, если ИИ ошибётся в
      серийнике) дублировать вручную; после среза "adb" первым словом
      осталось бы "-s", которое ТОЖЕ не top-level-команда — та же ошибка
      "inaccessible or not found", только с другim первым словом."""
    import re
    command = command.strip()
    if not command:
        return command
    if command.lower() == "adb":
        return ""
    match = re.match(r"^adb\s+", command, re.IGNORECASE)
    if match:
        command = command[match.end():]
    match = re.match(r"^(?:-s|--serial)\s+\S+\s*", command, re.IGNORECASE)
    if match:
        command = command[match.end():]
    return command


def split_top_level_command(command: str) -> list[str]:
    """Разбивает top-level adb-команду (см. TOP_LEVEL_COMMANDS) на argv с
    учётом кавычек — питоновский str.split() рвёт по КАЖДОМУ пробелу вслепую,
    что ломает "install -r "C:\\...\\Модель ОД\\...\\file.apk"" (реальный
    путь с пробелом в кириллическом имени модели) на несколько кусков и
    протаскивает кавычку прямо в имя файла (adb потом ругается на
    "filename doesn't end .apk"). shlex(posix=False) не трогает обратные
    слэши (важно для путей Windows — posix-режим воспринял бы их как
    escape-символы), но и не снимает кавычки сам — снимаем вручную."""
    import shlex
    tokens = shlex.split(command, posix=False)
    return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in ('"', "'") else t for t in tokens]


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
            # capture_output=True выше и так уже ловит stdout/stderr на КАЖДЫЙ
            # вызов — раньше это просто никогда не читалось при ошибке, из-за
            # чего в логе (и клиенту, и в присланном на сервер логе установки)
            # был только код возврата и сама команда, без единого слова о
            # РЕАЛЬНОЙ причине (device unauthorized, INSTALL_FAILED_*, нет
            # места на флешке и т.п.) — реальный случай, RuStore не
            # устанавливался ни одним способом, и по логу было невозможно
            # понять почему. Комментарий выше про "стену текста" — про
            # УСПЕШНЫЕ вызовы (там этот код не выполняется вообще), сюда не
            # относится: ошибка и так уже прерывает установку одной строкой,
            # добавить к ней реальную причину — чистый выигрыш, не шум.
            detail = ((result.stdout or "") + (result.stderr or "")).strip()
            message = f"Команда завершилась с ошибкой ({result.returncode}): {' '.join(cmd)}"
            if detail:
                message += f"\n{detail}"
            raise AdbError(message)
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
