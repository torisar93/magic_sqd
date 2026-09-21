"""Настоящий прогресс записи на флешку (жалоба клиента, 2026-09-21 — видно
только окно с анимацией, реального прогресса по файлам нет): app/usb_context.py
(чанковое копирование вместо shutil.copy2 целиком) и
app/web/api/usb_api.py:_scan_usb_items/UsbApi.list_items (список файлов
заранее, для кольца прогресса на фронтенде — см. LabUI.busy/apk_progress
в app/web/frontend/js/progress08.js). Ни pywebview, ни ADB/USB не нужны —
drive_root в UsbContext это просто обычная папка на диске."""
from __future__ import annotations
import os
import threading
import time
import types
import zipfile

from app import usb_context
from app.usb_context import UsbContext
from app.web.api import usb_api


def make_ctx(tmp_path, files_total=0, **kwargs):
    """on_progress по умолчанию просто копит события в список — тот же
    набор полей, что и usb_api.py:_progress_apk передаёт дальше в событие
    apk_progress (path, bytes_done, bytes_total, files_done, files_total,
    state)."""
    events: list[dict] = []

    def on_progress(path, bytes_done, bytes_total, files_done, files_total_, state):
        events.append({"path": path, "bytes_done": bytes_done, "bytes_total": bytes_total,
                        "files_done": files_done, "files_total": files_total_, "state": state})

    ctx = UsbContext(
        drive_root=tmp_path / "drive",
        model_dir=tmp_path / "model",
        selected_apks=[],
        log_fn=lambda *a: None,
        cancel_flag=threading.Event(),
        on_progress=on_progress,
        files_total=files_total,
        **kwargs,
    )
    return ctx, events


# --- UsbContext: чанковое копирование --------------------------------------

def test_copy_file_reports_growing_byte_progress_in_chunks(tmp_path, monkeypatch):
    # Маленький чанк вместо настоящих 4МБ — на тестовом файле получается
    # несколько чанков без необходимости гонять мегабайты в pytest.
    monkeypatch.setattr(usb_context, "_COPY_CHUNK_SIZE", 16)
    src = tmp_path / "firmware.bin"
    src.write_bytes(b"x" * 100)
    ctx, events = make_ctx(tmp_path, files_total=1)

    target = ctx.copy_file(src, "firmware.bin")

    assert target == tmp_path / "drive" / "firmware.bin"
    assert target.read_bytes() == b"x" * 100
    running = [e for e in events if e["state"] == "running"]
    assert len(running) == 7  # ceil(100/16)
    # растёт монотонно и последний чанк доходит ровно до конца файла
    assert [e["bytes_done"] for e in running] == sorted(e["bytes_done"] for e in running)
    assert running[-1]["bytes_done"] == 100
    assert all(e["bytes_total"] == 100 for e in running)
    assert all(e["path"] == str(src) for e in running)  # путь ИСТОЧНИКА, не назначения на флешке
    # files_done ещё не увеличился, пока идут чанки ОДНОГО файла
    assert all(e["files_done"] == 0 for e in running)
    # ровно одно "done" на файл целиком
    done = [e for e in events if e["state"] == "done"]
    assert len(done) == 1
    assert done[0]["bytes_done"] == done[0]["bytes_total"] == 100
    assert done[0]["files_done"] == 1
    assert done[0]["files_total"] == 1


def test_copy_file_preserves_mtime_like_copy2(tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"abc")
    old_mtime = time.time() - 100_000
    os.utime(src, (old_mtime, old_mtime))
    ctx, _ = make_ctx(tmp_path)

    target = ctx.copy_file(src, "a.bin")

    assert abs(target.stat().st_mtime - src.stat().st_mtime) < 2


def test_copy_dir_reports_progress_per_whole_file_not_per_subdir(tmp_path):
    src_dir = tmp_path / "usb_files"
    (src_dir / "sub").mkdir(parents=True)
    (src_dir / "a.txt").write_bytes(b"A")
    (src_dir / "sub" / "b.txt").write_bytes(b"BB")
    ctx, events = make_ctx(tmp_path, files_total=2)

    ctx.copy_dir(src_dir, "")

    assert (tmp_path / "drive" / "a.txt").read_bytes() == b"A"
    assert (tmp_path / "drive" / "sub" / "b.txt").read_bytes() == b"BB"
    done = [e for e in events if e["state"] == "done"]
    assert len(done) == 2  # по одному на файл, ни одного на саму подпапку sub/
    assert [e["files_done"] for e in done] == [1, 2]


def test_extract_zip_reports_one_done_event_per_member(tmp_path):
    zip_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("one.txt", "1" * 10)
        zf.writestr("nested/two.txt", "22" * 10)
    ctx, events = make_ctx(tmp_path, files_total=2)

    ctx.extract_zip(zip_path, "out")

    assert (tmp_path / "drive" / "out" / "one.txt").read_text() == "1" * 10
    assert (tmp_path / "drive" / "out" / "nested" / "two.txt").read_text() == "22" * 10
    assert len(events) == 2
    assert all(e["state"] == "done" for e in events)
    assert [e["files_done"] for e in events] == [1, 2]
    assert events[0]["bytes_done"] == events[0]["bytes_total"] == 10
    assert all(e["path"] == str(zip_path) for e in events)


def test_write_text_reports_done_event(tmp_path):
    ctx, events = make_ctx(tmp_path, files_total=1)

    ctx.write_text("flag.txt", "hello")

    assert (tmp_path / "drive" / "flag.txt").read_text(encoding="utf-8") == "hello"
    assert len(events) == 1
    assert events[0]["state"] == "done"
    assert events[0]["files_done"] == 1


