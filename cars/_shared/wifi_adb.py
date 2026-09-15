"""Общий хелпер для моделей, где ADB работает по Wi-Fi (adb connect
<ip>:<порт>). Два способа найти IP магнитолы:

1. IP шлюза по умолчанию — работает, когда компьютер подключается к
   Wi-Fi-сети САМОЙ магнитолы (типичная схема на большинстве таких ГУ,
   см. get_default_gateway).
2. Скан локальной подсети на открытый порт (см. scan_for_adb_hosts) — нужен
   для обратного случая: магнитола сама подключается к сети/точке доступа
   НОУТБУКА, и тогда её IP заранее неизвестен (шлюз в этой схеме — сам
   ноутбук/роутер, а не магнитола).

connect_wifi пробует способ 1, и только если он не сработал — способ 2 с
выбором из найденного (плюс ручной ввод всегда доступен рядом, на случай
если скан не нашёл нужное устройство)."""
from __future__ import annotations
import concurrent.futures
import ipaddress
import os
import socket
import subprocess
import sys
from pathlib import Path

import mdns_scan  # см. mdns_scan.py — тот же каталог cars/_shared/, обычный import по sys.path

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def _powershell_path() -> str:
    """Полный путь к powershell.exe вместо голого имени — на части машин
    (ограниченный PATH, сторонний софт, переписавший переменную окружения)
    subprocess.run(["powershell", ...]) падает с [WinError 2] "Не удается
    найти указанный файл", хотя powershell.exe стоит штатно (независимая
    копия той же логики, что и app/adb_utils.py:find_powershell_path — этот
    файл подгружается отдельно из cars/_shared, без доступа к app/)."""
    windir = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(windir) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.exists() else "powershell"


