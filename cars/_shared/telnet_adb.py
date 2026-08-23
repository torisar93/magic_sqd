"""Общий хелпер для моделей, где ADB изначально скрыт и включается через
telnet по IPv6 (например Geely CityRay): подключение на [ipv6]:23 и
команда вида "setprop persist.service.adb.button.visible ON".

IPv6-адрес — link-local (fe80::...), поэтому телефон/ПК должен явно
указать, через какой сетевой интерфейс до него достучаться (zone id,
"%..."). Раньше адрес приходилось смотреть руками (например на телефоне
через PingTools) и вводить в ctx.ask_input — теперь enable_adb_via_telnet
сначала пробует найти его сам через scan_ipv6_neighbors (соседи в ARP/NDP-
таблице активного адаптера), ручной ввод остаётся запасным путём, если скан
ничего не нашёл. Зона (ifIndex) подставляется отдельно — на Windows это не
имя интерфейса ("wlan0" — андроидное/линуксовое), а его числовой ifIndex,
поэтому берём ifIndex активного сетевого адаптера (того же, что и для
Wi-Fi ADB в cars/_shared/wifi_adb.py)."""
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

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


def get_active_interface_index() -> str:
    """ifIndex сетевого адаптера с активным подключением (тот же критерий,
    что у wifi_adb.get_default_gateway) — используется как zone id для
    link-local IPv6-адреса на Windows."""
    result = subprocess.run(
        [_powershell_path(), "-NoProfile", "-NonInteractive", "-Command",
         "(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway } "
         "| Select-Object -First 1 -ExpandProperty InterfaceIndex)"],
        capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW,
    )
    index = (result.stdout or "").strip()
    if not index:
        raise RuntimeError(
            "Не удалось определить сетевой адаптер (нет активного подключения с "
            "шлюзом по умолчанию). Подключитесь к Wi-Fi-сети магнитолы и повторите."
        )
    return index


def scan_ipv6_neighbors() -> list[tuple[str, str]]:
    """Link-local IPv6-соседи (fe80::...) активного сетевого адаптера в
    состоянии Reachable/Stale/Permanent (см. Get-NetNeighbor) — кандидаты на
    IP магнитолы, чтобы не искать его вручную (например на телефоне через
    PingTools). Возвращает пары (ip, mac) — MAC-адрес (LinkLayerAddress)
    показывается технику вместе с IP (см. enable_adb_via_telnet), чтобы было
    на что ориентироваться при выборе из списка, раз самого имени хоста
    ("Android", как в PingTools) Windows тут не даёт. Пустой список —
    соседей не нашлось (или адаптер/сеть не определились), вызывающий сам
    решает, что делать дальше."""
    result = subprocess.run(
        [_powershell_path(), "-NoProfile", "-NonInteractive", "-Command",
         "Get-NetNeighbor -AddressFamily IPv6 -ErrorAction SilentlyContinue "
         "| Where-Object { $_.State -in 'Reachable','Stale','Permanent' -and $_.IPAddress -like 'fe80:*' } "
         "| ForEach-Object { \"$($_.IPAddress)|$($_.LinkLayerAddress)\" } "
         "| Select-Object -Unique"],
        capture_output=True, text=True, timeout=15, creationflags=CREATE_NO_WINDOW,
    )
    seen = []
    pairs = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line or line in seen:
            continue
        seen.append(line)
        ip, _, mac = line.partition("|")
        pairs.append((ip.strip(), mac.strip()))
    return pairs


def enable_adb_via_telnet(
    ctx,
    ipv6_address: str | None = None,
    command: str = "setprop persist.service.adb.button.visible ON",
    port: int = 23,
    timeout: int = 10,
) -> None:
    """Подключается по telnet к магнитоле и выполняет command (по умолчанию
    — включает кнопку ADB в настройках Android). ipv6_address — адрес без
    зоны (её подставляем сами) или уже с "%..." — тогда не трогаем. Если не
    задан — сначала пробуем найти сам через scan_ipv6_neighbors и ВСЕГДА
    показываем список технику на подтверждение (даже если найден всего один
    кандидат) — молча выбирать самим рискованно: в сети может быть не одно
    IPv6-устройство, а разница между "той самой магнитолой" и случайным
    соседом на глаз не видна без доп. информации (см. PingTools на
    телефоне, который прямо показывает "Android"). Ни одного кандидата —
    запасной путь: ручной ввод (ctx.ask_input), как раньше."""
    if not ipv6_address:
        candidates = scan_ipv6_neighbors()
        if candidates:
            labels = [f"{ip}  (MAC {mac})" if mac else ip for ip, mac in candidates]
            label_to_ip = dict(zip(labels, (ip for ip, _ in candidates)))
            choice = ctx.ask_choice(
                "Выберите IPv6-адрес магнитолы (найдено в сети):", labels, title="Telnet ADB")
            ipv6_address = label_to_ip.get(choice, choice)
        else:
            ipv6_address = ctx.ask_input(
                "Не нашёл IPv6-соседей в сети. Введите IPv6-адрес магнитолы вручную:",
                title="Telnet ADB")

    host = (ipv6_address or "").strip()
    if not host:
        raise RuntimeError("Не указан IPv6-адрес магнитолы")
    if "%" not in host:
        host = f"{host}%{get_active_interface_index()}"

    ctx.log(f"Подключаюсь по telnet к [{host}]:{port}")
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            time.sleep(0.5)
            _drain(sock)
            sock.sendall(command.encode("ascii") + b"\r\n")
            time.sleep(0.5)
            _drain(sock)
    except OSError as exc:
        raise RuntimeError(f"Не удалось подключиться по telnet к [{host}]:{port}: {exc}") from exc

    ctx.log("Команда отправлена. Кнопка включения ADB должна появиться в настройках Android на магнитоле.")


def _drain(sock: socket.socket) -> None:
    """Вычитывает то, что телнет-демон успел прислать (баннер/эхо), просто
    чтобы не оставлять данные висеть в буфере — содержимое не разбираем."""
    try:
        sock.recv(4096)
    except OSError:
        pass
