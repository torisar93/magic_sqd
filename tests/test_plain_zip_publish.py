"""Публикация модели и заявка — файлы закрытого каталога в архив расшифрованными (app/plain_zip.py). Раньше архив
собирался из зашифрованных копий устройства, и на сервере фото инструкций Jetour T1, Tank 400, Omoda C7 и Cityray
были битыми у всех (найдено 02.11.2026)."""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app import admin_client, catalog_crypto, catalog_key, plain_zip


@pytest.fixture
def keyed():
    catalog_key.configure(b"build-secret-for-tests", b"device-random-one")
    yield
    catalog_key.configure(None, None)


def _model(tmp_path: Path) -> tuple[Path, Path]:
    cars = tmp_path / "cars"
    model = cars / "Geely" / "Cityray"
    (model / "files/instruction_1/images").mkdir(parents=True)
    (model / "files/instruction_1/images/photo.jpg").write_bytes(catalog_key.encrypt_bytes(b"\xff\xd8\xff JPEG"))
    (model / "files/instruction_1/instruction.html").write_text("<p>текст</p>", encoding="utf-8")
    (model / "files/pack").mkdir(parents=True)
    (model / "files/pack/app.apk").write_bytes(b"PK\x03\x04 apk")
    return cars, model


def test_model_archive_has_plain_files(tmp_path, keyed, monkeypatch):
    cars, model = _model(tmp_path)
    sent = {}

    def fake_send(base_url, cookie, target, build_archive, log, check_cancelled, on_progress=None):
        archive = build_archive(tmp_path)
        with zipfile.ZipFile(archive) as zf:
            sent.update({n: zf.read(n) for n in zf.namelist()})
        return len(sent)

    monkeypatch.setattr(admin_client, "_build_and_send", fake_send)
    admin_client.upload_model("https://example.test", "c=1", cars, model)
    assert sent["Geely/Cityray/files/instruction_1/images/photo.jpg"] == b"\xff\xd8\xff JPEG"
    assert sent["Geely/Cityray/files/pack/app.apk"] == b"PK\x03\x04 apk"
    assert not any(catalog_crypto.is_encrypted(v) for v in sent.values())

    sent.clear()
    admin_client.upload_dir("https://example.test", "c=1", "cars", cars)
    assert sent["Geely/Cityray/files/instruction_1/images/photo.jpg"] == b"\xff\xd8\xff JPEG"


def test_file_of_another_install_is_refused(tmp_path, keyed, monkeypatch):
    cars, model = _model(tmp_path)
    catalog_key.configure(b"build-secret-for-tests", b"device-random-two")  # ключ другой установки
    monkeypatch.setattr(admin_client, "_build_and_send",
                        lambda base_url, cookie, target, build_archive, *a, **k: build_archive(tmp_path))
    with pytest.raises(admin_client.AdminClientError, match="скачайте модель заново"):
        admin_client.upload_model("https://example.test", "c=1", cars, model)


def test_submission_archive_is_plain_too():
    src = (Path(__file__).resolve().parents[1] / "app/submit_client.py").read_text(encoding="utf-8")
    assert "plain_zip.write(zf, file, file.relative_to(model_dir).as_posix())" in src
    assert "zf.write(" not in src
    assert plain_zip.write  # модуль импортируется
