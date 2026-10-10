"""Общий Python-код каталога на Android (android/.../python/py_runner.py + code_signing.py; владелец, 2026-10-06:
«чтобы обновлений приложения стало поменьше»): «#py модуль.функция» исполняет ту же функцию из cars/_shared, что и ПК,
поверх Kotlin-примитивов (здесь — поддельный мост с теми же методами, что PyCtxBridge.kt). Код с сервера — только с
подписью разработчика, и вызываемый модуль, и его импорты из _shared; стандартную библиотеку не подменить."""
from __future__ import annotations

import importlib
import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from app import code_signing as pc_code_signing
from app import ed25519

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"
TEST_SECRET = bytes(range(32))
TEST_PUBLIC = ed25519.public_key(TEST_SECRET)
_ANDROID_MODULES = ("py_runner", "code_signing", "ed25519", "ui_bundle", "wizard_spec")


def test_android_copy_is_identical():
    assert (ROOT / "app/code_signing.py").read_bytes() == (ANDROID_PY / "code_signing.py").read_bytes(), (
        "app/code_signing.py и android/.../python/code_signing.py разошлись — правьте обе копии")


@pytest.fixture
def android(monkeypatch):
    """Модули Android-части (py_runner и его code_signing/ed25519/ui_bundle — модули верхнего уровня, как в Chaquopy)."""
    saved = {name: sys.modules.pop(name) for name in _ANDROID_MODULES if name in sys.modules}
    # Тесты ПК импортируют cars/_shared/*.py как обычные модули — на телефоне их в sys.modules нет.
    for path in (ROOT / "cars/_shared").glob("*.py"):
        monkeypatch.delitem(sys.modules, path.stem, raising=False)
    sys.path.insert(0, str(ANDROID_PY))
    try:
        runner = importlib.import_module("py_runner")
        monkeypatch.setattr(sys.modules["code_signing"], "PUBLIC_KEY", TEST_PUBLIC)
        yield runner
    finally:
        sys.path.remove(str(ANDROID_PY))
        for name in _ANDROID_MODULES:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


class FakeBridge:
    """Те же методы, что у PyCtxBridge.kt. shell отвечает по таблице: команда (или её начало) → (вывод, код)."""

    def __init__(self, replies=None, answers=None):
        self.replies = dict(replies or {})
        self.answers = list(answers or [])
        self.logs = []
        self.commands = []
        self.asks = []
        self.cancel = False
        self.helper_removed = []
        self.fail = None

    def _reply(self, command):
        for key, value in self.replies.items():
            if command == key or command.startswith(key):
                return value
        return "", 0

    def shell(self, command, timeout_ms):
        assert command.endswith("\necho __MSQD_RC=$?"), command
        command = command[: -len("\necho __MSQD_RC=$?")]
        self.commands.append(command)
        if self.fail:
            return "\x00fail:" + self.fail
        output, code = self._reply(command)
        if code is None:  # прошивка закрыла поток — метки нет
            return output
        return f"{output}\n__MSQD_RC={code}" if output else f"__MSQD_RC={code}"

    def service(self, name):
        self.commands.append(name)
        return self.replies.get(name, ("", 0))[0]

    def push(self, local, remote):
        self.commands.append(f"push {Path(local).name} {remote}")
        return None

    def pull(self, remote, local):
        return None

    def installApk(self, path):  # noqa: N802 — имя метода Kotlin
        self.commands.append(f"install {Path(path).name}")
        return None

    def reboot(self, wait, timeout_ms):
        self.commands.append(f"reboot wait={wait}")
        return None

    def waitForDevice(self, timeout_ms):  # noqa: N802
        return True

    def ask(self, prompt, title, choices_json, allow_manual):
        self.asks.append((prompt, title, json.loads(choices_json), allow_manual))
        return self.answers.pop(0) if self.answers else None

    def log(self, text):
        self.logs.append(text)

    def isCancelled(self):  # noqa: N802
        return self.cancel

    def isConnected(self):  # noqa: N802
        return True

    def progress(self, done, total):
        self.logs.append(f"progress {done}/{total}")

    def uninstallViaHelper(self, package):  # noqa: N802
        self.helper_removed.append(package)
        return True

    def optimizeForMotion(self, package):  # noqa: N802
        return None


