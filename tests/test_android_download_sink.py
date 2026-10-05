"""Android: ход скачивания перед записью на флешку — сразу в журнал и в окно записи (Belgee S50, 28.09: пока
качались выбранные приложения и комплект «freetuga» — после переустановки программы ~465 МБ, — окно записи просто
крутилось, а строки «Скачиваю …» приходили разом в конце; техник решал, что всё зависло, и сворачивал программу).
mobile_bridge.sync_shared_folder_for и ensure_apks_downloaded принимают sink (WebBridge.kt: DownloadSink):
line — строка журнала сразу, progress — байты."""
from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ANDROID_PY = Path(__file__).resolve().parents[1] / "android/app/src/main/python"


@pytest.fixture
def mobile_bridge(monkeypatch):
    # Андроидные модули из одной папки, в порядке зависимостей (mobile_bridge: from content_sync import …).
    for name in ("offline_pack", "content_sync", "scanner", "wizard_spec", "apk_library", "mobile_bridge"):
        spec = importlib.util.spec_from_file_location(name, ANDROID_PY / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return sys.modules["mobile_bridge"]


class Sink:
    """Как DownloadSink.kt: line(text), progress(done, total) — зовутся из потоков закачки."""

    def __init__(self):
        self.lines, self.bytes = [], []

    def line(self, text):
        self.lines.append(text)

    def progress(self, done, total):
        self.bytes.append((done, total))


def _shared_folder(content_server):
    sizes = {"cars/_shared/freetuga/magic_sqd.sh": 120,
             "cars/_shared/freetuga/magic_sqd/for_install/com.google.android.webview.apk": 300_000,
             "cars/_shared/freetuga/magic_sqd/for_unpacking/VocalizerEx2.zip": 200_000}
    for rel, size in sizes.items():
        content_server.add(rel, b"x" * size)
    content_server.write_manifest()
    return sizes


def test_shared_folder_lines_go_live_and_bytes_add_up(tmp_path, mobile_bridge, content_server):
    sizes = _shared_folder(content_server)
    sink = Sink()
    result = json.loads(mobile_bridge.sync_shared_folder_for(str(tmp_path / "cars"), content_server.url, "freetuga", sink))

    assert result["downloaded"] == 3 and result["log"] == []  # строки ушли в sink, а не списком в конце
    assert sum(line.startswith("Скачиваю cars/_shared/freetuga/") for line in sink.lines) == 3
    assert sink.lines[-1] == "Скачано файлов: 3."
    total = sum(sizes.values())
    assert sink.bytes[0] == (0, total) and sink.bytes[-1] == (total, total)  # сумма по всем файлам, не по одному
    done = [d for d, _ in sink.bytes]
    assert done == sorted(done)
    assert (tmp_path / "cars/_shared/freetuga/magic_sqd/for_unpacking/VocalizerEx2.zip").stat().st_size == 200_000


def test_shared_folder_without_sink_is_as_before(tmp_path, mobile_bridge, content_server):
    _shared_folder(content_server)
    result = json.loads(mobile_bridge.sync_shared_folder_for(str(tmp_path / "cars"), content_server.url, "freetuga"))
    assert result["downloaded"] == 3 and result["log"][-1] == "Скачано файлов: 3."


def test_selected_apks_report_to_the_sink(tmp_path, mobile_bridge, content_server):
    apk = b"PK\x03\x04" + b"y" * 50_000
    content_server.add("apk/Утилиты/File Manager+ v3.2.8.apk", apk)
    content_server.write_manifest()
    local = tmp_path / "apk" / "Утилиты" / "File Manager+ v3.2.8.apk"
    sink = Sink()

    result = json.loads(mobile_bridge.ensure_apks_downloaded(
        str(tmp_path / "apk"), str(tmp_path / "cars"), content_server.url, json.dumps([str(local)]), None, sink))

    assert result["downloaded"] == 1 and result["log"] == []
    assert sink.lines == ["Скачиваю File Manager+ v3.2.8.apk..."]
    assert sink.bytes[-1] == (len(apk), len(apk))
    assert local.read_bytes() == apk
