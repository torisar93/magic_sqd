"""Ранний доступ (server/user_groups.py): программа показывает модель под замком по «витрине»
из раздела early_access каталога, подписчику — целиком с меткой; без подписки файлы инструкции
не остаются на диске; в дату открытия модель становится обычной."""
from __future__ import annotations
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from app import content_sync
from app import scanner as desktop_scanner
from app.web.api.scanner_api import ScannerApi

ANDROID_PY = Path(__file__).resolve().parents[1] / "android/app/src/main/python"

MODEL = "Haval/H6"
OPEN_AT = 2_000_000_000.0
MODEL_FILES = ("_wizard_spec.json", "stages.py", "install.py", "version.json", "hero.webp", "logo.png",
               "Рест/_wizard_spec.json", "Рест/stages.py", "Рест/version.json", "Рест/hero.webp",
               "Дорест/_wizard_spec.json", "Дорест/stages.py", "Дорест/version.json")
TEASER = ("version.json", "hero.webp", "logo.png", "Рест/version.json", "Рест/hero.webp", "Дорест/version.json")


def _publish(server, subscriber: bool, early: bool = True, hidden: bool = True):
    """Каталог, каким его отдаёт сервер этому пользователю (см. user_groups.filter_manifest)."""
    server.add("cars/Haval/Jolion/_wizard_spec.json", b"{}")
    server.add("cars/Haval/Jolion/stages.py", b"x")
    for name in MODEL_FILES:
        server.add(f"cars/{MODEL}/{name}", json.dumps({"revision": 1}).encode() if name.endswith("version.json") else b"x")
    files = server.write_manifest()
    data = {"files": files}
    if hidden and not subscriber:
        data["files"] = {rel: meta for rel, meta in files.items() if not rel.startswith(f"cars/{MODEL}/")}
    if early:
        data["early_access"] = {MODEL: {"open_at": OPEN_AT,
                                        "files": {f"cars/{MODEL}/{n}": files[f"cars/{MODEL}/{n}"] for n in TEASER}}}
    (server.content / "manifest.json").write_text(json.dumps(data), encoding="utf-8")


