"""Консоль ADB: ошибку «переводим» только по первым строкам ответа. Лог №1773 (29.09, Jolion 2026): техник читал кусок
logcat через `cat`, и весь вывод на Android подменился «файл или папка не найдены» — фраза «No such file or directory»
была внутри самого лога. ПК — install_api._translate_console_error; Android — AdbConsoleFormat.translateError (там то же
правило, исходники сверяет tests/test_android_log_review_0930_source.py)."""
from __future__ import annotations
import types

from app.web.api import install_api
from app.web.api.install_api import InstallApi

translate = InstallApi._translate_console_error


def test_short_error_is_translated():
    assert translate("cat: /sdcard/x: No such file or directory") == "Ошибка: файл или папка не найдены по указанному пути."


def test_phrase_deep_inside_long_output_is_not_an_error():
    log = "\n".join(f"09-29 22:30:{i:02d} I/wifi: line {i}" for i in range(10))
    assert translate(log + "\n09-29 22:30:11 E/foo: open /data/x: No such file or directory") is None


def test_real_errors_near_the_top_still_translated():
    am_start = "Starting: Intent { cmp=a/.B }\nError type 3\nError: Activity class {a/a.B} does not exist."
    assert translate(am_start).startswith("Ошибка: указанный компонент")
    trace = "Exception occurred while executing 'grant':\njava.lang.SecurityException: Permission Denial: x\n\tat a.b(c)"
    assert translate(trace).startswith("Ошибка: отказано в доступе")
    assert translate("Failure [DELETE_FAILED_INTERNAL_ERROR]") == "Ошибка: команда отклонена системой (внутренняя ошибка системы)."


def test_long_successful_output_is_shown_without_an_error_line(monkeypatch):
    lines = []
    monkeypatch.setattr(install_api.event_bridge, "push", lambda e: lines.append(e["text"]))
    api = InstallApi.__new__(InstallApi)  # _log_console_result не трогает состояние объекта
    log = "\n".join(f"line {i}" for i in range(10)) + "\nE/foo: open /data/x: No such file or directory"
    api._log_console_result("cat /sdcard/part_aa", types.SimpleNamespace(stdout=log, stderr="", returncode=0))
    assert lines == [log]


def test_error_in_stderr_is_found_even_after_long_stdout(monkeypatch):
    lines = []
    monkeypatch.setattr(install_api.event_bridge, "push", lambda e: lines.append(e["text"]))
    api = InstallApi.__new__(InstallApi)
    stdout = "\n".join(f"line {i}" for i in range(10))
    api._log_console_result("cat a b", types.SimpleNamespace(stdout=stdout, stderr="cat: b: No such file or directory",
                                                             returncode=1))
    assert lines[0] == "Ошибка: файл или папка не найдены по указанному пути." and lines[1:] == [stdout, "cat: b: No such file or directory"]
