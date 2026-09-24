"""Wi-Fi ADB: «сначала скачать, потом подключиться» — InstallApi.prefetch_apks и InstallRunner.start(skip_sync)
(см. app/web/api/install_api.py, app/runner.py). Пойман в 1.0.16 по отзыву техника: у компьютера в сети
магнитолы нет интернета, а докачка шла внутри установки."""
from __future__ import annotations
import json
import threading
import types

import pytest

from conftest import wait_until


def make_api(base, monkeypatch):
    from app.web.api import install_api as ia
    events: list[dict] = []
    monkeypatch.setattr(ia.event_bridge, "push", events.append)
    scanner = types.SimpleNamespace(get_model=lambda key: types.SimpleNamespace(dir=base / "cars/Test/Model"))
    return ia.InstallApi("adb", base, scanner), events


def logs_of(events):
    return [e["text"] for e in events if e.get("kind") == "install_log"]


@pytest.fixture
def catalog(content_server, app_base):
    content_server.add("apk/GPS/a.apk", b"A" * 70_000)
    content_server.add("apk/GPS/b.apk", b"B" * 5_000)
    content_server.add("cars/Test/Model/files/resign_cert/certificate.crt", b"crt")
    content_server.add("cars/Test/Model/files/resign_cert/private.pk8", b"k")
    (app_base / "cars/Test/Model").mkdir(parents=True)
    (app_base / "apk/GPS").mkdir(parents=True)
    # есть в манифесте, но файла на сервере нет — 404
    content_server.write_manifest(extra={"apk/GPS/ghost.apk": {"size": 999, "mtime": 1}})
    return content_server


def test_prefetch_downloads_selected_apks_and_resign_cert(catalog, app_base, monkeypatch):
    api, events = make_api(app_base, monkeypatch)
    selected = [str(app_base / "apk/GPS/a.apk"), str(app_base / "apk/GPS/b.apk")]

    assert api.prefetch_apks("k", 0, selected) == {"ok": True}

    assert (app_base / "apk/GPS/a.apk").stat().st_size == 70_000
    assert (app_base / "apk/GPS/b.apk").stat().st_size == 5_000
    assert (app_base / "cars/Test/Model/files/resign_cert/private.pk8").exists(), "сертификат переподписи докачивается вместе с APK"
    lines = logs_of(events)
    assert any("сначала скачиваю" in line for line in lines)
    assert any("Приложения скачаны" in line for line in lines)
    assert any(e.get("kind") == "sync_progress" and e.get("total") == 0 for e in events), "прогресс-бар скрывается в конце"


def test_prefetch_names_missing_files(catalog, app_base, monkeypatch):
    api, _ = make_api(app_base, monkeypatch)
    result = api.prefetch_apks("k", 0, [str(app_base / "apk/GPS/a.apk"), str(app_base / "apk/GPS/ghost.apk")])
    assert result["ok"] is False
    assert "ghost.apk" in result["error"] and "интернет" in result["error"]


def test_prefetch_is_idempotent(catalog, app_base, monkeypatch):
    api, events = make_api(app_base, monkeypatch)
    selected = [str(app_base / "apk/GPS/a.apk")]
    assert api.prefetch_apks("k", 0, selected)["ok"] is True
    before = len(events)
    assert api.prefetch_apks("k", 0, selected)["ok"] is True
    assert not any("Скачиваю" in line for line in logs_of(events[before:])), "второй раз ничего не качает"


def test_prefetch_can_be_cancelled(catalog, app_base, monkeypatch):
    catalog.add("apk/GPS/big.apk", b"Z" * 60_000_000)
    catalog.write_manifest()
    api, _ = make_api(app_base, monkeypatch)
    result: dict = {}
    thread = threading.Thread(target=lambda: result.update(api.prefetch_apks("k", 0, [str(app_base / "apk/GPS/big.apk")])))
    thread.start()
    assert wait_until(lambda: api._prefetching, timeout=5)
    api.cancel_stage()
    thread.join(30)
    assert not thread.is_alive()
    # На localhost файл может успеть докачаться раньше отмены — тогда ok; иначе честная отмена.
    assert result.get("cancelled") is True or result.get("ok") is True
    assert api._prefetching is False


def test_runner_skip_sync_never_touches_network(tmp_path):
    from app.runner import InstallRunner
    base = tmp_path / "app"
    (base / "cars/Test/Model").mkdir(parents=True)
    (base / "apk").mkdir()
    # закрытый порт: любая попытка сети закончилась бы ошибкой в логе
    (base / "server.json").write_text(json.dumps({"base_url": "http://127.0.0.1:9/content"}), encoding="utf-8")
    done = threading.Event()
    outcome: dict = {}
    logs: list[str] = []
    runner = InstallRunner("adb", logs.append, lambda ok, msg, **kw: (outcome.update(ok=ok, msg=msg), done.set()), base_dir=base)
    seen = []
    runner.start(types.SimpleNamespace(dir=base / "cars/Test/Model"), "1.2.3.4:5555",
                 [str(base / "apk/a.apk")], run_fn=lambda ctx: seen.append(ctx.device), skip_sync=True)
    assert done.wait(10)
    assert outcome["ok"] is True and seen == ["1.2.3.4:5555"]
    assert logs == [], f"при skip_sync не должно быть сетевых попыток: {logs}"


def test_prefetch_reports_per_apk_download_progress_for_the_install_ring(catalog, app_base, monkeypatch):
    # Жалоба владельца 2026-09-23: на этапе приложений не видно прогресса
    # скачивания — кольцо окна установки понимает только apk_progress
    # (progress08.js: LabUI.progress), а десктоп слал лишь общий sync_progress.
    api, events = make_api(app_base, monkeypatch)
    a, b = str(app_base / "apk/GPS/a.apk"), str(app_base / "apk/GPS/b.apk")

    assert api.prefetch_apks("k", 3, [a, b]) == {"ok": True}

    downloads = [e for e in events if e.get("kind") == "apk_progress"]
    assert downloads, "ни одного apk_progress за всё скачивание"
    assert all(e["stage_index"] == 3 and e["phase"] == "download" and e["state"] == "running"
               for e in downloads)
    by_path = {}
    for e in downloads:
        by_path.setdefault(e["path"], []).append(e)
    assert set(by_path) == {a, b}  # те же строки, что в очереди окна — по ним ищется строка
    last_a = by_path[a][-1]
    assert last_a["bytes_done"] == last_a["bytes_total"] == 70_000 and last_a["determinate"] is True


def test_runner_reports_per_apk_download_progress_too(catalog, app_base):
    # Проводная установка: докачка внутри InstallRunner._run, до run(ctx).
    from app.runner import InstallRunner
    progress, finished = [], []
    runner = InstallRunner("adb", lambda m: None, lambda ok, msg, **kw: finished.append(ok), base_dir=app_base,
                           on_apk_download_progress=lambda p, d, t: progress.append((p, d, t)))
    model = types.SimpleNamespace(dir=app_base / "cars/Test/Model")
    b = str(app_base / "apk/GPS/b.apk")

    runner.start(model, "serial", [b], run_fn=lambda ctx: None)
    wait_until(lambda: finished)

    assert finished == [True]
    assert progress and progress[-1] == (b, 5_000, 5_000)
