"""План записи на флешку — общий код каталога (владелец, 12.10.2026: «делай вынос»): что скачать и куда положить
решает cars/_shared/flash_plan.py с сервера (подписанный) или встроенная копия той же версии; программа выполняет его
простыми операциями (flash_writer.make_plan проверяет ответ). Повод — папки комплекта, которые Android писал файлом
со списком папки (логи №244, №529, №617, №5420), и «[WinError 183]» на ПК от такого файла (№5376, №5380)."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app import code_signing, ed25519, flash_plan, flash_writer

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"
TEST_SECRET = bytes(range(32))
TRIGGER = "b832bc61472727635baffcf25dd28e9f239273e2"


def test_copies_are_identical():
    shared = (ROOT / "cars/_shared/flash_plan.py").read_bytes()
    assert (ROOT / "app/flash_plan.py").read_bytes() == shared
    assert (ANDROID_PY / "flash_plan.py").read_bytes() == shared
    assert (ANDROID_PY / "flash_writer.py").read_bytes() == (ROOT / "app/flash_writer.py").read_bytes()


def make(tmp_path):
    cars, apk = tmp_path / "cars", tmp_path / "apk"
    kit = cars / "Belgee/X70/Atlas Pro/usb_files/step_2/Отключить QDLink"
    kit.mkdir(parents=True)
    apk.mkdir()
    return cars, apk, kit


def request(cars, apk, **extra):
    return {"roots": {"cars": str(cars), "apk": str(apk)}, "manifest": None, "files": [], "selected_apks": [],
            "apks_dest": "", "shared": None, **extra}


def dests(result):
    return [item["dest"] for item in result["files"]]


def test_folder_from_the_server_is_laid_out_as_a_folder(tmp_path):
    cars, apk, kit = make(tmp_path)
    (kit / "done.sh").write_text("echo", encoding="utf-8")
    remote = "cars/Belgee/X70/Atlas Pro/usb_files/step_2/Отключить QDLink"
    manifest = {f"{remote}/done.sh": {"size": 4}, f"{remote}/{TRIGGER}/d4ad0e79.sh": {"size": 116}}
    result = flash_plan.plan(request(cars, apk, manifest=manifest,
                                     files=[str(kit / "done.sh"), str(kit / TRIGGER)]))
    assert dests(result) == ["done.sh", f"{TRIGGER}/d4ad0e79.sh"]
    folder_file = result["files"][1]
    assert folder_file["local"] == str(kit / TRIGGER / "d4ad0e79.sh") and folder_file["size"] == 116
    assert folder_file["remote"] == f"{remote}/{TRIGGER}/d4ad0e79.sh"
    assert result["local_cleanup"] == []


def test_folder_saved_as_a_file_is_cleaned_up(tmp_path):
    cars, apk, kit = make(tmp_path)
    (kit / TRIGGER).write_bytes(b'[{"name":"d4ad0e79.sh","type":"file"}]')  # список папки, сохранённый файлом (до 1.1.6)
    remote = "cars/Belgee/X70/Atlas Pro/usb_files/step_2/Отключить QDLink"
    result = flash_plan.plan(request(cars, apk, manifest={f"{remote}/{TRIGGER}/d4ad0e79.sh": {"size": 116}},
                                     files=[str(kit / TRIGGER)]))
    assert result["local_cleanup"] == [str(kit / TRIGGER)]
    assert dests(result) == [f"{TRIGGER}/d4ad0e79.sh"]


def test_local_folder_without_network(tmp_path):
    cars, apk, kit = make(tmp_path)
    (kit / "vr" / "bin").mkdir(parents=True)
    (kit / "vr" / "bin" / "md5sumcheck.sh").write_text("x", encoding="utf-8")
    (kit / "vr" / "a.txt").write_text("a", encoding="utf-8")
    (kit / "vr" / ".msqd_incomplete").write_bytes(b"")  # служебная метка не идёт на флешку
    result = flash_plan.plan(request(cars, apk, files=[str(kit / "vr")]))
    assert dests(result) == ["vr/a.txt", "vr/bin/md5sumcheck.sh"]


def test_selected_apks_shared_folder_and_order(tmp_path):
    cars, apk, kit = make(tmp_path)
    (kit / "update.bin").write_bytes(b"fw")
    (apk / "Навигация").mkdir()
    (apk / "Навигация" / "yandex.apk").write_bytes(b"apk")
    shared = cars / "_shared" / "freetuga"
    (shared / "magic_sqd" / "plugin").mkdir(parents=True)
    (shared / "magic_sqd.sh").write_text("sh", encoding="utf-8")
    (shared / "magic_sqd" / "plugin" / "magic_sqd.apk").write_bytes(b"p")
    result = flash_plan.plan(request(cars, apk, files=[str(kit / "update.bin")],
                                     selected_apks=[str(apk / "Навигация" / "yandex.apk")], apks_dest="apk files",
                                     shared=str(shared)))
    assert dests(result) == ["update.bin", "apk files/yandex.apk", "magic_sqd.sh", "magic_sqd/plugin/magic_sqd.apk"]
    assert result["files"][1]["remote"] == "apk/Навигация/yandex.apk"


@pytest.mark.parametrize("written, removed", [
    (["svlog.flag"], ["svengmode.flag"]),
    (["svengmode.flag"], ["svlog.flag"]),
    (["update.bin"], []),
])
def test_other_trigger_flags_are_removed_from_the_root(tmp_path, written, removed):
    cars, apk, kit = make(tmp_path)
    for name in written:
        (kit / name).write_bytes(b"1")
    result = flash_plan.plan(request(cars, apk, files=[str(kit / name) for name in written]))
    assert result["remove_on_flash"] == removed


def test_missing_file_stays_in_the_plan(tmp_path):
    # Нет ни на диске, ни на сервере — в плане остаётся: программа скажет «Файл не скачан», а не промолчит.
    cars, apk, kit = make(tmp_path)
    result = flash_plan.plan(request(cars, apk, files=[str(kit / "lost.apk")]))
    assert dests(result) == ["lost.apk"] and result["files"][0]["size"] == 0


# --- flash_writer: серверный план с подписью, запасной встроенный, проверка ответа -----------------------------

@pytest.fixture
def signing(monkeypatch):
    monkeypatch.setattr(code_signing, "PUBLIC_KEY", ed25519.public_key(TEST_SECRET))


def publish(shared: Path, source: str) -> None:
    shared.mkdir(parents=True, exist_ok=True)
    data = source.encode("utf-8")
    (shared / "flash_plan.py").write_bytes(data)
    (shared / "flash_plan.py.sig").write_text(code_signing.sign(TEST_SECRET, "flash_plan.py", data), encoding="ascii")


def test_builtin_plan_without_server_module(tmp_path):
    cars, apk, kit = make(tmp_path)
    (kit / "a.bin").write_bytes(b"a")
    logs = []
    result = flash_writer.make_plan(request(cars, apk, files=[str(kit / "a.bin")]), cars / "_shared", tmp_path, logs.append)
    assert result["source"] == "builtin" and dests(result) == ["a.bin"] and logs == []


def test_signed_server_plan_is_used(tmp_path, signing):
    cars, apk, kit = make(tmp_path)
    (kit / "a.bin").write_bytes(b"a")
    source = (ROOT / "cars/_shared/flash_plan.py").read_text(encoding="utf-8").replace(
        'return {"api": PLAN_API, "files": files', 'return {"api": PLAN_API, "files": files[::-1]')
    publish(cars / "_shared", source)
    (kit / "b.bin").write_bytes(b"b")
    result = flash_writer.make_plan(request(cars, apk, files=[str(kit / "a.bin"), str(kit / "b.bin")]),
                                    cars / "_shared", tmp_path)
    assert result["source"] == "server" and dests(result) == ["b.bin", "a.bin"]  # правка с сервера действует


@pytest.mark.parametrize("case", ["unsigned", "other_api", "raises", "escapes_flash", "foreign_file"])
def test_bad_server_plan_falls_back_to_builtin(tmp_path, signing, case):
    cars, apk, kit = make(tmp_path)
    (kit / "a.bin").write_bytes(b"a")
    good = (ROOT / "cars/_shared/flash_plan.py").read_text(encoding="utf-8")
    sources = {
        "other_api": good.replace("PLAN_API = 1", "PLAN_API = 2"),
        "raises": good.replace("    roots = request.get", "    raise RuntimeError('сбой')\n    roots = request.get"),
        "escapes_flash": "PLAN_API = 1\ndef plan(request):\n    return {'files': [{'local': request['files'][0], "
                         "'dest': '../../Windows/x', 'size': 1}]}\n",
        "foreign_file": "PLAN_API = 1\ndef plan(request):\n    return {'files': [{'local': '/etc/passwd', "
                        "'dest': 'passwd', 'size': 1}]}\n",
    }
    if case == "unsigned":
        (cars / "_shared").mkdir(parents=True)
        (cars / "_shared" / "flash_plan.py").write_text(good, encoding="utf-8")
    else:
        publish(cars / "_shared", sources[case])
    logs = []
    result = flash_writer.make_plan(request(cars, apk, files=[str(kit / "a.bin")]), cars / "_shared", tmp_path,
                                    logs.append)
    assert result["source"] == "builtin" and dests(result) == ["a.bin"]
    assert len(logs) == 1 and "Общий код каталога для флешки не годится" in logs[0]


@pytest.mark.parametrize("dest", ["", "/abs", "a/../b", "C:/x", "a\\b"])
def test_check_rejects_bad_flash_paths(tmp_path, dest):
    with pytest.raises(flash_writer.PlanError):
        flash_writer.check({"files": [{"local": str(tmp_path / "x"), "dest": dest}]}, tmp_path)


def test_check_rejects_nested_removal(tmp_path):
    with pytest.raises(flash_writer.PlanError):
        flash_writer.check({"files": [], "remove_on_flash": ["dir/svlog.flag"]}, tmp_path)


# --- Android: mobile_bridge.usb_plan (то, что зовёт WebBridge.kt) ----------------------------------------------

def test_android_usb_plan_bridge(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    loaded = {}
    for name in ("code_signing", "flash_plan", "flash_writer"):
        spec = importlib.util.spec_from_file_location(name, ANDROID_PY / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        loaded[name] = module
    files_root = tmp_path / "files"
    cars, apk, kit = make(files_root)
    (kit / TRIGGER).write_bytes(b"[]")
    remote = "cars/Belgee/X70/Atlas Pro/usb_files/step_2/Отключить QDLink"
    manifest = {f"{remote}/{TRIGGER}/d4ad0e79.sh": {"size": 116}}
    plan = loaded["flash_writer"].make_plan(request(cars, apk, manifest=manifest, files=[str(kit / TRIGGER)]),
                                            cars / "_shared", files_root)
    assert plan["source"] == "builtin" and plan["local_cleanup"] == [str(kit / TRIGGER)]
    assert dests(plan) == [f"{TRIGGER}/d4ad0e79.sh"]
    source = (ANDROID_PY / "mobile_bridge.py").read_text(encoding="utf-8")
    bridge = source[source.index("def usb_plan("):source.index("def ensure_apks_downloaded(")]
    assert "flash_writer.make_plan(request, root / \"cars\" / \"_shared\", root, log=logs.append)" in bridge
    assert json.dumps  # ответ — JSON с ok/plan/log (см. WebBridge.kt: usbPlan)


def test_android_kotlin_uses_the_plan():
    bridge = (ROOT / "android/app/src/main/java/ru/magicsqd/mobile/WebBridge.kt").read_text(encoding="utf-8")
    run = bridge[bridge.index("private fun usbRunStage("):]
    run = run[:run.index("private fun qrAdbWritePrepFlag(")]
    assert "val usbPlan = usbPlan(files, sharedFolderDir, selectedApks, apksDest, withManifest = true)" in run
    assert "writeUsbPlan(usbPlan, ::pushAdbLog, onWritten)" in run
    assert "else writeUsbStage(files, sharedFolderDir, selectedApks, apksDest, ::pushAdbLog, onWritten)" in run  # запасной
    listing = bridge[bridge.index("private fun usbListItems("):bridge.index("private fun usbPlan(")]
    assert "withManifest = false" in listing and "scanUsbStageItems(" in listing
    write = (ROOT / "android/app/src/main/java/ru/magicsqd/mobile/usb/UsbFlashWrite.kt").read_text(encoding="utf-8")
    plan_write = write[write.index("fun writeUsbPlan("):write.index("fun writeUsbStage(")]
    assert "removeFlashFiles(fs, plan.removeOnFlash, log)" in plan_write
    assert "writeFileToUsb(fs, File(file.local), file.dest, log, done, filesTotal, onProgress)" in plan_write
