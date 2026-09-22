"""Маркер "#log <команда>" в мини-DSL ADB-команд ("adb"/"actions" этапы) —
добавлен для диагностических кнопок, которые технику нужно просто нажать, а
результат (сырой stdout+stderr) должен попасть в лог установки, который он
потом отправит через "Сообщить о проблеме" (см. cars/Haval/"Jolion 2026
test" — диагностика включения Wi-Fi на Desay x9h без кодировки через ELM).

Обычный "shell" (строка без маркера) молчит на успехе — осознанный выбор
против "стены текста" на длинных цепочках рутинных команд (см. adb_utils.py:
Adb.run(), InstallEngine.kt: "shell" -> AdbShellResult.Output -> {}). "#log"
— явное исключение из этого правила, отдельный kind, а не флаг у "shell".

Три места должны согласованно понимать этот маркер (тот же паттерн разъезда
платформ, что уже ловился в этом проекте — см. project_belgee_data_apk_addition
в памяти): desktop-парсер (car_generator.py, пишет install.py), Android-порт
парсера (wizard_spec.py, тот же мини-DSL, другой рантайм) и сам
InstallContext.shell_log (что вызванный код реально делает на десктопе)."""
from __future__ import annotations
import importlib.util
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.car_generator import _parse_adb_line, _render_command_body
from app.install_context import InstallContext

ROOT = Path(__file__).resolve().parent.parent
WIZARD_SPEC_PATH = ROOT / "android/app/src/main/python/wizard_spec.py"


@pytest.fixture(scope="module")
def wizard_spec():
    """См. tests/test_android_wizard_spec_usb_apks.py — тот же приём импорта
    файла вне обычных Python-пакетов (Chaquopy source root, не app/)."""
    spec = importlib.util.spec_from_file_location("android_wizard_spec_shell_log", WIZARD_SPEC_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --- desktop: car_generator.py -------------------------------------------

def test_parse_adb_line_recognizes_log_marker():
    assert _parse_adb_line("#log getprop | grep -i wifi") == ("shell_log", "getprop | grep -i wifi")


def test_parse_adb_line_log_marker_case_insensitive_and_trims():
    assert _parse_adb_line("#LOG   dumpsys wifi  ") == ("shell_log", "dumpsys wifi")


def test_parse_adb_line_plain_shell_unaffected():
    """Регрессия: обычная строка (без маркера) по-прежнему разбирается как
    молчаливый "shell", #log не должен подхватывать всё подряд."""
    assert _parse_adb_line("dumpsys wifi") == ("shell", "dumpsys wifi")


def test_render_command_body_emits_shell_log_call():
    lines = _render_command_body(["#log dumpsys wifi"], "actions_1_1")
    body = "\n".join(lines)
    assert "ctx.shell_log('dumpsys wifi')" in body
    assert "ctx.shell(" not in body  # не должно случайно попасть в обычный shell()


def test_render_command_body_shell_log_supports_ask_substitution():
    """Как и обычный "shell", "#log" должен поддерживать {ask} — тот же
    _ask, что заполняет предыдущая "#ask" строка в том же списке команд."""
    lines = _render_command_body(["#ask Введите IP", "#log ping {ask}"], "actions_1_1")
    body = "\n".join(lines)
    assert "ctx.ask_input('Введите IP')" in body
    assert "ctx.shell_log('ping {ask}'.replace('{ask}', str(_ask)))" in body


def test_render_command_body_regression_plain_shell_still_silent():
    """Регрессия: обычная (без #log) строка по-прежнему рендерится в
    ctx.shell(..., check=False) без изменений в поведении."""
    lines = _render_command_body(["dumpsys wifi"], "actions_1_1")
    assert lines == ["    _ask = None", "    ctx.shell('dumpsys wifi', check=False)"]


# --- Android: wizard_spec.py (тот же мини-DSL, другой рантайм) -----------

def test_android_parse_adb_line_recognizes_log_marker(wizard_spec):
    assert wizard_spec.parse_adb_line("#log getprop | grep -i wifi") == {
        "kind": "shell_log", "command": "getprop | grep -i wifi",
    }


def test_android_parse_adb_line_plain_shell_unaffected(wizard_spec):
    assert wizard_spec.parse_adb_line("dumpsys wifi") == {"kind": "shell", "command": "dumpsys wifi"}


def test_android_and_desktop_parsers_agree_on_log_marker(wizard_spec):
    """Оба порта должны разбирать один и тот же #log так же (тот же класс
    бага, что уже был найден на usb-этапах standard_apks_optional — платформы
    незаметно расходятся, если один порт забыли обновить)."""
    line = "#log pm list packages | grep -i wifi"
    desktop_kind, desktop_payload = _parse_adb_line(line)
    android = wizard_spec.parse_adb_line(line)
    assert desktop_kind == "shell_log"
    assert android == {"kind": "shell_log", "command": desktop_payload}


# --- InstallContext.shell_log --------------------------------------------

def _make_ctx(tmp_path, log):
    return InstallContext(
        adb_path="fake-adb",
        device_serial="fake-device",
        model_dir=tmp_path,
        selected_apks=[],
        log_fn=log.append,
        cancel_flag=threading.Event(),
        shared_dir=None,
    )


def test_shell_log_writes_command_and_output_to_log(tmp_path):
    log = []
    ctx = _make_ctx(tmp_path, log)
    ctx.shell = lambda command, **kwargs: SimpleNamespace(stdout="Wi-Fi is disabled\n", stderr="", returncode=0)

    ctx.shell_log("dumpsys wifi")

    assert log == ["$ dumpsys wifi", "Wi-Fi is disabled"]


def test_shell_log_combines_stdout_and_stderr(tmp_path):
    log = []
    ctx = _make_ctx(tmp_path, log)
    ctx.shell = lambda command, **kwargs: SimpleNamespace(stdout="line1\n", stderr="warning: x\n", returncode=0)

    ctx.shell_log("some-command-with-a-warning")

    assert log == ["$ some-command-with-a-warning", "line1\nwarning: x"]


def test_shell_log_reports_empty_output_explicitly(tmp_path):
    log = []
    ctx = _make_ctx(tmp_path, log)
    ctx.shell = lambda command, **kwargs: SimpleNamespace(stdout="", stderr="", returncode=0)

    ctx.shell_log("echo -n")

    assert log == ["$ echo -n", "(пусто)"]


def test_shell_log_does_not_raise_on_nonzero_exit(tmp_path):
    """check=False по умолчанию — команда, вернувшая ненулевой код (например
    grep без совпадений), не должна прерывать остальные кнопки/команды."""
    log = []
    ctx = _make_ctx(tmp_path, log)
    seen_kwargs = {}

    def fake_shell(command, **kwargs):
        seen_kwargs.update(kwargs)
        return SimpleNamespace(stdout="", stderr="", returncode=1)

    ctx.shell = fake_shell
    ctx.shell_log("getprop | grep -i nonexistent_key_xyz")

    assert seen_kwargs.get("check") is False
