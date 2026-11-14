"""ПК: вывод format.com — OEM и ANSI вперемешку; «Указанный диск не существует» выходил кракозябрами (№16206)."""
from __future__ import annotations

import pytest

from app import console_text as usb_win


def test_each_line_gets_the_readable_encoding():
    data = "Тип файловой системы: FAT32.\r\n".encode("cp866") + "Указанный диск не существует.\r\n".encode("cp1251")
    text = usb_win.decode_console(data)
    assert "Тип файловой системы: FAT32." in text and "Указанный диск не существует." in text
    assert "╙" not in text


def test_plain_ascii_untouched():
    assert usb_win.decode_console(b"Format complete.\r\n").startswith("Format complete.")
