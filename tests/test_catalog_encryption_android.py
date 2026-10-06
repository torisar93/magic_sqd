"""Шифрование каталога на Android: читатели (scanner, wizard_spec) расшифровывают файлы модели,
картинки инструкций встраиваются base64; сверка свежести учитывает шифрование. Android-модули
плоские (Chaquopy) — грузим с ANDROID_PY на пути."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ANDROID_PY = Path(__file__).resolve().parents[1] / "android/app/src/main/python"


@pytest.fixture
def android(monkeypatch):
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    mods = {}
    for name in ("catalog_crypto", "catalog_key", "offline_pack", "content_sync", "scanner", "wizard_spec"):
        spec = importlib.util.spec_from_file_location(name, ANDROID_PY / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        mods[name] = module
    mods["catalog_key"].configure(b"build-secret", bytes(range(32)))
    yield mods
    mods["catalog_key"].configure(None, None)


def _enc(ck, data: bytes) -> bytes:
    return ck.encrypt_bytes(data)


def test_android_reads_encrypted_model(tmp_path, android):
    ck, scanner, wizard_spec, content_sync, cc = (
        android["catalog_key"], android["scanner"], android["wizard_spec"],
        android["content_sync"], android["catalog_crypto"])
    model = tmp_path / "cars" / "Haval" / "H3"
    (model / "files" / "instruction_1" / "images").mkdir(parents=True)
    # Этап-инструкция №1 → папка files/instruction_1 (индекс этапа, см. wizard_spec)
    spec = {"steps": [{"type": "instruction", "title": "Инструкция"}]}
    (model / "_wizard_spec.json").write_bytes(_enc(ck, json.dumps(spec).encode()))
    (model / "version.json").write_bytes(_enc(ck, json.dumps({"revision": 7}).encode()))
    (model / "stages.py").write_bytes(_enc(ck, b"STAGES = []\n"))
    html = '<p>Шаг</p><img class="screenshot" src="images/shot.jpg">'
    (model / "files/instruction_1/instruction.html").write_bytes(_enc(ck, html.encode()))
    (model / "files/instruction_1/images/shot.jpg").write_bytes(_enc(ck, b"\xff\xd8JPEG"))

    # на диске зашифровано
    assert cc.is_encrypted((model / "_wizard_spec.json").read_bytes())
    assert cc.is_encrypted((model / "files/instruction_1/images/shot.jpg").read_bytes())

    # сканер читает зашифрованный version.json
    assert scanner._read_version(model)[0] == 7

    # wizard_spec строит этапы из зашифрованной спеки и инлайнит зашифрованную картинку (не /data/)
    result = wizard_spec.load_wizard_spec(model, files_root=tmp_path)
    html_out = next(s["instruction_html"] for s in result["steps"] if s.get("instruction_html"))
    assert "data:image/jpeg;base64," in html_out
    assert "appassets.androidplatform.net/data/" not in html_out

    # зашифрованный файл больше на OVERHEAD — та же версия, не перекачиваем
    f = model / "version.json"
    item = {"size": len(json.dumps({"revision": 7}).encode()), "mtime": f.stat().st_mtime}
    assert not content_sync._is_stale(f, item)
