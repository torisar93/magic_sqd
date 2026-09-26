"""Штатные приложения нельзя удалить и отключить (владелец, 2026-09-26). В списках «Удалить приложение» и
«Отключить приложение» — только сторонние; «Включить» — из полного списка, чтобы вернуть отключённое раньше.
Логи: №1013 (ПК — отключили сам «android», pm ответил «new state: disabled-user»), №1042, №757, №1187, №1222.
Защита от ручного ввода — в cars/_shared/adb_permissions.py (tests/test_uninstall_app_result.py)."""
from __future__ import annotations
import re
from pathlib import Path

from app import car_generator as cg

ROOT = Path(__file__).resolve().parents[1]


def _action_bodies(install_py: str) -> dict[str, str]:
    bodies = {}
    for kind in ("uninstall_app", "disable_app", "enable_app"):
        match = re.search(r"    packages = [^\n]*\n    package = ctx\.ask_choice\([^\n]*\n    " + kind + r"\(ctx, package\)",
                          install_py)
        assert match, kind
        bodies[kind] = match.group(0)
    return bodies


def test_generated_lists_hide_stock_apps_for_uninstall_and_disable():
    actions = [cg.ActionSpec(label=label, kind=kind) for label, kind in
               [("Удалить приложения", "uninstall_app"), ("Отключить приложение", "disable_app"),
                ("Включить приложение", "enable_app")]]
    spec = cg.NewCarSpec(brand="Test", model="Model", steps=[cg.StepSpec(type="actions", title="Доп. действия",
                                                                          actions=actions)])
    bodies = _action_bodies(cg._render_install_py(spec))
    assert "packages = list_installed_packages(ctx)\n" in bodies["uninstall_app"]
    assert "packages = list_installed_packages(ctx)\n" in bodies["disable_app"]
    assert "third_party_only=False" in bodies["enable_app"]


def test_android_lists_hide_stock_apps_for_uninstall_and_disable():
    app_js = (ROOT / "android/app/src/main/assets/js/app.js").read_text(encoding="utf-8")
    assert 'uninstall_app: { bridgeMethod: "actions_uninstall_app", thirdPartyOnly: true }' in app_js
    assert 'disable_app: { bridgeMethod: "actions_disable_app", thirdPartyOnly: true }' in app_js
    assert 'enable_app: { bridgeMethod: "actions_enable_app", thirdPartyOnly: false }' in app_js
