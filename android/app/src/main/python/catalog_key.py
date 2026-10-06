"""Ключ шифрования файлов каталога и политика «что шифровать» (см. catalog_crypto).

Платформа один раз зовёт configure(build_secret, device_random): ПК берёт секрет сборки из
server.json и случайный секрет установки из base_dir/.catalog_key; Android — из BuildConfig и
SharedPreferences (через Kotlin-мост). Дальше:
- content_sync.download_file шифрует скачиваемый файл модели, если is_encrypted_path(путь);
- читатели файлов модели зовут decrypt_if_needed(bytes).

Без секрета (запуск из исходников) ключа нет — всё пишется и читается плейнтекстом, как раньше.

Файл общий для ПК и Android (android/app/src/main/python/catalog_key.py) — побайтно.
"""
from __future__ import annotations

import re

try:
    from . import catalog_crypto  # ПК: модуль пакета app
except ImportError:  # pragma: no cover
    import catalog_crypto  # Android: плоский модуль Chaquopy

# Собственные данные модели, которые и есть «инструкция»: спека, скрипты, версия, HTML инструкций
# и их картинки. НЕ шифруются hero/logo (общие миниатюры, медленно в сетке), payload-APK/прошивки,
# видео, cars/_shared/*, apk/*, флаги — см. план.
_MODEL_FILE_NAMES = frozenset({"_wizard_spec.json", "stages.py", "install.py", "version.json"})
_INSTRUCTION_RE = re.compile(r"/files/(?:instruction|flash)_[^/]+/(?:instruction\.html|images/[^/]+)$")

_key: bytes | None = None


def configure(build_secret: bytes | None, device_random: bytes | None) -> None:
    global _key
    if build_secret and device_random:
        _key = catalog_crypto.derive_key(bytes(build_secret), bytes(device_random))
    else:
        _key = None


def is_configured() -> bool:
    return _key is not None


def is_encrypted_path(rel: str) -> bool:
    """True, если файл модели по этому пути манифеста нужно хранить зашифрованным."""
    rel = rel.replace("\\", "/").strip("/")
    if not rel.startswith("cars/") or rel.startswith("cars/_shared/") or "/_shared/" in rel:
        return False
    name = rel.rsplit("/", 1)[-1]
    if name in _MODEL_FILE_NAMES:
        return True
    return bool(_INSTRUCTION_RE.search("/" + rel))


def encrypt_bytes(data: bytes) -> bytes:
    """Для download_file: шифрует контент модели; без ключа (из исходников) — пишет как есть."""
    return catalog_crypto.encrypt(_key, data) if _key is not None else data


def decrypt_if_needed(data: bytes) -> bytes:
    """Для читателей: расшифровывает, если файл в формате MSQD1; плейнтекст возвращает как есть."""
    if not catalog_crypto.is_encrypted(data):
        return data
    if _key is None:
        raise RuntimeError("файл каталога зашифрован, но ключ не настроен")
    return catalog_crypto.decrypt(_key, data)


def read_bytes(path) -> bytes:
    """Прочитать файл модели с диска, расшифровав при необходимости (замена path.read_bytes())."""
    with open(path, "rb") as f:
        return decrypt_if_needed(f.read())


def read_text(path, encoding: str = "utf-8", errors: str = "strict") -> str:
    """Замена path.read_text() для файлов модели (спека/версия/инструкция)."""
    return read_bytes(path).decode(encoding, errors)
