"""«Откатить в сток» (владелец, 2026-09-25): после этапа «Приложения» окно итога предлагает удалить с магнитолы
то, что поставлено в этом запуске (InstallContext.installed_apps → событие install_finished, поле installed).
Удаляем в обратном порядке. Прошивки Geely OneOS/Monji и Jetour T2 не дают удалять через ADB: pm закрыт,
adb отвечает «error: closed» (Android — CLSE, логи #797, #962) — такие приложения техник удаляет штатно
на самой магнитоле (решение владельца), программа называет их списком."""
from __future__ import annotations

from .adb_utils import Adb, AdbError
from .install_context import device_unavailable_message

MANUAL_REMOVAL = ("эта магнитола не даёт удалять приложения через программу — "
                  "удалите штатно на самой магнитоле (Настройки → Приложения).")


def remove_package(adb: Adb, package: str) -> tuple[str, str]:
    """pm uninstall одного пакета: ("removed", "") / ("blocked", текст) / ("failed", причина)."""
    try:
        result = adb.shell(f"pm uninstall {package}", check=False, timeout=60)
    except AdbError as exc:
        return "failed", str(exc)
    text = ((result.stdout or "") + (result.stderr or "")).strip()
    lower = text.lower()
    if "success" in lower and "failure" not in lower:
        return "removed", ""
    if "error: closed" in lower:
        return "blocked", text
    return "failed", text or "устройство не ответило"


def rollback(adb: Adb, apps: list[dict], log, on_progress=lambda *args: None) -> dict:
    """Удаляет apps ({"package", "name", "path"}) в обратном порядке установки. on_progress(путь, готово,
    всего, состояние) — очередь окна отката. Итог — списки ИМЁН ПАКЕТОВ removed/manual/failed (названия
    окно берёт из своего списка). Магнитола пропала — дальше не пробуем: остальное в failed."""
    queue = list(reversed(apps))
    outcome = {"removed": [], "manual": [], "failed": [], "not_connected": False}
    log(f"Откат в сток: удаляю приложения, поставленные сейчас ({len(queue)}).")
    for index, app in enumerate(queue):
        package, path = app.get("package", ""), app.get("path", "")
        name = app.get("name") or package
        if outcome["not_connected"]:
            outcome["failed"].append(package)
            on_progress(path, index + 1, len(queue), "error")
            continue
        on_progress(path, index, len(queue), "running")
        log(f"Удаляю приложение: {package}")
        status, detail = remove_package(adb, package)
        if status == "removed":
            log(f"Удалено: {name}")
            outcome["removed"].append(package)
            on_progress(path, index + 1, len(queue), "done")
            continue
        if status == "blocked":
            log(f"Не удалось удалить {name}: {MANUAL_REMOVAL}")
            outcome["manual"].append(package)
        else:
            gone = device_unavailable_message(detail, during=True)
            log(f"Не удалось удалить {name}: {gone or detail}")
            outcome["failed"].append(package)
            outcome["not_connected"] = bool(gone)
        on_progress(path, index + 1, len(queue), "error")
    return outcome