def _default_gateway_and_iface_mac() -> tuple[str, str] | None:
    """macOS/BSD-аналог Get-NetIPConfiguration — 'route -n get default'
    печатает и шлюз, и интерфейс активного маршрута по умолчанию отдельными
    строками ("gateway: 192.168.1.1", "interface: en0")."""
    try:
        result = subprocess.run(
            ["route", "-n", "get", "default"], capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    gateway = iface = None
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("gateway:"):
            gateway = line.split(":", 1)[1].strip()
        elif line.startswith("interface:"):
            iface = line.split(":", 1)[1].strip()
    return (gateway, iface) if gateway and iface else None


def get_default_gateway() -> str:
    """IP шлюза по умолчанию активного сетевого адаптера."""
    if sys.platform != "win32":
        info = _default_gateway_and_iface_mac()
        if info is None:
            raise RuntimeError(
                "Не удалось определить IP магнитолы (шлюз по умолчанию). "
                "Убедитесь, что компьютер подключён к Wi-Fi-сети магнитолы."
            )
        return info[0]
    result = subprocess.run(
        [_powershell_path(), "-NoProfile", "-NonInteractive", "-Command",
         "(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } "
         "| Select-Object -First 1 -ExpandProperty IPv4DefaultGateway).NextHop"],
        capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW,
    )
    ip = (result.stdout or "").strip()
    if not ip:
        raise RuntimeError(
            "Не удалось определить IP магнитолы (шлюз по умолчанию). "
            "Убедитесь, что компьютер подключён к Wi-Fi-сети магнитолы."
        )
    return ip


def _get_local_ipv4_and_subnet() -> tuple[str, ipaddress.IPv4Network] | None:
    """IP и подсеть активного сетевого адаптера (тот же критерий, что у
    get_default_gateway). Подсеть не крупнее /24 — даже если у адаптера
    маска шире, сканировать десятки тысяч адресов незачем и слишком долго,
    а сети точек доступа/хотспотов на таких магнитолах и так почти всегда
    /24."""
    if sys.platform != "win32":
        info = _default_gateway_and_iface_mac()
        if info is None:
            return None
        _, iface = info
        try:
            result = subprocess.run(["ifconfig", iface], capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired):
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
            prefix = max(bin(int(netmask_hex, 16)).count("1"), 24)
            network = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
        except ValueError:
            return None
        return ip, network

    result = subprocess.run(
        [_powershell_path(), "-NoProfile", "-NonInteractive", "-Command",
         "$c = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } "
         "| Select-Object -First 1; "
         "if ($c) { \"$($c.IPv4Address.IPAddress)/$($c.IPv4Address.PrefixLength)\" }"],
        capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW,
    )
    output = (result.stdout or "").strip()
    if not output:
        return None
    try:
        iface = ipaddress.ip_interface(output)
    except ValueError:
        return None
    prefix = max(iface.network.prefixlen, 24)
    network = ipaddress.ip_network(f"{iface.ip}/{prefix}", strict=False)
    return str(iface.ip), network


def scan_for_adb_hosts(port: int, timeout: float = 0.25) -> list[str]:
    """Параллельно проверяет, у каких хостов локальной подсети открыт port
    (обычно 5555/7777 — ADB по Wi-Fi), возвращает их IP по возрастанию.
    Пустой список — либо подсеть не определилась, либо никто на неё не
    ответил (не значит, что метод сломан — вызывающий сам решает, что
    делать дальше, обычно предложить ввести IP вручную)."""
    info = _get_local_ipv4_and_subnet()
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
    """Реальный список живых хостов подсети через системный ping — НЕ
    зависит от того, какой (если вообще какой-то) порт у них открыт, в
    отличие от scan_for_adb_hosts (см. её докстринг). Устройство может
    ответить на ping, даже если ADB/порт сейчас недоступен по любой причине
    (тумблер "Беспроводная отладка" временно выключен и т.п.) — так же, как
    NetworkScan.pingSweep дополняет scanSubnetForPort на Android (объединение
    результатов, а не пересечение — см. scan_network_for_wifi_adb ниже)."""
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
    """Объединённый список кандидатов для диалога подключения по Wi-Fi ADB
    (см. app/web/frontend/js/refinement05.js: LabUI.connection — элементы
    списка либо голая строка-IP (порт неизвестен — предлагается тот, что
    уже введён в поле), либо {"host":.., "port":..} с уже готовым портом).
    Как и на Android (см. android/.../WebBridge.kt:scanHosts) — источники
    объединяются, а не пересекаются: часть хостов фильтрует ICMP, но
    отвечает на TCP-connect (и наоборот), а mDNS-резолв "android.local"/
    "_adb-tls-connect._tcp" не зависит ни от одного из двух. Резолв
    "_adb-tls-connect._tcp" даёт РЕАЛЬНЫЙ порт "Беспроводной отладки"
    (Android 11+, переназначается при каждом включении тумблера — угадать
    его сканом по одному конкретному порту нельзя, только через mDNS).
    Кандидаты, подтверждённые через mDNS (это ТОЧНО Android, не просто
    "что-то ответило на пинг") идут первыми и с "recommended": True —
    фронтенд подсвечивает их отдельно, как раньше подсвечивался хост с
    открытым портом 5555."""
    ip_candidates: set[str] = set()

    info = _get_local_ipv4_and_subnet()
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


def _try_connect(ctx, ip: str, port: int) -> bool:
    """adb connect почти всегда возвращает код 0 даже при неудаче (пишет
    "unable to connect"/"failed to connect" в вывод, но не падает) —
    поэтому успех определяется по тексту вывода, а не по коду возврата."""
    ctx.log(f"Подключаюсь по Wi-Fi ADB: {ip}:{port}")
    result = ctx.adb("connect", f"{ip}:{port}", check=False)
    output = ((result.stdout or "") + (result.stderr or "")).lower()
    return "connected to" in output or "already connected" in output


def connect_wifi(ctx, port: int, ip: str | None = None) -> str:
    """adb connect <ip>:<port>. Если ip не задан — сначала пробует IP шлюза
    (см. get_default_gateway), а если не подключилось — сканирует локальную
    подсеть (см. scan_for_adb_hosts) и предлагает выбрать найденное через
    ctx.ask_choice (пункт "ввести вручную" в этом диалоге есть всегда,
    независимо от результатов скана)."""
    if ip:
        if not _try_connect(ctx, ip, port):
            raise RuntimeError(f"Не удалось подключиться к {ip}:{port}")
        return ip

    try:
        gateway_ip = get_default_gateway()
    except RuntimeError:
        gateway_ip = None
    if gateway_ip and _try_connect(ctx, gateway_ip, port):
        return gateway_ip

    ctx.log("Автоподключение по шлюзу не удалось — сканирую локальную сеть...")
    candidates = scan_for_adb_hosts(port)
    if candidates:
        ctx.log(f"Найдены устройства с открытым портом {port}: {', '.join(candidates)}")
    else:
        ctx.log(f"Не нашёл в сети устройств с открытым портом {port}.")
    ip = ctx.ask_choice(f"Выберите IP магнитолы (порт {port}):", candidates, title="Wi-Fi ADB")

    if not _try_connect(ctx, ip, port):
        raise RuntimeError(f"Не удалось подключиться к {ip}:{port}")
    return ip


def open_android_settings(ctx):
    """Открыть системные настройки Android на магнитоле."""
    ctx.shell("am start -a android.settings.SETTINGS", check=False)