def put(shared: Path, name: str, source: str, *, sign: bool = True, secret: bytes = TEST_SECRET) -> Path:
    shared.mkdir(parents=True, exist_ok=True)
    path = shared / name
    path.write_text(source, encoding="utf-8")
    if sign:
        (shared / f"{name}.sig").write_text(pc_code_signing.sign(secret, name, path.read_bytes()), encoding="ascii")
    return path


def test_signature_format_matches_on_both_sides(android):
    data = b"def f(ctx):\n    pass\n"
    sig = pc_code_signing.sign(TEST_SECRET, "mod.py", data)
    android_signing = sys.modules["code_signing"]
    assert android_signing.verify("mod.py", data, sig, TEST_PUBLIC)
    assert not android_signing.verify("other.py", data, sig, TEST_PUBLIC)  # подпись привязана к имени файла
    assert not android_signing.verify("mod.py", data + b"#", sig, TEST_PUBLIC)
    from app import ui_bundle
    assert pc_code_signing.PUBLIC_KEY == ui_bundle.PUBLIC_KEY  # тот же ключ разработчика, что у бандлов интерфейса


def test_signed_function_runs_with_ctx(android, tmp_path):
    shared = tmp_path / "_shared"
    put(shared, "tool.py", "def run(ctx, package, mode):\n"
                           "    out = ctx.shell(f'pm path {package}', check=False)\n"
                           "    ctx.log(f'{mode}: {out.stdout.strip()} rc={out.returncode}')\n"
                           "    return {'ok': out.returncode == 0}\n")
    bridge = FakeBridge({"pm path com.a": ("package:/data/app/a.apk", 0)})
    result = json.loads(android.run(bridge, str(shared), "tool", "run", json.dumps(["com.a", "x"])))
    assert result == {"ok": True, "result": {"ok": True}}
    assert bridge.logs == ["x: package:/data/app/a.apk rc=0"]


@pytest.mark.parametrize("case", ["unsigned", "other_key", "tampered", "missing"])
def test_code_without_valid_signature_is_not_executed(android, tmp_path, case):
    shared = tmp_path / "_shared"
    marker = tmp_path / "ran.txt"
    source = f"open({str(marker)!r}, 'w').write('ran')\ndef run(ctx):\n    pass\n"
    if case != "missing":
        path = put(shared, "tool.py", source, sign=case != "unsigned", secret=bytes(32) if case == "other_key" else TEST_SECRET)
        if case == "tampered":
            path.write_text(source + "# подмена\n", encoding="utf-8")
    else:
        shared.mkdir()
    result = android.call(FakeBridge(), shared, "tool", "run")
    assert result["ok"] is False and result["unavailable"] is True
    assert ("подпис" in result["error"]) or ("нет файла" in result["error"])
    assert not marker.exists()


def test_imports_from_shared_need_signature_too(android, tmp_path):
    shared = tmp_path / "_shared"
    put(shared, "tool.py", "import helper\ndef run(ctx):\n    return helper.VALUE\n")
    put(shared, "helper.py", "VALUE = 42\n", sign=False)
    result = android.call(FakeBridge(), shared, "tool", "run")
    assert result["ok"] is False and result["unavailable"] is True and "helper.py" in result["error"]
    put(shared, "helper.py", "VALUE = 42\n")
    assert android.call(FakeBridge(), shared, "tool", "run") == {"ok": True, "result": 42}
    assert "helper" not in sys.modules and "tool" not in sys.modules  # после вызова не остаются


