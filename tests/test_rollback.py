"""«Откатить в сток» (владелец, 2026-09-25): удалить с магнитолы то, что поставлено в этом запуске этапа
«Приложения». Прошивки, где pm закрыт (Geely OneOS/Monji, Jetour T2 — adb отвечает «error: closed»), —
список «удалите штатно на самой магнитоле». С 1.1.2 удаляет общий код каталога (adb_permissions.remove_for_rollback,
тот же на Android), его нет — встроенный способ."""
from __future__ import annotations
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from app.rollback import MANUAL_REMOVAL, remove_package, rollback

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "cars/_shared"


class FakeAdb:
    def __init__(self, answers):
        self.answers = answers
        self.commands = []

    def shell(self, command, check=True, timeout=120):
        self.commands.append(command)
        package = command.split()[-1]
        text = self.answers.get(package, "Success")
        return SimpleNamespace(stdout=text, stderr="", returncode=0 if text == "Success" else 1)


APPS = [{"package": f"pkg.{n}", "name": f"{n}.apk", "path": f"/apk/{n}.apk"} for n in ("a", "b", "c")]


def test_remove_package_statuses():
    adb = FakeAdb({"pkg.x": "error: closed", "pkg.y": "Failure [DELETE_FAILED_DEVICE_POLICY_MANAGER]"})
    assert remove_package(adb, "pkg.ok") == ("removed", "")
    assert remove_package(adb, "pkg.x")[0] == "blocked"
    assert remove_package(adb, "pkg.y") == ("failed", "Failure [DELETE_FAILED_DEVICE_POLICY_MANAGER]")
    assert adb.commands[0] == "pm uninstall pkg.ok"


def test_rollback_removes_in_reverse_order_and_lists_manual():
    adb = FakeAdb({"pkg.b": "error: closed"})
    log, progress = [], []
    outcome = rollback(adb, APPS, log.append, lambda *args: progress.append(args))
    assert adb.commands == ["pm uninstall pkg.c", "pm uninstall pkg.b", "pm uninstall pkg.a"]
    assert outcome == {"removed": ["pkg.c", "pkg.a"], "manual": ["pkg.b"], "failed": [], "not_connected": False}
    assert any(MANUAL_REMOVAL in line for line in log)
    assert progress[-1] == ("/apk/a.apk", 3, 3, "done")
    assert ("/apk/b.apk", 2, 3, "error") in progress


def test_rollback_stops_trying_when_head_unit_is_gone():
    adb = FakeAdb({"pkg.c": "adb: error: failed to get feature set: device 'X' not found"})
    log = []
    outcome = rollback(adb, APPS, log.append)
    assert adb.commands == ["pm uninstall pkg.c"]  # дальше не пробуем — магнитолы нет
    assert outcome["not_connected"] is True and outcome["failed"] == ["pkg.c", "pkg.b", "pkg.a"]


# --- общий код каталога (с 1.1.2) ---

class ClosedPmAdb:
    """pm uninstall отбит прошивкой («error: closed»), app_process (наш хелпер) отвечает answer; adb push — run()."""

    def __init__(self, answer="Success", closed=("pkg.closed",)):
        self.answer, self.closed, self.commands = answer, set(closed), []

    def shell(self, command, check=True, timeout=120):
        self.commands.append(command)
        if command.startswith("pm uninstall"):
            package = command.split()[-1]
            if package in self.closed:
                return SimpleNamespace(stdout="", stderr="error: closed", returncode=1)
            return SimpleNamespace(stdout="Success", stderr="", returncode=0)
        return SimpleNamespace(stdout=self.answer if "app_process" in command else "", stderr="", returncode=0)

    def run(self, *args, check=True, timeout=120):
        self.commands.append(" ".join(("adb",) + tuple(str(a) for a in args)))
        return SimpleNamespace(stdout="1 file pushed", stderr="", returncode=0)

    def push(self, local, remote, timeout=180):
        self.commands.append(f"push {remote}")


def _shared_module():
    spec = importlib.util.spec_from_file_location("adb_permissions_rollback_test", SHARED / "adb_permissions.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_shared_remove_for_rollback_statuses():
    module = _shared_module()
    adb = ClosedPmAdb()
    ctx = SimpleNamespace(shell=adb.shell, adb=adb.run, log=lambda text: None, shared_dir=SHARED)
    assert module.remove_for_rollback(ctx, "pkg.ok") == "removed"
    assert module.remove_for_rollback(ctx, "pkg.closed") == "removed"  # своим dex-хелпером
    assert "CLASSPATH=/data/local/tmp/msqd_pkg_helper.dex app_process /data/local/tmp MagicSqdPkgHelper pkg.closed" in adb.commands
    adb.answer = "Failure [DELETE_FAILED_INTERNAL_ERROR]"
    assert module.remove_for_rollback(ctx, "pkg.closed") == "blocked"
    failing = SimpleNamespace(shell=lambda c, **kw: SimpleNamespace(stdout="Failure [DELETE_FAILED_DEVICE_POLICY_MANAGER]",
                                                                    stderr=""), adb=adb.run, log=lambda t: None,
                              shared_dir=SHARED)
    assert module.remove_for_rollback(failing, "pkg.x") == "failed: Failure [DELETE_FAILED_DEVICE_POLICY_MANAGER]"


def test_rollback_goes_through_shared_code(monkeypatch):
    monkeypatch.delitem(sys.modules, "adb_permissions", raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    adb, log = ClosedPmAdb(), []
    outcome = rollback(adb, [{"package": "pkg.ok", "name": "ok.apk"}, {"package": "pkg.closed", "name": "closed.apk"}],
                       log.append, shared=SHARED)
    assert outcome == {"removed": ["pkg.closed", "pkg.ok"], "manual": [], "failed": [], "not_connected": False}
    assert any("adb push" in c and "msqd_pkg_helper.dex" in c for c in adb.commands)  # хелпер общего кода
    assert "Удалено: closed.apk" in log


def test_rollback_without_shared_function_uses_builtin_way(monkeypatch, tmp_path):
    monkeypatch.delitem(sys.modules, "adb_permissions", raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    (tmp_path / "adb_permissions.py").write_text("def uninstall_app(ctx, package):\n    pass\n", encoding="utf-8")
    adb, log = ClosedPmAdb(answer="Success"), []
    outcome = rollback(adb, [{"package": "pkg.closed", "name": "closed.apk"}], log.append,
                       helper=SHARED / "msqd_pkg_helper.dex", shared=tmp_path)
    assert outcome["removed"] == ["pkg.closed"]
    assert "push /data/local/tmp/msqd_pkg_helper.dex" in adb.commands  # встроенный путь — тот же хелпер без «uninstall»
