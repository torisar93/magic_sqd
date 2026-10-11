"""«Откатить в сток» (владелец, 2026-09-25): после этапа «Приложения» окно итога предлагает удалить с магнитолы
то, что поставлено в этом запуске (InstallContext.installed_apps → событие install_finished, поле installed).
Удаляем в обратном порядке. Прошивки Geely OneOS/Monji, VOLGA/N155 и Jetour T2 не дают удалять через ADB: pm закрыт,
adb отвечает «error: closed» (Android — CLSE, логи #797, #962). С 1.0.51 такие удаляем dex-хелпером через app_process
(app/uninstall_helper.py, логи №1664, №1746); не вышло и так — техник удаляет штатно на самой магнитоле (решение
владельца), программа называет их списком.

С 1.1.2 удаляет общий код каталога — cars/_shared/adb_permissions.py: remove_for_rollback (тот же на Android): правки
удаления дальше приходят с каталогом, без выпуска программы. Нет его (каталог не синхронизирован) — встроенный способ
ниже, с тем же dex-хелпером без слова «uninstall» (app/uninstall_helper.py)."""
from __future__ import annotations
import sys
from pathlib import Path

from .adb_utils import Adb, AdbError
from .install_context import device_unavailable_message
from .uninstall_helper import uninstall_via_helper

MANUAL_REMOVAL = ("эта магнитола не даёт удалять приложения через программу — "
                  "удалите штатно на самой магнитоле (Настройки → Приложения).")


class _SharedCtx:
    """ctx для общего кода каталога поверх Adb — то, что нужно adb_permissions.remove_for_rollback."""

    def __init__(self, adb: Adb, log, shared_dir: Path, helper: Path | None):
        self._adb, self.log, self.shared_dir, self._helper = adb, log, shared_dir, helper

    def shell(self, command, check=True, timeout=120):
        return self._adb.shell(command, check=check, timeout=timeout)

    def adb(self, *args, check=True, timeout=120):
        return self._adb.run(*args, check=check, timeout=timeout)

    def uninstall_via_helper(self, package: str) -> bool:
        if self._helper is None:
            return False
        removed, reason = uninstall_via_helper(
            lambda local, remote: self._adb.push(local, remote),
            lambda command: _output(self._adb.shell(command, check=False, timeout=90)), self._helper, package)
        if not removed:
            self.log(f"dex-хелпер не удалил: {reason}")
        return removed


def _remove_via_shared(adb: Adb, package: str, shared: Path, helper: Path | None, log) -> tuple[str, str] | None:
    """Удалить общим кодом каталога; None — его нет или он не справился сам (тогда встроенный способ)."""
    shared_str = str(shared)
    if shared_str not in sys.path:
        sys.path.insert(0, shared_str)
    try:
        module = __import__("adb_permissions")
    except Exception:  # noqa: BLE001 — нет модуля или он не загрузился: встроенный способ
        return None
    function = getattr(module, "remove_for_rollback", None)
    if not callable(function):
        return None  # каталог старше 1.1.2
    try:
        answer = function(_SharedCtx(adb, log, shared, helper), package)
    except AdbError as exc:
        return "failed", str(exc)
    except Exception as exc:  # noqa: BLE001 — сбой кода с сервера не должен сорвать откат
        log(f"Общий код каталога не сработал ({type(exc).__name__}: {exc}) — удаляю встроенным способом.")
        return None
    if answer in ("removed", "blocked"):
        return answer, ""
    if isinstance(answer, str) and answer.startswith("failed"):
        return "failed", answer.partition(":")[2].strip() or "устройство не ответило"
    return None


def remove_package(adb: Adb, package: str, helper: Path | None = None, log=lambda text: None,
                   shared: Path | None = None) -> tuple[str, str]:
    """pm uninstall одного пакета: ("removed", "") / ("blocked", текст) / ("failed", причина). Сначала — общим кодом
    каталога (shared — cars/_shared), нет его — сами: pm закрыт прошивкой («error: closed») — dex-хелпер (helper —
    cars/_shared/msqd_pkg_helper.dex), и только потом blocked."""
    if shared is not None:
        via_shared = _remove_via_shared(adb, package, shared, helper, log)
        if via_shared is not None:
            return via_shared
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


def rollback(adb: Adb, apps: list[dict], log, on_progress=lambda *args: None, helper: Path | None = None,
             shared: Path | None = None) -> dict:
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
        status, detail = remove_package(adb, package, helper, log, shared)
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
