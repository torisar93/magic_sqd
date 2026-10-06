"""Кнопка «Вернуть Wi-Fi / ДХО / Arkamys» (kind "restore_wifi"; владелец, 2026-10-06): генератор кладёт отдельную
строку импорта только моделям с этой кнопкой (урок 1.0.24), кнопка спрашивает подтверждение и зовёт
InstallContext.restore_wifi_features; в старой программе без метода — понятное сообщение; Android такую кнопку не
предлагает («Доступно только в версии для Windows»)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.car_generator import ActionSpec, NewCarSpec, StepSpec, _render_install_py

ROOT = Path(__file__).resolve().parents[1]


def _spec(kinds):
    actions = [ActionSpec(label=f"Кнопка {k}", kind=k) for k in kinds]
    return NewCarSpec(brand="Omoda", model="C5", modification="до 2026",
                      steps=[StepSpec(type="actions", title="Дополнительные действия", actions=actions)])


def test_generator_renders_button_with_own_import():
    source = _render_install_py(_spec(["launch_activity", "restore_wifi"]))
    assert "from wifi_restore import restore_wifi_features  # noqa: E402" in source
    assert "def action_step_1_2(ctx):\n    \"\"\"Кнопка restore_wifi.\"\"\"\n    restore_wifi_features(ctx)" in source
    compile(source, "install.py", "exec")
    assert "wifi_restore" not in _render_install_py(_spec(["launch_activity"]))


@pytest.fixture(scope="module")
def wifi_restore():
    spec = importlib.util.spec_from_file_location("wifi_restore_test", ROOT / "cars/_shared/wifi_restore.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Ctx:
    def __init__(self, with_method=True, answer=True):
        self.logs, self.calls, self.asked = [], [], []
        self.answer = answer
        if with_method:
            self.restore_wifi_features = lambda: self.calls.append("restore")

    def log(self, text):
        self.logs.append(text)

    def ask_choice(self, prompt, choices, title="", allow_manual=True):
        self.asked.append((title, list(choices), allow_manual))
        if not self.answer:
            raise RuntimeError("Выбор отменён пользователем.")  # как InstallCancelled программы
        return choices[0]


def test_button_confirms_then_restores(wifi_restore):
    ctx = Ctx()
    wifi_restore.restore_wifi_features(ctx)
    assert ctx.calls == ["restore"]
    assert ctx.asked == [("Вернуть Wi-Fi / ДХО / Arkamys", [wifi_restore.CONFIRM], False)]


def test_cancel_leaves_head_unit_untouched(wifi_restore):
    ctx = Ctx(answer=False)
    with pytest.raises(RuntimeError):
        wifi_restore.restore_wifi_features(ctx)
    assert ctx.calls == []


def test_old_program_gets_clear_message(wifi_restore):
    ctx = Ctx(with_method=False)
    wifi_restore.restore_wifi_features(ctx)
    assert ctx.calls == [] and ctx.asked == [] and "обновите программу" in ctx.logs[-1]


def test_editor_and_android():
    editor = (ROOT / "app/web/frontend/js/screens/car_step_fields.js").read_text(encoding="utf-8")
    assert 'restore_wifi: "Вернуть Wi-Fi / ДХО / Arkamys' in editor
    # Android не знает этот kind — кнопка показывается недоступной («Доступно только в версии для Windows»).
    android = (ROOT / "android/app/src/main/assets/js/app.js").read_text(encoding="utf-8")
    assert "restore_wifi" not in android and "Доступно только в версии для Windows." in android


def test_platform_key_is_public_aosp_test_key():
    """В репозиторий и на сервер кладётся только ОТКРЫТЫЙ тестовый ключ AOSP (магнитолы собраны test-keys)."""
    cert = (ROOT / "cars/_shared/platform_cert/certificate.crt").read_text(encoding="ascii")
    assert cert.startswith("-----BEGIN CERTIFICATE-----")
    assert (ROOT / "cars/_shared/platform_cert/private.pk8").stat().st_size > 1000
