"""Текст консольных программ Windows: OEM и ANSI вперемешку (format.com) — по строкам, читаемой кодировкой.
Отдельно от usb_utils_win, чтобы проверялось и не на Windows (tests/test_format_console_encoding.py)."""
from __future__ import annotations


def decode_console(data: bytes, oem: str = "cp866", ansi: str = "cp1251") -> str:
    """Вывод format.com: его строки — в OEM (cp866), а системные сообщения, которые он передаёт, бывают в ANSI (cp1251):
    «Указанный диск не существует» выходил «╙ърчрээ√щ фшёъ эх ёє∙хёЄтєхЄ» (ПК 1.1.24, №16206; разбор 14.11.2026).
    Каждую строку — той кодировкой, в которой у неё больше русских букв и меньше псевдографики."""
    def score(text: str) -> int:
        return sum(1 for ch in text if "А" <= ch <= "я" or ch in "Ёё") - 3 * sum(1 for ch in text if "\u2500" <= ch <= "\u259f")
    lines = []
    for raw in data.split(b"\n"):
        variants = [raw.decode(oem, "replace"), raw.decode(ansi, "replace")]
        lines.append(max(variants, key=score))
    return "\n".join(lines)
