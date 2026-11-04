"""Android 1.1.20, этап «Флешка» с комплектом (Coolray L/G/G2, логи №12893, №12896): интернет оборвался, сервер не
отдал манифест, план записи не сложился — «пишу по-старому» записал 0 файлов комплекта и «Этап выполнен успешно».
Теперь mobile_bridge.sync_shared_folder_for говорит, скачался ли комплект весь, а WebBridge.usbRunStage без него
флешку не пишет (комплект с прошлого раза на телефоне — пишет, как раньше)."""
from __future__ import annotations
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"
WEB_BRIDGE = ROOT / "android/app/src/main/java/ru/magicsqd/mobile/WebBridge.kt"


@pytest.fixture
def mobile_bridge(monkeypatch):
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    for name in ("offline_pack", "content_sync", "scanner", "wizard_spec", "apk_library", "mobile_bridge"):
        spec = importlib.util.spec_from_file_location(name, ANDROID_PY / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return sys.modules["mobile_bridge"]


def _kit(content_server):
    for rel in ("cars/_shared/stagelight_install/run.sh", "cars/_shared/stagelight_install/apk/a.apk",
                "cars/_shared/stagelight_install/apk/b.apk"):
        content_server.add(rel, b"x" * 100)
    content_server.write_manifest()


def test_complete_kit_is_reported_complete(tmp_path, mobile_bridge, content_server):
    _kit(content_server)
    result = json.loads(mobile_bridge.sync_shared_folder_for(str(tmp_path / "cars"), content_server.url,
                                                             "stagelight_install"))
    assert result["manifest"] is True and result["missing"] == 0 and result["downloaded"] == 3


def test_files_that_did_not_arrive_are_counted(tmp_path, mobile_bridge, content_server, monkeypatch):
    _kit(content_server)
    monkeypatch.setattr(mobile_bridge, "sync_shared_folder", lambda *a, **k: 0)  # связь оборвалась на скачивании
    result = json.loads(mobile_bridge.sync_shared_folder_for(str(tmp_path / "cars"), content_server.url,
                                                             "stagelight_install"))
    assert result["manifest"] is True and result["missing"] == 3


def test_no_manifest_means_unknown(tmp_path, mobile_bridge, monkeypatch):
    monkeypatch.setattr(mobile_bridge, "fetch_manifest", lambda base_url: None)
    result = json.loads(mobile_bridge.sync_shared_folder_for(str(tmp_path / "cars"), "https://x", "stagelight_install"))
    assert result["manifest"] is False and result["missing"] is None
    assert result["log"] == ["Не удалось получить manifest.json с сервера — работаем с тем, что уже скачано локально."]


def _code() -> str:
    text = WEB_BRIDGE.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def test_flash_stage_stops_without_kit_and_without_files():
    code = _code()
    stage = re.search(r"private fun usbRunStage\(.*?\n    }\n", code, flags=re.S).group(0)
    # комплект не весь — до записи; «нечего записывать» — тоже до записи, а не «успешно» с 0 файлов
    assert stage.index("kitNotReady(sharedFolder, sharedFolderDir, kit)") < stage.index("writeUsbPlan(")
    assert stage.index("if (planned.isEmpty() && (files.isNotEmpty()") < stage.index("writeUsbPlan(")
    check = re.search(r"private fun kitNotReady\(.*?\n    }\n", code, flags=re.S).group(0)
    assert "missing > 0" in check and "folder.walkTopDown().any { it.isFile }" in check