def test_files_done_counter_is_shared_across_calls_in_one_run(tmp_path):
    """files_done — общий счётчик на весь ctx, не сбрасывается между
    copy_file/write_text в рамках одного запуска usb-этапа (см. докстринг
    UsbContext.__init__)."""
    src = tmp_path / "a.bin"
    src.write_bytes(b"a")
    ctx, events = make_ctx(tmp_path, files_total=2)

    ctx.copy_file(src, "a.bin")
    ctx.write_text("b.txt", "b")

    done = [e for e in events if e["state"] == "done"]
    assert [e["files_done"] for e in done] == [1, 2]


# --- usb_api.py: список файлов заранее (для кольца прогресса) --------------

def _make_model(tmp_path, name="Model"):
    model_dir = tmp_path / "cars" / "Test" / name
    model_dir.mkdir(parents=True)
    return types.SimpleNamespace(dir=model_dir, key=f"Test/{name}")


def test_scan_usb_items_finds_step_files_selected_apks_and_shared_folder(tmp_path):
    model = _make_model(tmp_path)
    step_dir = model.dir / "usb_files" / "step_1"
    step_dir.mkdir(parents=True)
    (step_dir / "firmware.iso").write_bytes(b"F" * 50)

    apk = tmp_path / "apk" / "app.apk"
    apk.parent.mkdir(parents=True)
    apk.write_bytes(b"A" * 20)

    shared_dir = tmp_path / "cars" / "_shared" / "common_usb"
    shared_dir.mkdir(parents=True)
    (shared_dir / "readme.txt").write_bytes(b"R" * 5)

    stage = {"usb_copy_selected_apks": True, "usb_shared_folder": "common_usb"}
    items = usb_api._scan_usb_items(tmp_path, model, stage, 0, None, [str(apk)])

    by_name = {item["name"]: item for item in items}
    assert set(by_name) == {"firmware.iso", "app.apk", "readme.txt"}
    assert by_name["firmware.iso"] == {"name": "firmware.iso", "path": str(step_dir / "firmware.iso"), "size": 50}
    assert by_name["app.apk"] == {"name": "app.apk", "path": str(apk), "size": 20}
    assert by_name["readme.txt"] == {"name": "readme.txt", "path": str(shared_dir / "readme.txt"), "size": 5}
    # порядок — тот же, что и реальная запись в car_generator.py: usb_dir -> выбранные APK -> _shared
    assert [item["name"] for item in items] == ["firmware.iso", "app.apk", "readme.txt"]


def test_scan_usb_items_respects_variant_subfolder(tmp_path):
    model = _make_model(tmp_path)
    (model.dir / "usb_files" / "step_1" / "Lite").mkdir(parents=True)
    (model.dir / "usb_files" / "step_1" / "Lite" / "lite.bin").write_bytes(b"L")
    (model.dir / "usb_files" / "step_1" / "Full").mkdir(parents=True)
    (model.dir / "usb_files" / "step_1" / "Full" / "full.bin").write_bytes(b"FF")

    items_lite = usb_api._scan_usb_items(tmp_path, model, {}, 0, "Lite", [])
    items_full = usb_api._scan_usb_items(tmp_path, model, {}, 0, "Full", [])

    assert [item["name"] for item in items_lite] == ["lite.bin"]
    assert [item["name"] for item in items_full] == ["full.bin"]


def test_scan_usb_items_ignores_selected_apks_when_stage_does_not_copy_them(tmp_path):
    model = _make_model(tmp_path)
    apk = tmp_path / "apk" / "app.apk"
    apk.parent.mkdir(parents=True)
    apk.write_bytes(b"A")

    items = usb_api._scan_usb_items(tmp_path, model, {"usb_copy_selected_apks": False}, 0, None, [str(apk)])

    assert items == []


def test_scan_usb_items_tolerates_missing_directories(tmp_path):
    model = _make_model(tmp_path)
    # Ни usb_files/, ни _shared/<folder>/ не существуют, APK не скачан —
    # список файлов чисто визуальный (см. план), не должен падать с исключением.
    stage = {"usb_copy_selected_apks": True, "usb_shared_folder": "missing_folder"}

    items = usb_api._scan_usb_items(tmp_path, model, stage, 0, None, [str(tmp_path / "not_downloaded.apk")])

    assert items == []


def test_usb_api_list_items_returns_ok_with_items(tmp_path, monkeypatch):
    model = _make_model(tmp_path)
    (model.dir / "usb_files" / "step_1").mkdir(parents=True)
    (model.dir / "usb_files" / "step_1" / "f.bin").write_bytes(b"12345")
    stage = {"usb_copy_selected_apks": False}
    monkeypatch.setattr(usb_api, "load_stages", lambda m: [stage])
    scanner = types.SimpleNamespace(get_model=lambda key: model if key == "Test/Model" else None)
    api = usb_api.UsbApi(tmp_path, scanner)

    result = api.list_items("Test/Model", 0, None, [])

    assert result == {"ok": True, "items": [{"name": "f.bin", "path": str(model.dir / "usb_files/step_1/f.bin"),
                                              "size": 5}]}


def test_usb_api_list_items_unknown_model_is_not_ok(tmp_path, monkeypatch):
    scanner = types.SimpleNamespace(get_model=lambda key: None)
    api = usb_api.UsbApi(tmp_path, scanner)

    result = api.list_items("Nope/Nope", 0, None, [])

    assert result == {"ok": False, "error": "unknown model key"}