def test_stdlib_and_app_modules_are_not_shadowed(android, tmp_path):
    shared = tmp_path / "_shared"
    put(shared, "json.py", "def dumps(*a, **k):\n    return 'ПОДМЕНА'\n")
    put(shared, "tool.py", "import json\ndef run(ctx):\n    return json.dumps([1])\n")
    assert android.call(FakeBridge(), shared, "tool", "run") == {"ok": True, "result": "[1]"}
    # Модуль с именем модуля приложения не исполняется вместо него.
    result = android.call(FakeBridge(), shared, "json", "dumps")
    assert result["ok"] is False and result["unavailable"] is True


def test_fresh_module_on_every_call(android, tmp_path):
    shared = tmp_path / "_shared"
    put(shared, "tool.py", "def run(ctx):\n    return 1\n")
    assert android.call(FakeBridge(), shared, "tool", "run")["result"] == 1
    put(shared, "tool.py", "def run(ctx):\n    return 2\n")  # каталог обновился, приложение не перезапускалось
    assert android.call(FakeBridge(), shared, "tool", "run")["result"] == 2


@pytest.mark.parametrize("module,function", [("tool", "_private"), ("to.ol", "run"), ("tool", "missing"),
                                             ("", "run")])
def test_bad_names_and_missing_functions(android, tmp_path, module, function):
    shared = tmp_path / "_shared"
    put(shared, "tool.py", "def run(ctx):\n    pass\ndef _private(ctx):\n    pass\n")
    result = android.call(FakeBridge(), shared, module, function)
    assert result["ok"] is False and result["unavailable"] is True


def test_shell_exit_codes_check_and_link_loss(android, tmp_path):
    shared = tmp_path / "_shared"
    put(shared, "tool.py", "def soft(ctx):\n    r = ctx.shell('false', check=False)\n    return [r.returncode, r.stdout]\n"
                           "def hard(ctx):\n    ctx.shell('false')\n")
    bridge = FakeBridge({"false": ("нет такого", 1)})
    assert android.call(bridge, shared, "tool", "soft") == {"ok": True, "result": [1, "нет такого"]}
    result = android.call(bridge, shared, "tool", "hard")
    assert result["ok"] is False and result["unavailable"] is False and "ошибкой (1)" in result["error"]
    assert any("Подробности для разработчика" in line for line in bridge.logs)
    bridge = FakeBridge()
    bridge.fail = "связь с магнитолой потеряна"
    result = android.call(bridge, shared, "tool", "soft")
    assert result["ok"] is False and "связь с магнитолой потеряна" in result["error"]


def test_ask_choice_and_cancel(android, tmp_path):
    shared = tmp_path / "_shared"
    put(shared, "tool.py", "def run(ctx):\n    return ctx.ask_choice('Какое?', ['a', 'b'], title='Выбор приложения')\n")
    bridge = FakeBridge(answers=["b"])
    assert android.call(bridge, shared, "tool", "run") == {"ok": True, "result": "b"}
    assert bridge.asks == [("Какое?", "Выбор приложения", ["a", "b"], True)]
    result = android.call(FakeBridge(answers=[""]), shared, "tool", "run")
    assert result["ok"] is False and result["cancelled"] is True


def test_cancel_stops_sleep(android, tmp_path):
    shared = tmp_path / "_shared"
    put(shared, "tool.py", "def run(ctx):\n    ctx.sleep(30)\n")
    bridge = FakeBridge()
    bridge.cancel = True
    result = android.call(bridge, shared, "tool", "run")
    assert result["cancelled"] is True and "Остановлено" in result["error"]


