"""ПК: магнитолы нет — этап сразу заканчивается понятным окном, а не перебором способов установки
(владелец, 2026-09-24: «после no device программа не должна продолжать попытки ставить»).

Логи #734, #736, #737, #786, #789: техник ответил «Продолжить всё равно» без выбранной магнитолы, и
каждое приложение проходило все 8 способов с «no devices/emulators found»; у Haval Jolion 2026 первый
способ (JDWP) при этом писал «магнитола не даёт pidof?», теряя настоящую причину."""
from __future__ import annotations
import subprocess
import threading
from types import SimpleNamespace

import pytest

from app import runner as runner_module
from app.adb_utils import AdbError
from app.install_context import (InstallCancelled, InstallContext, INSTALL_METHOD_KEYS, check_device,
                                 device_unavailable_message)
from app.runner import InstallRunner
from app.web.api.install_api import InstallApi


class _FakeAdb:
    """Ответ `adb get-state` без настоящего adb."""

    def __init__(self, returncode=0, stdout="", stderr="", error=None):
        self.result = subprocess.CompletedProcess(["adb", "get-state"], returncode, stdout, stderr)
        self.error = error
        self.calls = []

    def run(self, *args, **kwargs):
        self.calls.append(args)
        if self.error:
            raise self.error
        return self.result


@pytest.mark.parametrize("stderr, expected", [
    ("error: no devices/emulators found", "Магнитола не подключена"),
    ("error: device 'R58N12ABCDE' not found", "Магнитола не подключена"),
    ("error: device offline", "Магнитола не подключена"),
    ("error: device unauthorized.\nThis adb server's $ADB_VENDOR_KEYS is not set", "Разрешить отладку"),
])
def test_check_device_explains_missing_head_unit(stderr, expected):
    message = check_device(_FakeAdb(returncode=1, stderr=stderr))
    assert message and expected in message
    assert "(adb: " in message  # сырая причина остаётся в тексте — для лога и разбора


@pytest.mark.parametrize("fake", [
    _FakeAdb(returncode=0, stdout="device\n"),
    # Стартовал adb-сервер — лишние строки в stderr, но код 0: магнитола на связи.
    _FakeAdb(returncode=0, stdout="device\n", stderr="* daemon not running; starting now at tcp:5037\n"),
    # Непонятная ошибка (несколько устройств, нет adb) — не останавливаем: дальше покажет своё обычный путь.
    _FakeAdb(returncode=1, stderr="error: more than one device/emulator"),
    _FakeAdb(error=AdbError("adb.exe не найден (C:\\tools\\adb.exe).")),
])
def test_check_device_does_not_block_when_unit_is_there_or_unknown(fake):
    assert check_device(fake) is None


def test_lost_during_install_is_worded_as_disconnect():
    text = "adb.exe: device 'R58N12ABCDE' not found"
    assert device_unavailable_message(text).startswith("Магнитола не подключена")
    assert device_unavailable_message(text, during=True).startswith("Магнитола отключилась во время установки")
    assert device_unavailable_message("Failure [INSTALL_FAILED_INVALID_APK]") is None


def _ctx(tmp_path, log, names=("a.apk", "b.apk"), **kwargs):
    apks = []
    for name in names:
        (tmp_path / name).write_bytes(b"")
        apks.append(tmp_path / name)
    return InstallContext(adb_path="fake-adb", device_serial="R58N12ABCDE", model_dir=tmp_path, selected_apks=apks,
                          log_fn=log.append, cancel_flag=threading.Event(), shared_dir=None, **kwargs)


def test_no_head_unit_means_no_install_attempt_at_all(tmp_path):
    log = []
    ctx = _ctx(tmp_path, log)
    ctx._adb = _FakeAdb(returncode=1, stderr="adb.exe: no devices/emulators found")
    ctx.install_apk_auto = lambda *a, **k: pytest.fail("без магнитолы установка не должна начинаться")

    with pytest.raises(InstallCancelled, match="Магнитола не подключена"):
        ctx.install_selected_apks()
    assert not any("Установка APK" in line for line in log)