def _load_android(monkeypatch, *names):
    for name in names:  # андроидные модули импортируют друг друга по короткому имени
        spec = importlib.util.spec_from_file_location(name, ANDROID_PY / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    return [sys.modules[name] for name in names]


@pytest.fixture(params=["desktop", "android"])
def platform(request, monkeypatch):
    """Обе копии: ПК (app/content_sync.py, app/scanner.py) и Android (android/.../python/)."""
    if request.param == "desktop":
        def sync(app_base, server):
            manifest = content_sync.fetch_manifest(server.url)
            content_sync.sync_scripts(app_base, app_base / "cars", manifest=manifest)
            content_sync.prune_removed_models(app_base, app_base / "cars", manifest)
            content_sync.sync_early_access(app_base, app_base / "cars", manifest)
            return {g.name: g for g in desktop_scanner.scan_cars(app_base / "cars").get("Haval", [])}
    else:
        _, android_sync, android_scanner = _load_android(monkeypatch, "offline_pack", "content_sync", "scanner")

        def sync(app_base, server):
            cars = app_base / "cars"
            manifest = android_sync.fetch_manifest(server.url)
            android_sync.sync_scripts(server.url, cars, manifest=manifest)
            android_sync.prune_removed_models(app_base, cars, manifest)
            android_sync.sync_early_access(server.url, cars, manifest)
            return {g.name: g for g in android_scanner.scan_cars(cars).get("Haval", [])}
    sync.name = request.param
    return sync


def _marker(app_base):
    return json.loads((app_base / "cars" / MODEL / "_early_access.json").read_text(encoding="utf-8"))


def test_without_subscription_only_the_teaser_is_on_disk_and_locked(app_base, content_server, platform):
    _publish(content_server, subscriber=False)
    groups = platform(app_base, content_server)
    model_dir = app_base / "cars" / MODEL
    assert sorted(p.relative_to(model_dir).as_posix() for p in model_dir.rglob("*") if p.is_file()) == \
        sorted([*TEASER, "_early_access.json"])
    assert _marker(app_base) == {"open_at": OPEN_AT, "locked": True}

    h6 = groups["H6"]
    assert h6.early_locked and h6.early_open_at == OPEN_AT
    assert [(m.modification, m.early_locked, m.stages_script) for m in h6.modifications] == \
        [("Дорест", True, None), ("Рест", True, None)]
    assert not groups["Jolion"].early_open_at and not groups["Jolion"].early_locked

    if platform.name != "desktop":
        return
    data = ScannerApi(app_base / "cars", app_base / "apk").list_cars()
    [haval] = [b for b in data["brands"] if b["name"] == "Haval"]
    [group] = [g for g in haval["groups"] if g["name"] == "H6"]
    assert group["early_locked"] is True and group["early_open_at"] == OPEN_AT
    assert all(m["early_locked"] and m["early_open_at"] == OPEN_AT for m in group["modifications"])


def test_subscriber_gets_the_whole_model_with_the_mark(app_base, content_server, platform):
    _publish(content_server, subscriber=True)
    h6 = platform(app_base, content_server)["H6"]
    assert (app_base / "cars" / MODEL / "Рест" / "stages.py").is_file()
    assert _marker(app_base) == {"open_at": OPEN_AT, "locked": False}
    assert h6.early_open_at == OPEN_AT and not h6.early_locked
    assert all(m.early_open_at == OPEN_AT and not m.early_locked and m.stages_script for m in h6.modifications)


def test_lost_subscription_removes_the_instruction(app_base, content_server, platform):
    _publish(content_server, subscriber=True)
    platform(app_base, content_server)
    _publish(content_server, subscriber=False)  # вышел из аккаунта / снята отметка
    h6 = platform(app_base, content_server)["H6"]
    model_dir = app_base / "cars" / MODEL
    assert not list(model_dir.rglob("stages.py")) and not list(model_dir.rglob("_wizard_spec.json"))
    assert (model_dir / "hero.webp").is_file() and h6.early_locked


def test_lost_subscription_cleanup_without_a_snapshot(app_base, content_server, platform):
    """Даже без снимка прошлого каталога (prune_removed_models ничего не удаляет) файлы
    инструкции под замком не остаются."""
    _publish(content_server, subscriber=True)
    platform(app_base, content_server)
    (app_base / "known_models.json").unlink()
    _publish(content_server, subscriber=False)
    platform(app_base, content_server)
    assert not list((app_base / "cars" / MODEL).rglob("stages.py"))


def test_on_the_date_the_model_becomes_ordinary(app_base, content_server, platform):
    _publish(content_server, subscriber=False)
    platform(app_base, content_server)
    _publish(content_server, subscriber=False, early=False, hidden=False)  # сервер открыл модель всем
    h6 = platform(app_base, content_server)["H6"]
    assert not (app_base / "cars" / MODEL / "_early_access.json").exists()
    assert not h6.early_locked and h6.early_open_at is None
    assert all(m.stages_script for m in h6.modifications)


def test_early_access_removed_while_still_hidden_drops_the_teaser(app_base, content_server, platform):
    _publish(content_server, subscriber=False)
    platform(app_base, content_server)
    _publish(content_server, subscriber=False, early=False, hidden=True)  # ранний доступ сняли, модель скрыта
    groups = platform(app_base, content_server)
    assert "H6" not in groups and not (app_base / "cars" / MODEL).exists()


def test_bad_paths_in_the_early_section_are_ignored(app_base, content_server, platform):
    _publish(content_server, subscriber=False)
    data = json.loads((content_server.content / "manifest.json").read_text(encoding="utf-8"))
    data["early_access"].update({"../evil": {"open_at": OPEN_AT, "files": {}},
                                 "Haval/../../x": {"open_at": OPEN_AT, "files": {}},
                                 "Haval/Bad": {"open_at": "завтра", "files": {}},
                                 "Haval/Sneaky": {"open_at": OPEN_AT, "files": {"cars/Haval/Other/stages.py": {"size": 1}}}})
    (content_server.content / "manifest.json").write_text(json.dumps(data), encoding="utf-8")
    platform(app_base, content_server)
    assert not (app_base / "evil").exists() and not (app_base / "x").exists()
    assert not (app_base / "cars" / "Haval" / "Bad").exists()
    assert not (app_base / "cars" / "Haval" / "Other").exists()
