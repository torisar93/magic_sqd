"""Подпись Python-кода каталога, который исполняется на Android (py_runner.py: команда «#py модуль.функция»).

На ПК программа исполняет cars/_shared/*.py с сервера как есть. На телефоне техника код с сервера исполняется только с
подписью разработчика: рядом с модулем лежит <модуль>.py.sig — Ed25519 (ed25519.py) тем же ключом, что бандлы
интерфейса (ui_bundle.PUBLIC_KEY), над "magicsqd-py|<имя файла>|<sha256 содержимого>". Подписывает и выкладывает
scripts/publish_shared.py; ключа на сервере нет — даже с доступом к серверу нельзя исполнить свой код на телефонах.

Файл ОДИНАКОВЫЙ на ПК (app/code_signing.py) и Android (android/app/src/main/python/code_signing.py) — tests/test_py_runner.py."""
from __future__ import annotations

import hashlib
from pathlib import Path

try:
    from . import ed25519  # ПК: модуль пакета app
    from .ui_bundle import PUBLIC_KEY
except ImportError:  # Android (Chaquopy): модули верхнего уровня
    import ed25519
    from ui_bundle import PUBLIC_KEY

SIG_SUFFIX = ".sig"


class UnsignedCode(ImportError):
    """Модуля нет, у него нет подписи или она не сошлась — исполнять нельзя."""


def message(name: str, sha256: str) -> bytes:
    return f"magicsqd-py|{name}|{sha256.lower()}".encode("utf-8")


def sign(secret: bytes, name: str, data: bytes) -> str:
    return ed25519.sign(secret, message(name, hashlib.sha256(data).hexdigest())).hex()


def verify(name: str, data: bytes, sig_hex: str, public: bytes | None = None) -> bool:
    try:
        signature = bytes.fromhex(sig_hex.strip())
    except (AttributeError, ValueError):
        return False
    return ed25519.verify(PUBLIC_KEY if public is None else public,
                          message(name, hashlib.sha256(data).hexdigest()), signature)


def read_verified(path, public: bytes | None = None) -> bytes:
    """Содержимое файла, если подпись <файл>.sig сходится; иначе UnsignedCode с понятной причиной. Возвращает ровно
    те байты, что проверены (файл не перечитывается между проверкой и исполнением)."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError:
        raise UnsignedCode(f"нет файла {path.name} — синхронизируйте каталог") from None
    try:
        sig_hex = path.with_name(path.name + SIG_SUFFIX).read_text(encoding="ascii")
    except (OSError, ValueError):
        raise UnsignedCode(f"{path.name} без подписи разработчика — на телефоне не исполняется") from None
    if not verify(path.name, data, sig_hex, public):
        raise UnsignedCode(f"подпись {path.name} не сошлась — на телефоне не исполняется")
    return data
