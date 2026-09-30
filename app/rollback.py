"""«Откатить в сток» (владелец, 2026-09-25): после этапа «Приложения» окно итога предлагает удалить с магнитолы
то, что поставлено в этом запуске (InstallContext.installed_apps → событие install_finished, поле installed).
Удаляем в обратном порядке. Прошивки Geely OneOS/Monji, VOLGA/N155 и Jetour T2 не дают удалять через ADB: pm закрыт,
adb отвечает «error: closed» (Android — CLSE, логи #797, #962). С 1.0.51 такие удаляем dex-хелпером через app_process
(app/uninstall_helper.py, логи №1664, №1746); не вышло и так — техник удаляет штатно на самой магнитоле (решение
владельца), программа называет их списком."""
from __future__ import annotations
from pathlib import Path

from .adb_utils import Adb, AdbError
from .install_context import device_unavailable_message
from .uninstall_helper import uninstall_via_helper

MANUAL_REMOVAL = ("эта магнитола не даёт удалять приложения через программу — "
                  "удалите штатно на самой магнитоле (Настройки → Приложения).")


def remove_package(adb: Adb, package: str, helper: Path | None = None, log=lambda text: None) -> tuple[str, str]:
    """pm uninstall одного пакета: ("removed", "") / ("blocked", текст) / ("failed", причина). pm закрыт прошивкой
    («error: closed») — пробуем dex-хелпер (helper — cars/_shared/uninstall_helper.dex), и только потом blocked."""
    try:
        result = adb.shell(f"pm uninstall {package}", check=False, timeout=60)
    except AdbError as exc:
        return "failed", str(exc)
    text = ((result.stdout or "") + (result.stderr or "")).strip()
    lower = text.lower()
    if "success" in lower and "failure" not in lower:
        return "removed", ""
    if "error: closed" in lower:
        if helper is None:
            return "blocked", text
        log("Магнитола не пускает pm uninstall — удаляю через dex-хелпер...")
        try:
            removed, reason = uninstall_via_helper(
                lambda local, remote: adb.push(local, remote),
                lambda command: _output(adb.shell(command, check=False, timeout=90)), helper, package)
        except AdbError as exc:
            return "failed", str(exc)
        if removed:
            return "removed", ""
        log(f"dex-хелпер не удалил: {reason}")
        return "blocked", reason
    return "failed", text or "устройство не ответило"


def _output(result) -> str:
    return (result.stdout or "") + (result.stderr or "")


def rollback(adb: Adb, apps: list[dict], log, on_progress=lambda *args: None, helper: Path | None = None) -> dict:
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
        status, detail = remove_package(adb, package, helper, log)
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
