"""Файлы модели — в архив для сервера расшифрованными (публикация из редактора, «Выгрузить на сервер», заявка техника).

Файлы закрытого каталога на устройстве зашифрованы ключом этой установки (catalog_key: HKDF(build_secret || device_random)).
Архив собирался из них как есть, и на сервер уходили зашифрованные копии, которые не расшифрует больше никто: фото
инструкций Jetour T1, Tank 400, Omoda C7, Cityray со значком Wi-Fi были битыми у всех (найдено 02.11.2026). Редактор
пересоздаёт спеку, инструкцию и stages.py при сохранении, а картинки копирует как есть — поэтому страдали именно они."""
from __future__ import annotations

import zipfile
from pathlib import Path

from . import catalog_crypto, catalog_key


class ForeignEncryptedFile(ValueError):
    """Файл зашифрован, но не ключом этой установки — отправлять его нельзя (на сервере он был бы битым)."""


def write(zf: zipfile.ZipFile, file: Path, arcname: str) -> None:
    """Как zf.write(file, arcname), но зашифрованный файл каталога — расшифрованным. Большие APK не читаются в память
    целиком: сначала проверяется только начало файла."""
    with open(file, "rb") as fh:
        head = fh.read(len(catalog_crypto.MAGIC))
    if not catalog_crypto.is_encrypted(head):
        zf.write(file, arcname)
        return
    try:
        plain = catalog_key.decrypt_if_needed(Path(file).read_bytes())
    except (ValueError, RuntimeError) as exc:
        raise ForeignEncryptedFile(
            f"{Path(file).name}: файл зашифрован, и программа не может его расшифровать — скачайте модель заново "
            f"(«Обновить файлы модели») и повторите публикацию") from exc
    info = zipfile.ZipInfo.from_file(file, arcname)
    info.compress_type = zipfile.ZIP_DEFLATED
    zf.writestr(info, plain)
