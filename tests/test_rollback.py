"""«Откатить в сток» (владелец, 2026-09-25): удалить с магнитолы то, что поставлено в этом запуске этапа
«Приложения». Прошивки, где pm закрыт (Geely OneOS/Monji, Jetour T2 — adb отвечает «error: closed»), —
список «удалите штатно на самой магнитоле»."""
from __future__ import annotations
from types import SimpleNamespace

from app.rollback import MANUAL_REMOVAL, remove_package, rollback


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
