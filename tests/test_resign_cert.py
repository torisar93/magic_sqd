"""Сертификат переподписи APK (files/resign_cert, Changan WutongOS) — докачка перед установкой и честный лог
(см. app/runner.py:sync_resign_cert, app/install_context.py:_maybe_resign). До 1.0.15 сертификат никогда не
скачивался клиентом, переподпись молча пропускалась, магнитола отвечала «-118 is not auth» (логи #361/#362/#365)."""
from __future__ import annotations
import threading
import types

import pytest


def make_runner(base):
    from app.runner import InstallRunner
    logs: list[str] = []
    runner = InstallRunner("adb", logs.append, lambda *a: None, base_dir=base)
    runner._cancel_flag = threading.Event()
    return runner, logs


def make_ctx(base, model_dir, logs):
    from app.install_context import InstallContext
    (base / "cars/_shared").mkdir(exist_ok=True)
    return InstallContext("adb", "x", model_dir, [], logs.append, threading.Event(), shared_dir=base / "cars/_shared")


def test_sync_downloads_cert_when_model_has_one(content_server, app_base):
    from app.apk_signer import resign_cert_dir_for_model
    content_server.add("cars/Changan/CS75 Plus/1-3/files/resign_cert/certificate.crt", b"-----BEGIN CERTIFICATE-----")
    content_server.add("cars/Changan/CS75 Plus/1-3/files/resign_cert/private.pk8", b"\x30\x82")
    content_server.write_manifest()
    model_dir = app_base / "cars/Changan/CS75 Plus/1-3"
    model_dir.mkdir(parents=True)
    runner, logs = make_runner(app_base)

    runner.sync_resign_cert(types.SimpleNamespace(dir=model_dir))

    assert resign_cert_dir_for_model(model_dir) is not None
    assert any("resign_cert): 2." in line for line in logs), logs


def test_sync_is_silent_for_models_without_cert(content_server, app_base):
    from app.apk_signer import resign_cert_dir_for_model
    content_server.add("cars/Geely/Monjaro/SE/install.py", b"# no cert")
    content_server.write_manifest()
    model_dir = app_base / "cars/Geely/Monjaro/SE"
    model_dir.mkdir(parents=True)
    runner, logs = make_runner(app_base)

    runner.sync_resign_cert(types.SimpleNamespace(dir=model_dir))

    assert resign_cert_dir_for_model(model_dir) is None
    assert logs == []


def test_sync_survives_server_outage(tmp_path):
    base = tmp_path / "app"
    (base / "cars/X/Y").mkdir(parents=True)
    (base / "server.json").write_text('{"base_url": "http://127.0.0.1:9/content"}', encoding="utf-8")
    runner, logs = make_runner(base)
    runner.sync_resign_cert(types.SimpleNamespace(dir=base / "cars/X/Y"))  # не должно бросать
    assert True


def test_maybe_resign_logs_absence_once_per_run(app_base):
    model_dir = app_base / "cars/Geely/Monjaro/SE"
    model_dir.mkdir(parents=True)
    logs: list[str] = []
    ctx = make_ctx(app_base, model_dir, logs)
    apk = model_dir / "a.apk"
    apk.write_bytes(b"x")

    assert ctx._maybe_resign(apk) == apk
    assert ctx._maybe_resign(apk) == apk

    absent = [line for line in logs if "не используется" in line]
    assert len(absent) == 1, logs


def test_maybe_resign_with_cert_but_no_bundled_jre_is_a_clear_error(app_base):
    from app.install_context import AdbError
    model_dir = app_base / "cars/Changan/CS75 Plus/1-3"
    (model_dir / "files/resign_cert").mkdir(parents=True)
    (model_dir / "files/resign_cert/certificate.crt").write_bytes(b"c")
    (model_dir / "files/resign_cert/private.pk8").write_bytes(b"k")
    logs: list[str] = []
    ctx = make_ctx(app_base, model_dir, logs)
    apk = model_dir / "a.apk"
    apk.write_bytes(b"x")

    with pytest.raises(AdbError, match="JRE"):
        ctx._maybe_resign(apk)
    assert any("Переподписываю a.apk" in line for line in logs)
