"""Свой способ установки модели (владелец, 2026-10-06: «чтобы обновлений приложения стало поменьше»):
apps_install_method "py:модуль.функция" — функция(ctx, apk, remote) из cars/_shared ставит APK сама, на ПК
(app/install_context.py) и на Android (InstallEngine.kt → py_runner.py). Только этот способ, без перебора остальных;
его отказ — понятный итог; после установки — те же разрешения, что и после встроенных способов."""
from __future__ import annotations

import importlib
import json
import re
import sys
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import install_context
from app.install_context import InstallCancelled, InstallContext, parse_py_install_method

ROOT = Path(__file__).resolve().parents[1]
PKG = "com.example.app"
MODULE = "unit_install_test_mod"
SOURCE = '''
CALLS = []

def install(ctx, apk, remote=None):
    CALLS.append((str(apk), remote))
    ctx.push(apk, "/data/local/tmp/msqd.apk")
    result = ctx.shell("pm install -r /data/local/tmp/msqd.apk", check=False)
    if "Success" not in (result.stdout or ""):
        raise RuntimeError("магнитола не приняла: " + (result.stdout or "").strip())

def broken(ctx, apk, remote=None):
    raise RuntimeError("на этой магнитоле так не выйдет")
'''


@pytest.mark.parametrize("value,expected", [
    ("py:unit.install", ("unit", "install")),
    ("  py:unit.install  ", ("unit", "install")),
    ("py:unit", None), ("py:unit._x", None), ("py:1unit.f", None), ("localinstall", None), ("", None), (None, None),
    ("py:a.b.c", None),
])
def test_parse(value, expected):
    assert parse_py_install_method(value) == expected


def test_same_pattern_on_android_and_in_editor():
    pc = install_context._PY_METHOD_RE.pattern
    assert pc == r"^py:([A-Za-z]\w*)\.([A-Za-z]\w*)$"
    engine = (ROOT / "android/app/src/main/java/ru/magicsqd/mobile/usb/InstallEngine.kt").read_text(encoding="utf-8")
    assert 'Regex("^py:([A-Za-z]\\\\w*)\\\\.([A-Za-z]\\\\w*)$")' in engine
    editor = (ROOT / "app/web/frontend/js/screens/car_step_fields.js").read_text(encoding="utf-8")
    assert r"/^py:([A-Za-z]\w*\.[A-Za-z]\w*)$/" in editor


class FakeDevice:
    def __init__(self, pm_answer="Success"):
        self.commands = []
        self.pm_answer = pm_answer

    def run(self, *args, check=True, timeout=120):
        self.commands.append(args)
        text = ""
        if args[0] == "shell" and args[1].startswith("pm install"):
            text = self.pm_answer
        return SimpleNamespace(stdout=text, stderr="", returncode=0)


def _apk(tmp_path, name="app.apk"):
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("AndroidManifest.xml", b"manifest")
        zf.writestr("classes.dex", b"dex")
    return path


@pytest.fixture
def make_ctx(tmp_path, monkeypatch):
    shared = tmp_path / "_shared"
    shared.mkdir()
    (shared / f"{MODULE}.py").write_text(SOURCE, encoding="utf-8")
    monkeypatch.setattr(install_context, "read_package_name", lambda path: PKG)
    monkeypatch.delitem(sys.modules, MODULE, raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))  # _load_shared_module добавит cars/_shared — откатим

    def make(device, method, apks=None):
        apks = apks if apks is not None else [_apk(tmp_path)]
        log = []
        ctx = InstallContext(adb_path="fake-adb", device_serial="HU1", model_dir=tmp_path, selected_apks=apks,
                             log_fn=log.append, cancel_flag=threading.Event(), shared_dir=shared,
                             preferred_install_method=method, device_confirmed=True)
        ctx._adb.run = device.run
        ctx.sleep = lambda seconds: None
        granted = []
        ctx._grant_all_permissions_if_available = granted.append
        ctx._same_apk_already_installed = lambda apk: False
        ctx.test = SimpleNamespace(log=log, granted=granted)
        return ctx

    yield make
    sys.modules.pop(MODULE, None)


