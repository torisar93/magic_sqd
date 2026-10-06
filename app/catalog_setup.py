"""Включение закрытого каталога при старте программы (вызывается из app/web/bridge.py).

Делает три вещи, если в сборке есть секрет (get_app_build_secret):
1. настраивает ключ шифрования файлов модели (catalog_key.configure);
2. ставит хук расшифровки в cars/_shared/catalog_io (чтобы load_sibling мог читать install.py);
3. включает токен официальной сборки на запросах к /content (content_sync.set_app_token).

Без секрета (запуск из исходников) ничего не включается — каталог открыт, файлы плейнтекстом.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from . import app_token, catalog_key, content_sync
from .content_config import get_app_build_secret, get_base_url
from .ping_client import get_or_create_client_id

_KEY_FILE = ".catalog_key"  # случайный секрет этой установки (не в git, см. .gitignore)


def _device_random(base_dir: Path) -> bytes:
    path = base_dir / _KEY_FILE
    try:
        data = path.read_bytes()
        if len(data) == 32:
            return data
    except OSError:
        pass
    data = os.urandom(32)
    try:
        path.write_bytes(data)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except OSError:
        pass  # не записать (нет прав) — ключ продержится до перезапуска, модель перекачается
    return data


def _register_decrypt_hook(base_dir: Path) -> None:
    shared = base_dir / "cars" / "_shared"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    try:
        import catalog_io  # из cars/_shared
    except ImportError:
        return
    catalog_io.set_decrypt(catalog_key.decrypt_if_needed)


def configure(base_dir: Path) -> None:
    build_secret = get_app_build_secret(base_dir)
    catalog_key.configure(build_secret, _device_random(base_dir) if build_secret else None)
    _register_decrypt_hook(base_dir)
    base_url = get_base_url(base_dir)
    if build_secret and base_url:
        client_id = get_or_create_client_id(base_dir)
        content_sync.set_app_token(app_token.AppToken(base_url, build_secret, client_id))