def test_files_of_the_stage(android, tmp_path):
    shared = tmp_path / "_shared"
    apk = tmp_path / "dl" / "gboard.apk"
    apk.parent.mkdir()
    apk.write_bytes(b"apk")
    put(shared, "tool.py", "def run(ctx):\n    ctx.install_apk(ctx.file('actions_3_2/gboard.apk'))\n"
                           "    ctx.push(ctx.file('gboard.apk'), '/sdcard/x.apk')\n")
    bridge = FakeBridge()
    result = json.loads(android.run(bridge, str(shared), "tool", "run", "[]", "", json.dumps({"gboard.apk": str(apk)})))
    assert result["ok"] is True
    assert bridge.commands == ["install gboard.apk", "push gboard.apk /sdcard/x.apk"]


# --- настоящий cars/_shared/adb_permissions.py (его исполняет ПК) — на ctx телефона ------------------------------


@pytest.fixture
def real_shared(tmp_path):
    shared = tmp_path / "_shared"
    shared.mkdir()
    for name in ("adb_permissions.py",):
        data = (ROOT / "cars/_shared" / name).read_bytes()
        (shared / name).write_bytes(data)
        (shared / f"{name}.sig").write_text(pc_code_signing.sign(TEST_SECRET, name, data), encoding="ascii")
    return shared


def test_real_disable_app(android, real_shared):
    bridge = FakeBridge({"pm list packages -s": ("package:android\npackage:com.android.settings", 0),
                         "pm disable-user --user 0 com.foo": ("Package com.foo new state: disabled-user", 0)})
    assert android.call(bridge, real_shared, "adb_permissions", "disable_app", ["com.foo"])["ok"] is True
    assert bridge.logs[-1] == "Готово."
    # Штатное приложение — отказ, как на ПК.
    bridge = FakeBridge({"pm list packages -s": ("package:com.android.settings", 0)})
    android.call(bridge, real_shared, "adb_permissions", "disable_app", ["com.android.settings"])
    assert "штатное приложение" in bridge.logs[-1]
    assert not any(c.startswith("pm disable-user") for c in bridge.commands)


def test_real_uninstall_falls_back_to_helper_when_pm_is_closed(android, real_shared):
    bridge = FakeBridge({"pm list packages -s": ("package:android", 0), "pm uninstall com.foo": ("error: closed", None)})
    assert android.call(bridge, real_shared, "adb_permissions", "uninstall_app", ["com.foo"])["ok"] is True
    assert bridge.helper_removed == ["com.foo"] and bridge.logs[-1] == "Готово."


def test_real_uninstall_uses_own_helper_without_the_word_uninstall(android, real_shared):
    """Прошивки, закрывшие pm uninstall, отклоняют любую команду со словом «uninstall» (лог №4649) — свой хелпер каталога
    (msqd_pkg_helper.dex, класс MagicSqdPkgHelper) зовётся так, чтобы этого слова в командах не было."""
    (real_shared / "msqd_pkg_helper.dex").write_bytes((ROOT / "cars/_shared/msqd_pkg_helper.dex").read_bytes())
    bridge = FakeBridge({"pm list packages -s": ("package:android", 0), "pm uninstall com.foo": ("error: closed", None),
                         "CLASSPATH=/data/local/tmp/msqd_pkg_helper.dex app_process": ("Success", 0)})
    assert android.call(bridge, real_shared, "adb_permissions", "uninstall_app", ["com.foo"])["ok"] is True
    assert bridge.logs[-1] == "Готово." and bridge.helper_removed == []
    assert "push msqd_pkg_helper.dex /data/local/tmp/msqd_pkg_helper.dex" in bridge.commands
    assert "CLASSPATH=/data/local/tmp/msqd_pkg_helper.dex app_process /data/local/tmp MagicSqdPkgHelper com.foo" in bridge.commands
    assert [c for c in bridge.commands if "uninstall" in c.lower()] == ["pm uninstall com.foo"]


