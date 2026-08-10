"""Общий хелпер для моделей, где ADB изначально скрыт и включается через
telnet по IPv6 (например Geely CityRay): подключение на [ipv6]:23 и
команда вида "setprop persist.service.adb.button.visible ON".

IPv6-адрес — link-local (fe80::...), поэтому телефон/ПК должен явно
указать, через какой сетевой интерфейс до него достучаться (zone id,
"%..."). Пользователь копирует из настроек магнитолы только сам адрес, без
зоны — эта зона имеет смысл только на устройстве, с которого выполняется
подключение, и на Windows это не имя интерфейса ("wlan0" — андроидное/линуксовое
имя), а его числовой ifIndex, поэтому подставляем ifIndex активного
сетевого адаптера (того же, что берёт для Wi-Fi ADB cars/_shared/wifi_adb.py)."""
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


def enable_adb_via_telnet(
    ctx,
    ipv6_address: str,
    command: str = "setprop persist.service.adb.button.visible ON",
    port: int = 23,
    timeout: int = 10,
) -> None:
    """Подключается по telnet к магнитоле и выполняет command (по умолчанию
    — включает кнопку ADB в настройках Android). ipv6_address — адрес без
    зоны (её подставляем сами) или уже с "%..." — тогда не трогаем."""
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
