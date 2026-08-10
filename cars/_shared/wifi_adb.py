"""Общий хелпер для моделей, где ADB работает по Wi-Fi (adb connect
<ip>:<порт>). Типичная схема на этих ГУ: компьютер подключается к
Wi-Fi-сети самой магнитолы, и IP шлюза по умолчанию для этого
подключения — это и есть IP магнитолы, поэтому его можно не спрашивать у
пользователя, а определить автоматически."""
import os
import subprocess
import sys
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


def get_default_gateway() -> str:
    """IP шлюза по умолчанию активного сетевого адаптера."""
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


def connect_wifi(ctx, port: int, ip: str | None = None) -> str:
    """adb connect <ip>:<port>. Если ip не задан — берётся IP шлюза
    активного сетевого адаптера (см. get_default_gateway)."""
    ip = ip or get_default_gateway()
    ctx.log(f"Подключаюсь по Wi-Fi ADB: {ip}:{port}")
    ctx.adb("connect", f"{ip}:{port}")
    return ip


def open_android_settings(ctx):
    """Открыть системные настройки Android на магнитоле."""
    ctx.shell("am start -a android.settings.SETTINGS", check=False)
