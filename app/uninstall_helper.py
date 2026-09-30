"""Удаление приложения dex-хелпером — когда магнитола не пускает «pm uninstall» (adb отвечает «error: closed»:
VOLGA/Geely N155, Geely OneOS/Monji — логи №797, №962, №1664, №1746; «Откатить в сток» упирался в «удалите
штатно»). Хелпер наш: исходник и сборка — helpers/uninstall_helper/, готовый файл — cars/_shared/uninstall_helper.dex.
Запуск через app_process от имени shell — тот же путь, что у хелпера установки dex_shell_helper.dex, его прошивка
пропускает. Проверено на эмуляторе Android 9: установленное — «Success», штатное — «Failure [DELETE_FAILED_…]».
Android — AdbPermissions.kt: removeViaHelper."""
from __future__ import annotations
import re
from pathlib import Path
from typing import Callable

HELPER_NAME = "uninstall_helper.dex"
REMOTE_HELPER = "/data/local/tmp/uninstall_helper.dex"
ENTRY_CLASS = "MagicSqdUninstaller"
_PACKAGE_RE = re.compile(r"[A-Za-z0-9_.]+")


def uninstall_via_helper(push: Callable[[str, str], object], shell: Callable[[str], str], helper: Path,
                         package: str) -> tuple[bool, str]:
    """push(локальный, на устройстве), shell(команда) → текст ответа. (True, "") — удалено, иначе (False, причина)."""
    if not _PACKAGE_RE.fullmatch(package or ""):
        return False, f"странное имя пакета: {package!r}"
    if not helper.is_file():
        return False, f"нет файла {HELPER_NAME} — обновите каталог"
    push(str(helper), REMOTE_HELPER)
    shell(f"chmod 644 {REMOTE_HELPER}")
    text = (shell(f"CLASSPATH={REMOTE_HELPER} app_process /data/local/tmp {ENTRY_CLASS} {package}") or "").strip()
    shell(f"rm -f {REMOTE_HELPER}")
    if "success" in text.lower() and "failure" not in text.lower():
        return True, ""
    return False, text or "хелпер не ответил"
