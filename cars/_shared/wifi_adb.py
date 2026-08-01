"""Общий хелпер для моделей, где ADB работает по Wi-Fi (adb connect
<ip>:<порт>). Типичная схема на этих ГУ: компьютер подключается к
Wi-Fi-сети самой магнитолы, и IP шлюза по умолчанию для этого
подключения — это и есть IP магнитолы, поэтому его можно не спрашивать у
пользователя, а определить автоматически."""
import subprocess
import sys

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def get_default_gateway() -> str:
    """IP шлюза по умолчанию активного сетевого адаптера."""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
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
