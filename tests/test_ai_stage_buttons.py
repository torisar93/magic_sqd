"""ИИ-мастер на Android: кнопки этапов флешки и «Пароль ADB по QR-коду» видны ИИ как action:N с подписями (разбор
переписки 07.11.2026: ИИ их не видел и на этапе пароля жал «Далее» вместо «Получить пароль»; Jolion 2026, Atlas Monji)."""
from __future__ import annotations
import re
from pathlib import Path

APP_JS = Path(__file__).resolve().parents[1] / "android/app/src/main/assets/js/app.js"


def _fn(js: str, name: str) -> str:
    return re.search(rf"function {name}\(.*?\n  }}\n", js, flags=re.S).group(0)


def test_stage_card_buttons_are_actions_with_labels():
    js = APP_JS.read_text(encoding="utf-8")
    buttons = _fn(js, "aiActionButtons")
    assert '".flow-action-card"' in buttons and '".usb-stage > .usb-step-card"' in buttons
    label = _fn(js, "aiButtonLabel")
    assert 'closest(".usb-step-card, .flow-action-card")' in label and 'querySelector("h3")' in label
    assert "if (action) return aiButtonLabel(aiActionButtons()[Number(action[1])]);" in _fn(js, "aiLabel")
    state = _fn(js, "aiState")
    assert "aiActionButtons().map((button, i) => (button ? `action:${i} — ${aiButtonLabel(button)}` : \"\"))" in state
