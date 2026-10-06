"""Сквозная проверка шифрования каталога на ПК: download_file шифрует собственные файлы модели
по политике, а читатели (сканер, загрузка этапов с exec stages.py/install.py, инструкция с
картинкой) расшифровывают. hero/logo и payload остаются плейнтекстом."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app import catalog_crypto, catalog_key, content_sync, scanner, stage_runner


@pytest.fixture
def key(tmp_path):
    catalog_key.configure(b"build-secret", bytes(range(32)))
    shared = tmp_path / "cars" / "_shared"
    shared.mkdir(parents=True)
    # catalog_io из репозитория + хук расшифровки (как catalog_setup в бою)
    (shared / "catalog_io.py").write_bytes((Path(__file__).resolve().parents[1] / "cars/_shared/catalog_io.py").read_bytes())
    (shared / "load_sibling.py").write_bytes((Path(__file__).resolve().parents[1] / "cars/_shared/load_sibling.py").read_bytes())
    sys.path.insert(0, str(shared))
    import catalog_io
    catalog_io.set_decrypt(catalog_key.decrypt_if_needed)
    yield
    catalog_io.set_decrypt(None)
    sys.path.remove(str(shared))
    for m in ("catalog_io", "load_sibling"):
        sys.modules.pop(m, None)
    catalog_key.configure(None, None)


def _download(server_bytes: bytes, dest: Path, remote_path: str):
    """Имитация content_sync.download_file без сети: шифрует по политике, как настоящий."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = server_bytes
    if catalog_key.is_configured() and catalog_key.is_encrypted_path(remote_path):
        data = catalog_key.encrypt_bytes(data)
    dest.write_bytes(data)


def test_model_files_encrypted_on_disk_but_readable(tmp_path, key):
    model = tmp_path / "cars" / "Haval" / "H3"
    stages_src = (
        "import sys\n"
        "from pathlib import Path\n"
        "_p = Path(__file__).resolve()\n"
        "for parent in _p.parents:\n"
        "    if (parent / '_shared').is_dir():\n"
        "        sys.path.insert(0, str(parent / '_shared')); break\n"
        "from load_sibling import load_install\n"
        "m = load_install(__file__)\n"
        "STAGES = [{'id': 'a', 'type': 'instruction', 'title': m.TITLE, "
        "'instruction': 'files/instruction_1/instruction.html'}]\n"
    )
    install_src = "TITLE = 'Поставить приложения'\n"
    html = '<p>Инструкция</p><img class="screenshot" src="images/shot.jpg">'
    _download(stages_src.encode(), model / "stages.py", "cars/Haval/H3/stages.py")
    _download(install_src.encode(), model / "install.py", "cars/Haval/H3/install.py")
    _download(json.dumps({"revision": 4}).encode(), model / "version.json", "cars/Haval/H3/version.json")
    _download(json.dumps({"id": "x"}).encode(), model / "_wizard_spec.json", "cars/Haval/H3/_wizard_spec.json")
    _download(html.encode(), model / "files/instruction_1/instruction.html",
              "cars/Haval/H3/files/instruction_1/instruction.html")
    _download(b"\xff\xd8JPEGDATA", model / "files/instruction_1/images/shot.jpg",
              "cars/Haval/H3/files/instruction_1/images/shot.jpg")
    _download(b"HEROWEBPBYTES", model / "hero.webp", "cars/Haval/H3/hero.webp")
    _download(b"APKPAYLOAD", model / "files/pack/RuStore.apk", "cars/Haval/H3/files/pack/RuStore.apk")

    # На диске: модельные файлы зашифрованы, миниатюра и payload — нет
    assert catalog_crypto.is_encrypted((model / "stages.py").read_bytes())
    assert catalog_crypto.is_encrypted((model / "install.py").read_bytes())
    assert catalog_crypto.is_encrypted((model / "version.json").read_bytes())
    assert catalog_crypto.is_encrypted((model / "files/instruction_1/instruction.html").read_bytes())
    assert catalog_crypto.is_encrypted((model / "files/instruction_1/images/shot.jpg").read_bytes())
    assert (model / "hero.webp").read_bytes() == b"HEROWEBPBYTES"
    assert (model / "files/pack/RuStore.apk").read_bytes() == b"APKPAYLOAD"

    # Сканер читает зашифрованный version.json
    assert scanner._read_version(model)[0] == 4

    # Загрузка этапов: stages.py и install.py расшифровываются и исполняются (через load_sibling/catalog_io)
    models = {m.name: m for m in scanner.scan_cars(tmp_path / "cars").get("Haval", [])}
    stages = stage_runner.load_stages(models["H3"].leaf)
    assert stages[0]["title"] == "Поставить приложения"  # TITLE из расшифрованного install.py


def test_size_check_treats_encrypted_as_same_version(tmp_path, key):
    plain = json.dumps({"revision": 1}).encode()
    f = tmp_path / "version.json"
    f.write_bytes(catalog_key.encrypt_bytes(plain))
    item = {"size": len(plain), "mtime": f.stat().st_mtime}
    # Зашифрованный файл больше на OVERHEAD, но это та же версия — не устарел, не перекачивается
    assert not content_sync._is_stale(f, item)
    assert content_sync.local_copy_is_current(f, item)


def test_without_key_download_stays_plaintext(tmp_path):
    catalog_key.configure(None, None)
    model = tmp_path / "cars" / "Haval" / "H3"
    _download(b"STAGES = []\n", model / "stages.py", "cars/Haval/H3/stages.py")
    assert (model / "stages.py").read_bytes() == b"STAGES = []\n"  # из исходников — как раньше