def test_confirmed_head_unit_is_not_checked_again(tmp_path):
    log, installed = [], []
    ctx = _ctx(tmp_path, log, device_confirmed=True)
    ctx._adb = _FakeAdb(error=AssertionError("уже проверено перед этапом — второй get-state не нужен"))
    ctx.install_apk_auto = lambda path, extra_args=None: installed.append(path.name)
    ctx._after_app_installed = lambda apk, mock: None

    ctx.install_selected_apks()
    assert installed == ["a.apk", "b.apk"]


def test_jdwp_first_method_keeps_the_real_adb_reason(tmp_path):
    # Haval Jolion 2026: JDWP-способ первый. Без магнитолы он не должен выдавать «не даёт pidof?» и
    # отдавать очередь остальным 7 способам.
    log = []
    ctx = _ctx(tmp_path, log, names=("launcher.apk",),
               preferred_install_method="jdwp_whitelist", device_confirmed=True)
    ctx._maybe_resign = lambda path: path
    tried = []
    real_install_with_method = ctx._install_with_method

    def install_with_method(method, path, extra_args):
        tried.append(INSTALL_METHOD_KEYS[method])
        return real_install_with_method(method, path, extra_args)

    ctx._install_with_method = install_with_method
    ctx.shell = lambda command, **kwargs: subprocess.CompletedProcess(
        ["adb", "shell", command], 1, "", "adb.exe: no devices/emulators found")
    import app.install_context as install_context
    original = install_context.read_package_name
    install_context.read_package_name = lambda path: "com.appindustry.everywherelauncher"
    try:
        with pytest.raises(InstallCancelled, match="отключилась во время установки"):
            ctx.install_apk_auto(tmp_path / "launcher.apk")
    finally:
        install_context.read_package_name = original
    assert tried == ["jdwp_whitelist"]
    assert any("no devices/emulators found" in line for line in log)


def _runner(tmp_path, finished):
    return InstallRunner("fake-adb", on_log=lambda line: None, on_finished=lambda ok, msg: finished.append((ok, msg)),
                         base_dir=tmp_path)


def test_runner_stops_before_downloads_and_commands_when_no_head_unit(tmp_path, monkeypatch):
    finished, calls = [], []
    monkeypatch.setattr(runner_module, "check_device", lambda adb: "Магнитола не подключена — установка не начиналась.")
    monkeypatch.setattr(runner_module, "ensure_apks_downloaded", lambda *a, **k: calls.append("download"))
    model = SimpleNamespace(dir=tmp_path)
    runner = _runner(tmp_path, finished)
    runner.start(model, None, [], run_fn=lambda ctx: calls.append("run"), require_device=True)
    runner._thread.join(5)
    assert calls == []
    assert finished == [(False, "Магнитола не подключена — установка не начиналась.")]


def test_runner_turns_adb_no_device_into_plain_words(tmp_path, monkeypatch):
    finished = []
    monkeypatch.setattr(runner_module, "check_device", lambda adb: None)
    monkeypatch.setattr(InstallRunner, "sync_resign_cert", lambda self, model, check_cancelled=None: None)

    def run_fn(ctx):
        raise AdbError(r"Команда завершилась с ошибкой (1): C:\adb.exe -s X shell pm list packages"
                       "\nadb.exe: device 'X' not found")

    runner = _runner(tmp_path, finished)
    runner.start(SimpleNamespace(dir=tmp_path), "X", [], run_fn=run_fn, own_dirs=[], require_device=True,
                 skip_sync=True)
    runner._thread.join(5)
    assert len(finished) == 1 and finished[0][0] is False
    assert finished[0][1].startswith("Магнитола отключилась во время установки")


def _with_connect(fn):  # как в сгенерированном stages.py моделей «весь ADB по Wi-Fi»
    def wrapped(ctx):
        fn(ctx)
    return wrapped


def _adb_step(ctx):
    pass


@pytest.mark.parametrize("stage, run_fn, expected", [
    ({"type": "apps"}, None, True),
    ({"type": "actions"}, None, True),
    ({"type": "adb"}, _adb_step, True),
    ({"type": "adb"}, _with_connect(_adb_step), False),  # подключается по Wi-Fi сам, внутри этапа
    ({"type": "uart"}, _adb_step, False),
    ({"type": "telnet"}, _adb_step, False),
])
def test_which_stages_need_a_connected_head_unit(stage, run_fn, expected):
    assert InstallApi._needs_device(stage, run_fn) is expected
