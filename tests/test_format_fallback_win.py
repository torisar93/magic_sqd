"""Форматирование флешки на Windows: Format-Volume отвечал «Invalid Parameter» на части флешек (логи №3915, №5100,
№8339 — 29–58 ГБ, после магнитолы, все три ФС) и не видел тома у части картридеров (№3755, №7806) — теперь запасной
способ format.com, как «Форматировать…» в Проводнике. Модуль Windows-only: ctypes.WinDLL подменяется."""
from __future__ import annotations
import ctypes
import importlib
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

POWERSHELL_ERROR = ("Format-Volume : Invalid Parameter\nActivity ID: {598d0d9c}\n+ Format-Volume -DriveLetter F ...\n"
                    "    + FullyQualifiedErrorId : StorageWMI 5,Format-Volume.\n")


@pytest.fixture()
def win(monkeypatch):
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: mock.MagicMock(), raising=False)
    sys.modules.pop("app.usb_utils_win", None)
    module = importlib.import_module("app.usb_utils_win")
    module._kernel32.GetOEMCP.return_value = 866
    monkeypatch.setattr(module, "assert_safe_to_format", lambda letter, base_dir: None)
    monkeypatch.setattr(module, "find_powershell_path", lambda: "powershell.exe")
    yield module
    sys.modules.pop("app.usb_utils_win", None)


def _run(answers, calls):
    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        code, out, err = answers.pop(0)
        return subprocess.CompletedProcess(cmd, code, out, err)
    return run


def test_format_volume_success_needs_no_fallback(win, monkeypatch):
    calls = []
    monkeypatch.setattr(win.subprocess, "run", _run([(0, "", "")], calls))
    log = []
    win.format_drive("F:", "NTFS", "X50+ Belgee", Path("C:/app"), log.append)
    assert len(calls) == 1 and calls[0][0][0] == "powershell.exe"
    assert "[Console]::OutputEncoding = [Text.Encoding]::UTF8" in calls[0][0][-1]
    assert log == ["Форматирование F:\\ в NTFS...", "Форматирование завершено."]


def test_invalid_parameter_falls_back_to_format_com(win, monkeypatch):
    calls = []
    monkeypatch.setattr(win.subprocess, "run", _run([(1, "", POWERSHELL_ERROR), (0, "Форматирование завершено.\n", "")],
                                                    calls))
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    log = []
    win.format_drive("F:", "NTFS", "X50+ Belgee", Path("C:/app"), log.append)
    cmd, kwargs = calls[1]
    assert cmd[0].replace("/", "\\").endswith(r"System32\format.com")
    assert cmd[1:] == ["F:", "/FS:NTFS", "/Q", "/V:X50Belgee", "/X", "/Y"]
    assert kwargs["input"].startswith("\r\n") and kwargs["encoding"] == "cp866"
    assert log == ["Форматирование F:\\ в NTFS...",
                   "Format-Volume не справился (Format-Volume : Invalid Parameter) — форматирую командой format...",
                   "Форматирование завершено."]


def test_filesystem_names_for_format_com(win, monkeypatch):
    for fs, expected in (("exFAT", "/FS:exFAT"), ("FAT32", "/FS:FAT32")):
        calls = []
        monkeypatch.setattr(win.subprocess, "run", _run([(1, "", POWERSHELL_ERROR), (0, "", "")], calls))
        win._kernel32.GetDiskFreeSpaceExW.side_effect = None  # объём 0 — проверка «FAT32 до 32 ГБ» проходит
        win.format_drive("G:", fs, "", Path("C:/app"))
        assert calls[1][0][2] == expected and calls[1][0][4] == "/V:CARINSTALL"


def test_both_failing_report_both(win, monkeypatch):
    monkeypatch.setattr(win.subprocess, "run", _run(
        [(1, "", POWERSHELL_ERROR), (4, "Тип файловой системы: RAW.\nДиск защищён от записи.\n", "")], []))
    with pytest.raises(RuntimeError) as error:
        win.format_drive("F:", "NTFS", "", Path("C:/app"))
    assert str(error.value) == ("Не удалось отформатировать F:\\: Format-Volume : Invalid Parameter; "
                                "format: Диск защищён от записи.")


def test_wrapped_russian_powershell_error_is_joined(win):
    """Вывод ВМ Windows 11 (рус.) с UTF-8: сообщение перенесено по ширине консоли посреди слова."""
    text = ('Format-Volume : Не найдены объекты MSFT_Volume со свойством "DriveLetter", равным "Q". Проверьте значение '
            'свойства и по\nпробуйте еще раз.\nстрока:1 знак:52\n+ ... Format-Volume -DriveLetter Q\n')
    assert win._first_error_line(text) == ('Format-Volume : Не найдены объекты MSFT_Volume со свойством "DriveLetter", '
                                           'равным "Q". Проверьте значение свойства и попробуйте еще раз.')
    assert win._first_error_line(POWERSHELL_ERROR) == "Format-Volume : Invalid Parameter"