def test_real_list_packages_and_grant(android, real_shared):
    bridge = FakeBridge({"pm list packages -3": ("package:com.b\npackage:com.a", 0)})
    assert android.call(bridge, real_shared, "adb_permissions", "list_installed_packages", [True]) == {
        "ok": True, "result": ["com.a", "com.b"]}
    dumpsys = ("requested permissions:\n      android.permission.CAMERA\n      android.permission.RECORD_AUDIO\n"
               "install permissions:\n")
    bridge = FakeBridge({"dumpsys package com.a": (dumpsys, 0)})
    assert android.call(bridge, real_shared, "adb_permissions", "grant_all_permissions", ["com.a"])["ok"] is True
    assert "pm grant com.a android.permission.CAMERA" in bridge.commands
    assert any(line.startswith("progress ") for line in bridge.logs)  # ход выдачи — тем же мостом


# --- «#py» в мини-DSL: разбор на обеих платформах и исполнение на ПК ---------------------------------------------


def test_android_wizard_spec_parses_py(android):
    wizard_spec = importlib.import_module("wizard_spec")
    assert wizard_spec.parse_adb_line("#py adb_permissions.grant_system_apps_permissions") == {
        "kind": "py", "module": "adb_permissions", "function": "grant_system_apps_permissions", "args": []}
    assert wizard_spec.parse_adb_line('#PY svc.disable com.a "два слова" {ask}')["args"] == ["com.a", "два слова", "{ask}"]
    assert wizard_spec.parse_adb_line("#py svc._hidden")["kind"] == "shell"  # не #py — как раньше, строка в shell


def test_pc_and_android_parse_the_same(android):
    from app import car_generator
    wizard_spec = importlib.import_module("wizard_spec")
    for line in ["#py a.b", "#py a.b x y", "#py a.b 'x y' z", "#py a.b \"unbalanced", "#py a", "#py .b", "#py a.b   "]:
        kind, payload = car_generator._parse_adb_line(line)
        parsed = wizard_spec.parse_adb_line(line)
        assert parsed["kind"] == kind, line
        if kind == "py":
            assert (parsed["module"], parsed["function"], parsed["args"]) == payload, line


def test_android_engine_handles_every_dsl_kind(android):
    """Каждый kind, который выдаёт wizard_spec.parse_adb_line, исполняет InstallEngine.runAdbCommands."""
    wizard_spec = importlib.import_module("wizard_spec")
    lines = ["#sleep 1", "#reboot", "#reboot_nowait", "#wait_device", "#ask x", "#root", "#disable_verity", "#remount",
             "#push a /b", "#install a.apk", "#install_stream a.apk", "#log ls", "ls", "rem x", "#py a.b"]
    kinds = {wizard_spec.parse_adb_line(line)["kind"] for line in lines}
    engine = (ROOT / "android/app/src/main/java/ru/magicsqd/mobile/usb/InstallEngine.kt").read_text(encoding="utf-8")
    handled = set(re.findall(r'^\s*"([a-z_]+)"(?:,\s*"([a-z_]+)")?\s*->', engine, re.MULTILINE))
    handled = {k for pair in handled for k in pair if k}
    assert kinds <= handled, kinds - handled


def test_pc_generated_code_calls_shared_function(tmp_path, monkeypatch):
    from app import car_generator
    shared = tmp_path / "_shared"
    shared.mkdir()
    (shared / "svc_test_mod.py").write_text("CALLS = []\ndef disable(ctx, *args):\n    CALLS.append(args)\n",
                                            encoding="utf-8")
    monkeypatch.syspath_prepend(str(shared))
    body = car_generator._render_command_body(["#ask Пакет?", "#py svc_test_mod.disable com.a {ask} 'b c'"], "x")
    source = "def action(ctx):\n" + "\n".join(body) + "\n"
    namespace = {}
    exec(compile(source, "install.py", "exec"), namespace)

    class Ctx:
        def ask_input(self, prompt):
            return "com.answer"

    namespace["action"](Ctx())
    module = sys.modules["svc_test_mod"]
    assert module.CALLS == [("com.a", "com.answer", "b c")]
    sys.modules.pop("svc_test_mod", None)
