"""Android: папка внутри комплекта флешки (папка-триггер магнитолы b832bc…/, vr/, TurboDog/, Data/…) скачивается
папкой, а не файлом. Раньше apk_library.ensure_apks_downloaded качал её как файл: на адрес папки сервер отдаёт её
список (nginx autoindex json), и этот список уходил на флешку вместо папки со скриптом (логи №244, №529, №617, №5420:
Belgee X70 Дорест (Atlas Pro) — «Записано: b832bc… (130 байт)», Geely Coolray Дорест — Data, SysApps, Wallpapers…;
на ПК тот же техник потом получил «[WinError 183]» от этого файла, №5376). Запись на флешку раскладывает папку
(UsbFlashWrite.kt: writeUsbStage/scanUsbStageItems)."""
from __future__ import annotations

import http.server
import importlib.util
import json
import sys
import threading
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"
KIT = "cars/Belgee/X70/Atlas Pro/usb_files/step_2/Отключить QDLink"
FOLDER = f"{KIT}/b832bc61472727635baffcf25dd28e9f239273e2"
SCRIPT = b"#!/system/bin/sh\npm disable com.neusoft.ssp.ces.c4.car.assistant\n"
FILES = {f"{FOLDER}/d4ad0e797c738a44a9dd1b9231d201e8374dfda2.sh": SCRIPT, f"{KIT}/done.sh": b"echo done\n"}
LISTING = json.dumps([{"name": "d4ad0e797c738a44a9dd1b9231d201e8374dfda2.sh", "type": "file", "size": len(SCRIPT)}]).encode()


@pytest.fixture
def apk_library(monkeypatch):
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    for name in ("offline_pack", "content_sync", "apk_library"):
        spec = importlib.util.spec_from_file_location(name, ANDROID_PY / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return sys.modules["apk_library"]


@pytest.fixture
def server():
    state = {"missing": set()}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = urllib.parse.unquote(self.path.lstrip("/"))
            if path == "manifest.json":
                body = json.dumps({"files": {k: {"size": len(v), "mtime": 0} for k, v in FILES.items()}}).encode()
            elif path in FILES and path not in state["missing"]:
                body = FILES[path]
            elif path.rstrip("/") == FOLDER:
                body = LISTING  # как nginx autoindex_format json на адрес папки
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}", state
    httpd.shutdown()
    httpd.server_close()


def run(apk_library, tmp_path, base_url, logs):
    files = tmp_path / "files"
    (files / "apk").mkdir(parents=True, exist_ok=True)
    paths = [str(files / FOLDER), str(files / KIT / "done.sh")]
    return apk_library.ensure_apks_downloaded(files / "apk", files / "cars", base_url, paths, log=logs.append), files


def test_kit_folder_is_downloaded_as_a_folder(tmp_path, apk_library, server):
    base_url, _ = server
    logs = []
    downloaded, files = run(apk_library, tmp_path, base_url, logs)
    folder = files / FOLDER
    assert downloaded == 2 and folder.is_dir()
    assert (folder / "d4ad0e797c738a44a9dd1b9231d201e8374dfda2.sh").read_bytes() == SCRIPT
    assert (files / KIT / "done.sh").read_bytes() == b"echo done\n"
    assert not (folder / apk_library.FOLDER_INCOMPLETE_MARK).exists()


def test_old_listing_file_is_replaced_by_the_folder(tmp_path, apk_library, server):
    base_url, _ = server
    files = tmp_path / "files"
    (files / KIT).mkdir(parents=True)
    (files / FOLDER).write_bytes(LISTING)  # прошлое «скачивание» папки 1.1.5 и раньше
    logs = []
    run(apk_library, tmp_path, base_url, logs)
    assert (files / FOLDER).is_dir()
    assert (files / FOLDER / "d4ad0e797c738a44a9dd1b9231d201e8374dfda2.sh").read_bytes() == SCRIPT


def test_partly_downloaded_folder_is_marked_until_complete(tmp_path, apk_library, server):
    base_url, state = server
    state["missing"].add(f"{FOLDER}/d4ad0e797c738a44a9dd1b9231d201e8374dfda2.sh")
    logs = []
    _, files = run(apk_library, tmp_path, base_url, logs)
    marker = files / FOLDER / apk_library.FOLDER_INCOMPLETE_MARK
    assert marker.exists() and any("Не удалось скачать" in line for line in logs)
    state["missing"].clear()
    run(apk_library, tmp_path, base_url, logs)
    assert not marker.exists()


def test_android_writer_lays_out_the_folder():
    code = (ROOT / "android/app/src/main/java/ru/magicsqd/mobile/usb/UsbFlashWrite.kt").read_text(encoding="utf-8")
    stage = code[code.index("fun writeUsbStage("):]
    assert 'writeFileToUsb(fs, inner, "${f.name}/$rel", log, filesDone, filesTotal, onProgress)' in stage
    assert "File(it, FOLDER_INCOMPLETE_MARK).exists()" in stage
    scan = code[code.index("fun scanUsbStageItems("):code.index("fun writeFileToUsb(")]
    assert "folderFiles(f).forEach" in scan
    assert 'private const val FOLDER_INCOMPLETE_MARK = ".msqd_incomplete"' in code
