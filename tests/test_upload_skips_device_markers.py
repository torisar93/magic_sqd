"""Публикация модели из редактора и заявка техника не уносят на сервер пометки этого устройства
(content_sync.is_local_only_file): «Скачать заранее» (_offline.json), ранний доступ (_early_access.json),
неотправленную правку (_local_edit.json). Иначе через каталог они приехали бы ко всем — модель
значилась бы у всех скачанной заранее. Закрытые этапы (_closed/) — наоборот, уходят."""
from __future__ import annotations
import zipfile
from pathlib import Path

from app import admin_client, submit_client

MARKERS = ("_offline.json", "_early_access.json", "_local_edit.json", "_known_files.json")


def _model(tmp_path: Path) -> tuple[Path, Path]:
    cars = tmp_path / "cars"
    model_dir = cars / "Haval" / "H3"
    for rel in ("stages.py", "_wizard_spec.json", "_closed/stages.py", "Рест/stages.py", *MARKERS,
                *(f"Рест/{m}" for m in MARKERS)):
        (model_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (model_dir / rel).write_text("x", encoding="utf-8")
    return cars, model_dir


def test_upload_model_archive_has_no_device_markers(tmp_path, monkeypatch):
    cars, model_dir = _model(tmp_path)
    names = []

    def fake_send(base_url, cookie, dest, build_archive, *args, **kwargs):
        archive = build_archive(tmp_path)
        with zipfile.ZipFile(archive) as zf:
            names.extend(zf.namelist())
        return 1

    monkeypatch.setattr(admin_client, "_build_and_send", fake_send)
    admin_client.upload_model("http://127.0.0.1:1", "sid=1", cars, model_dir)
    assert "Haval/H3/stages.py" in names and "Haval/H3/_closed/stages.py" in names
    assert not [n for n in names if n.rsplit("/", 1)[-1] in MARKERS]


def test_submission_archive_has_no_device_markers(tmp_path, monkeypatch):
    _, model_dir = _model(tmp_path)
    names = []

    def fake_send(archive_path, *args, **kwargs):
        with zipfile.ZipFile(archive_path) as zf:
            names.extend(zf.namelist())

    monkeypatch.setattr(submit_client, "_send", fake_send)
    submit_client.submit_model(model_dir, "Haval", "H3", config=None)
    assert "stages.py" in names and "_closed/stages.py" in names
    assert not [n for n in names if n.rsplit("/", 1)[-1] in MARKERS]
