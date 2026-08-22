"""Минимальная проверка, что Chaquopy вообще встраивается и работает —
без этого не имеет смысла переносить cars/_shared/*.py и scanner.py.
Реальный перенос бизнес-логики — отдельный шаг после этой проверки
и после подтверждения USB-спайков (см. android/README.md)."""
import platform


def hello() -> str:
    return f"Python {platform.python_version()} жив внутри Chaquopy"
