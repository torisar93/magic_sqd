"""Чтение исходника модели (stages.py/install.py) с диска — с расшифровкой, если включено.

Файлы модели на устройстве техника хранятся зашифрованными (см. app/catalog_key.py), но код
cars/_shared/ общий и публичный — ключа знать не должен. Приложение при старте ставит сюда
функцию расшифровки: catalog_io.set_decrypt(catalog_key.decrypt_if_needed). Пока не поставлена
(запуск из исходников, тесты) — читаем как есть. load_sibling.load_install и приложение
(app/stage_runner.py) читают исходник моделей через этот модуль.
"""
from pathlib import Path

_decrypt = None  # функция bytes->bytes; None = читать как есть


def set_decrypt(fn):
    global _decrypt
    _decrypt = fn


def read_source(path) -> str:
    data = Path(path).read_bytes()
    if _decrypt is not None:
        data = _decrypt(data)
    return data.decode("utf-8")
