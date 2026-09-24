"""Пароль ADB по QR — встроенным шрифтом, где 0 не похож на O, а I — на l (владелец, 2026-09-24: «техник
из-за этого вводил пароль неправильно»). Раньше пароль шёл системным шрифтом: Segoe UI на Windows и Arial
на Mac (заглавная I и строчная l — одинаковые палочки), Droid Sans Mono на Android (ноль как O). Выбран
JetBrains Mono Bold — у нуля точка, у I засечки, у l хвостик; файл лежит в обоих деревьях (одинаковость
копий — tests/test_shared_frontend_copies.py)."""
from __future__ import annotations

import re
import struct
from pathlib import Path

import pytest

from app.qr_adb_password import _ALPHABET

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "app/web/frontend"
ANDROID = ROOT / "android/app/src/main/assets"
FAMILY = '"MSQD Password"'
FONT_FILE = "JetBrainsMono-Bold.ttf"

# Где пароль выводится: (файл стилей, селектор правила).
PASSWORD_RULES = [
    (DESKTOP / "css/usb06.css", ".usb06-code"),                         # карточка этапа на ПК
    (DESKTOP / "css/usb06.css", ".app-modal-code"),                     # окно, если «Скопировать» не сработало
    (ANDROID / "css/usb06.css", ".usb-password-result .qr-adb-code"),  # карточка этапа на Android
]


def _declarations(css: str, selector: str) -> dict[str, str]:
    """Объявления правила, в списке селекторов которого есть selector (CSS тут без вложенности)."""
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selectors = [part.strip() for part in re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.S).split(",")]
        if selector in selectors:
            return {name.strip(): value.strip() for name, _, value in
                    (item.partition(":") for item in match.group(2).split(";") if ":" in item)}
    raise AssertionError(f"правило {selector} не найдено")


def _font_face(css: str) -> dict[str, str]:
    for match in re.finditer(r"@font-face\s*\{([^{}]*)\}", css):
        body = match.group(1)
        if FAMILY in body:
            return {name.strip(): value.strip() for name, _, value in
                    (item.partition(":") for item in body.split(";") if ":" in item)}
    raise AssertionError(f"@font-face {FAMILY} не найден")


def _cmap(font: bytes) -> set[int]:
    """Кодовые точки из таблицы cmap (Windows Unicode BMP, формат 4) — без fontTools."""
    tables = {}
    for i in range(struct.unpack(">H", font[4:6])[0]):
        tag, _, offset, _ = struct.unpack(">4sIII", font[12 + 16 * i:28 + 16 * i])
        tables[tag] = offset
    cmap = tables[b"cmap"]
    for i in range(struct.unpack(">H", font[cmap + 2:cmap + 4])[0]):
        platform, encoding, sub = struct.unpack(">HHI", font[cmap + 4 + 8 * i:cmap + 12 + 8 * i])
        start = cmap + sub
        if (platform, encoding) == (3, 1) and struct.unpack(">H", font[start:start + 2])[0] == 4:
            seg2 = struct.unpack(">H", font[start + 6:start + 8])[0]
            ends = struct.unpack(f">{seg2 // 2}H", font[start + 14:start + 14 + seg2])
            starts = struct.unpack(f">{seg2 // 2}H", font[start + 16 + seg2:start + 16 + 2 * seg2])
            return {code for first, last in zip(starts, ends) if first != 0xFFFF for code in range(first, last + 1)}
    raise AssertionError("в шрифте нет таблицы cmap формата 4")


@pytest.mark.parametrize("css_path, selector", PASSWORD_RULES)
def test_password_is_shown_with_the_bundled_font(css_path, selector):
    rule = _declarations(css_path.read_text(encoding="utf-8"), selector)
    assert rule["font-family"].startswith(FAMILY), rule["font-family"]
    face = _font_face(css_path.read_text(encoding="utf-8"))
    # Тот же вес, что у файла шрифта, и без «дорисованной» жирности — иначе браузер утолщает буквы сам.
    assert rule["font-weight"] == face["font-weight"] == "700"
    assert rule["font-synthesis"] == "none"


@pytest.mark.parametrize("css_path", [DESKTOP / "css/usb06.css", ANDROID / "css/usb06.css"])
def test_font_face_points_to_the_bundled_file(css_path):
    face = _font_face(css_path.read_text(encoding="utf-8"))
    url = re.search(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", face["src"]).group(1)
    font = (css_path.parent / url).resolve()
    assert font.name == FONT_FILE and font.is_file(), font
    assert (font.parent / "JetBrainsMono-OFL.txt").is_file(), "OFL требует класть лицензию рядом со шрифтом"


def test_font_has_every_password_character():
    missing = [ch for ch in _ALPHABET if ord(ch) not in _cmap((DESKTOP / "fonts" / FONT_FILE).read_bytes())]
    assert not missing, f"в шрифте нет символов пароля: {missing}"


def test_android_legacy_rule_names_the_same_font():
    # style.css — старое правило .qr-adb-code (перекрывается usb06.css); пусть не расходится с ним.
    rule = _declarations((ANDROID / "css/style.css").read_text(encoding="utf-8"), ".qr-adb-code")
    assert rule["font-family"].startswith(FAMILY)


def test_copy_fallback_shows_the_password_in_the_same_font():
    wizard = (DESKTOP / "js/screens/stage_wizard.js").read_text(encoding="utf-8")
    assert wizard.count("window.notice(codeEl.textContent, {title:'Пароль ADB', code:true})") == 2
    modal = (DESKTOP / "js/components/modal.js").read_text(encoding="utf-8")
    assert 'messageEl.classList.toggle("app-modal-code", code)' in modal
    # Остальные окна переиспользуют тот же <p> и обязаны снимать класс.
    assert modal.count('messageEl.classList.remove("app-modal-code")') == 3
