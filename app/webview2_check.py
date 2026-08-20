"""Проверка наличия WebView2 Runtime — без него окно pywebview открывается
пустым белым: сама программа (WinForms) создаёт окно нормально, а вот
встроенный Chromium-рендерер (msedgewebview2.exe) не запускается вовсе, ни
один JS в этом окне не выполняется. Ни startup.log (webview.start() и
create_window() отрабатывают без исключений), ни debug_all.log (в нём
просто нет вызовов моста, потому что до них не доходит) эту ситуацию не
ловят как ошибку — реальный случай на машине техника без WebView2,
разбирались через удалённый доступ и Диспетчер задач (msedgewebview2.exe
не появлялся вовсе).

Установщик (installer.iss/admin_installer.iss) уже сам молча ставит
WebView2 при установке/обновлении — эта проверка на стороне самого
приложения нужна как подстраховка для уже установленных копий БЕЗ этого
шага (старые версии) и для машин, где сам инсталлятор не смог поставить
рантайм (нет интернета в момент установки и т.п.): без неё техник просто
увидел бы пустое окно без единой подсказки, что не так.

winreg — стандартная библиотека, без сторонних зависимостей и без
обращения к самому pywebview/WebView2 (которых как раз может не быть)."""
import subprocess
import sys
import winreg
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0

# GUID клиента WebView2 в Edge Update — см. официальную документацию
# Microsoft (Detect if a WebView2 Runtime is already installed,
# learn.microsoft.com/microsoft-edge/webview2/concepts/distribution).
_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
_MACHINE_KEY = rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{_CLIENT_GUID}"
_USER_KEY = rf"Software\Microsoft\EdgeUpdate\Clients\{_CLIENT_GUID}"


def is_installed() -> bool:
    """per-machine (HKLM) или per-user (HKCU) — проверяем оба, у техника не
    всегда есть права администратора на момент установки WebView2."""
    for hive, key in ((winreg.HKEY_LOCAL_MACHINE, _MACHINE_KEY),
                       (winreg.HKEY_CURRENT_USER, _USER_KEY)):
        try:
            with winreg.OpenKey(hive, key) as k:
                value, _ = winreg.QueryValueEx(k, "pv")
                if value and value != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def install_silently(bootstrapper_path: Path, timeout: int = 180) -> bool:
    """Запускает официальный WebView2 Runtime Bootstrapper (см.
    tools/MicrosoftEdgeWebview2Setup.exe, ~2 МБ — качает подходящую под
    архитектуру машины версию рантайма с серверов Microsoft, поэтому нужен
    интернет) и ждёт завершения. Возвращает True, если после этого
    WebView2 обнаруживается в реестре — реальный результат, а не просто
    "процесс завершился с кодом 0"."""
    if not bootstrapper_path.exists():
        return False
    try:
        subprocess.run(
            [str(bootstrapper_path), "/silent", "/install"],
            timeout=timeout, creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    return is_installed()