def test_model_method_installs_and_grants(make_ctx):
    device = FakeDevice()
    ctx = make_ctx(device, f"py:{MODULE}.install")
    ctx.install_selected_apks()
    assert ctx.failed_apps == []
    assert ("push",) == device.commands[0][:1] and device.commands[0][2] == "/data/local/tmp/msqd.apk"
    assert device.commands[1] == ("shell", "pm install -r /data/local/tmp/msqd.apk")
    assert not any(c[0] == "install" for c in device.commands)  # встроенные способы не пробовались
    assert ctx.test.granted == [PKG]  # разрешения — как после встроенных способов
    assert sys.modules[MODULE].CALLS[0][1] is None  # на ПК файл заранее не залит


def test_model_method_failure_is_the_result(make_ctx):
    device = FakeDevice(pm_answer="Failure [INSTALL_FAILED_INTERNAL_ERROR]")
    ctx = make_ctx(device, f"py:{MODULE}.install")
    with pytest.raises(InstallCancelled) as exc:
        ctx.install_selected_apks()
    assert "«app.apk» не установлено" in str(exc.value) and "магнитола не приняла" in str(exc.value)
    assert not any(c[0] == "install" for c in device.commands)
    assert any("свой способ модели" in line for line in ctx.test.log)


def test_missing_function_is_clear(make_ctx):
    ctx = make_ctx(FakeDevice(), f"py:{MODULE}.nope")
    with pytest.raises(InstallCancelled) as exc:
        ctx.install_selected_apks()
    assert f"нет функции {MODULE}.nope" in str(exc.value)


def test_after_first_success_only_that_app_is_skipped(make_ctx, tmp_path):
    device = FakeDevice()
    ctx = make_ctx(device, f"py:{MODULE}.install", apks=[_apk(tmp_path, "a.apk"), _apk(tmp_path, "b.apk")])
    answers = iter(["Success", "Failure [INSTALL_FAILED_INTERNAL_ERROR]"])
    original = device.run

    def run(*args, **kwargs):
        if args[0] == "shell" and args[1].startswith("pm install"):
            device.pm_answer = next(answers)
        return original(*args, **kwargs)

    ctx._adb.run = run
    ctx.install_selected_apks()
    assert len(ctx.failed_apps) == 1 and "b.apk" in ctx.failed_apps[0]


def test_unknown_method_in_old_way_still_iterates(make_ctx):
    """Как увидит такой ключ версия без «py:» — обычный перебор (здесь: ключ, который не разбирается)."""
    device = FakeDevice()
    ctx = make_ctx(device, "py:not valid")
    ctx.install_selected_apks()
    assert device.commands[0][0] == "install"  # adb install — первый встроенный способ


def test_same_function_runs_on_android_ctx(monkeypatch, tmp_path):
    """Тот же модуль — через py_runner телефона (поддельный мост из tests/test_py_runner.py)."""
    from app import code_signing
    from tests.test_py_runner import FakeBridge, TEST_PUBLIC, TEST_SECRET, ANDROID_PY, _ANDROID_MODULES
    shared = tmp_path / "_shared"
    shared.mkdir()
    (shared / f"{MODULE}.py").write_text(SOURCE, encoding="utf-8")
    (shared / f"{MODULE}.py.sig").write_text(code_signing.sign(TEST_SECRET, f"{MODULE}.py", (shared / f"{MODULE}.py").read_bytes()),
                                             encoding="ascii")
    saved = {name: sys.modules.pop(name) for name in _ANDROID_MODULES if name in sys.modules}
    monkeypatch.delitem(sys.modules, MODULE, raising=False)
    sys.path.insert(0, str(ANDROID_PY))
    try:
        runner = importlib.import_module("py_runner")
        monkeypatch.setattr(sys.modules["code_signing"], "PUBLIC_KEY", TEST_PUBLIC)
        bridge = FakeBridge({"pm install -r /data/local/tmp/msqd.apk": ("Success", 0)})
        apk = _apk(tmp_path)
        result = json.loads(runner.run(bridge, str(shared), MODULE, "install",
                                       json.dumps([str(apk), "/data/local/tmp/app.apk"])))
        assert result == {"ok": True, "result": None}
        assert bridge.commands == ["push app.apk /data/local/tmp/msqd.apk", "pm install -r /data/local/tmp/msqd.apk"]
        failed = runner.call(bridge, shared, MODULE, "broken", [str(apk), None])
        assert failed["ok"] is False and failed["unavailable"] is False and "так не выйдет" in failed["error"]
    finally:
        sys.path.remove(str(ANDROID_PY))
        for name in _ANDROID_MODULES:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
