"""Первый запуск на macOS (жалоба 29.10.2026: «Не удалось загрузить stages.py: No module named 'load_sibling'»):
данные программы — в ~/Library/Application Support/MagicSQD, при старте cars/_shared ещё нет. catalog_setup ставит её
в sys.path и пробует import catalog_io — раньше Python запоминал пустой путь (sys.path_importer_cache → None) и
после синхронизации туда не заглядывал: до перезапуска не открывалась ни одна модель."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from app import catalog_setup, stage_runner

ROOT = Path(__file__).resolve().parents[1]
STAGES = '''import sys
from pathlib import Path

for _p in Path(__file__).resolve().parents:
    if (_p / "_shared").is_dir():
        sys.path.insert(0, str(_p / "_shared"))
        break
from load_sibling import load_install  # noqa: E402

m = load_install(__file__)

STAGES = [{"type": "instruction", "title": m.TITLE, "id": 1, "next": None}]
'''


@pytest.fixture
def first_run(tmp_path, monkeypatch):
    base = tmp_path / "MagicSQD"
    shared = base / "cars" / "_shared"
    # cars/_shared репозитория (его ставят другие тесты) — убрать: здесь своя установка без _shared
    monkeypatch.setattr(sys, "path", [p for p in sys.path if Path(p).name != "_shared"])
    for name in ("catalog_io", "load_sibling"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(catalog_setup, "_shared_dir", None, raising=False)
    monkeypatch.setattr(catalog_setup, "_hook_set", False, raising=False)
    yield base, shared
    for name in ("catalog_io", "load_sibling"):
        sys.modules.pop(name, None)
    sys.path_importer_cache.pop(str(shared), None)


def test_model_opens_after_shared_arrives_without_restart(first_run):
    base, shared = first_run
    catalog_setup._register_decrypt_hook(base)  # старт: _shared нет — catalog_io ещё не скачан
    assert "catalog_io" not in sys.modules

    # синхронизация докачала каталог
    shared.mkdir(parents=True, exist_ok=True)
    for name in ("catalog_io.py", "load_sibling.py"):
        shutil.copy(ROOT / "cars" / "_shared" / name, shared / name)
    model = base / "cars" / "Geely" / "Tugella" / "Рест"
    model.mkdir(parents=True)
    (model / "stages.py").write_text(STAGES, encoding="utf-8")
    (model / "install.py").write_text('TITLE = "Флешка"\n', encoding="utf-8")

    module = stage_runner._load_module(model / "stages.py")
    assert module.STAGES[0]["title"] == "Флешка"
    import catalog_io
    assert catalog_io._decrypt is not None  # хук расшифровки встал и на первом запуске
