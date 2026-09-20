"""Wi-Fi: свои файлы adb/actions-этапов докачиваются при показе этапа (InstallApi.prefetch_stage), а запуск
идёт с prefetched=True — при запуске компьютер уже в сети магнитолы без интернета."""
from __future__ import annotations
import types


def make_api(base, monkeypatch, stages):
    from app.web.api import install_api as ia
    events: list[dict] = []
    monkeypatch.setattr(ia.event_bridge, "push", events.append)
    monkeypatch.setattr(ia, "load_stages", lambda model: stages)
    scanner = types.SimpleNamespace(get_model=lambda key: types.SimpleNamespace(dir=base / "cars/Test/Model"))
    return ia.InstallApi("adb", base, scanner), events


def test_prefetch_adb_stage_downloads_stage_files(content_server, app_base, monkeypatch):
    content_server.add("cars/Test/Model/files/adb_2/tool.bin", b"T" * 1000)
    content_server.add("cars/Test/Model/files/apps/big.apk", b"A" * 1000)  # чужая папка — не трогаем
    content_server.write_manifest()
    (app_base / "cars/Test/Model").mkdir(parents=True)
    api, _ = make_api(app_base, monkeypatch, [{"type": "instruction"}, {"type": "adb", "title": "Команды"}])

    assert api.prefetch_stage("k", 1) == {"ok": True}

    assert (app_base / "cars/Test/Model/files/adb_2/tool.bin").exists()
    # adb-этап не знает свою подпапку (adb_files не сохраняется в STAGES) — берётся весь files/ модели,
    # как и при обычном запуске (см. InstallApi._stage_own_dirs); usb_files/ сюда не входит.
    assert (app_base / "cars/Test/Model/files/apps/big.apk").exists()


def test_prefetch_actions_stage_downloads_all_actions_dirs(content_server, app_base, monkeypatch):
    content_server.add("cars/Test/Model/files/actions_3_1/a.apk", b"A")
    content_server.add("cars/Test/Model/files/actions_3_2/b.bin", b"B")
    content_server.write_manifest()
    (app_base / "cars/Test/Model").mkdir(parents=True)
    stages = [{}, {}, {"type": "actions", "title": "Действия", "actions": [{"label": "1"}, {"label": "2"}]}]
    api, _ = make_api(app_base, monkeypatch, stages)

    assert api.prefetch_stage("k", 2, None) == {"ok": True}
    assert (app_base / "cars/Test/Model/files/actions_3_1/a.apk").exists()
    assert (app_base / "cars/Test/Model/files/actions_3_2/b.bin").exists()

    assert api.prefetch_stage("k", 2, 0) == {"ok": True}  # одно действие — тоже ок
    assert api.prefetch_stage("k", 9)["ok"] is False        # несуществующий этап


def test_prefetch_stage_survives_server_outage(tmp_path, monkeypatch):
    base = tmp_path / "app"
    (base / "cars/Test/Model").mkdir(parents=True)
    (base / "server.json").write_text('{"base_url": "http://127.0.0.1:9/content"}', encoding="utf-8")
    api, events = make_api(base, monkeypatch, [{"type": "adb", "title": "x"}])
    result = api.prefetch_stage("k", 0)
    # sync_tree при недоступном сервере пишет в лог и возвращает 0 — не исключение
    assert result["ok"] is True
    assert api._prefetching is False
    assert any(e.get("kind") == "sync_progress" and e.get("total") == 0 for e in events)
