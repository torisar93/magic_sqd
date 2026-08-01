"""Загрузка install.py из той же папки модели, что и вызывающий stages.py
— с уникальным именем модуля в sys.modules. Если несколько stages.py
разных моделей в одном запуске программы сделают обычный `import install`,
второй такой импорт в Python вернёт закешированный модуль ПЕРВОЙ модели
(коллизия по имени "install" в sys.modules) — отсюда и нужен этот хелпер."""
import importlib.util
from pathlib import Path


def load_install(stages_file):
    """stages_file — передавайте __file__ вызывающего stages.py."""
    install_path = Path(stages_file).resolve().parent / "install.py"
    spec = importlib.util.spec_from_file_location(
        f"model_install_{abs(hash(str(install_path)))}", install_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
