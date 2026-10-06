"""Загрузка install.py из той же папки модели, что и вызывающий stages.py
— с уникальным именем модуля в sys.modules. Если несколько stages.py
разных моделей в одном запуске программы сделают обычный `import install`,
второй такой импорт в Python вернёт закешированный модуль ПЕРВОЙ модели
(коллизия по имени "install" в sys.modules) — отсюда и нужен этот хелпер."""
import importlib.util
from pathlib import Path

try:
    import catalog_io  # рядом в cars/_shared; приложение ставит ему хук расшифровки
except ImportError:  # запуск без _shared на пути / старые тесты — читаем как есть
    catalog_io = None


def load_install(stages_file):
    """stages_file — передавайте __file__ вызывающего stages.py."""
    install_path = Path(stages_file).resolve().parent / "install.py"
    # Файл модели на устройстве зашифрован — читаем исходник через catalog_io (расшифрует, если надо),
    # исполняем из строки; __file__ ставим реальным, чтобы install.py видел свою папку.
    source = (catalog_io.read_source(install_path) if catalog_io is not None
              else install_path.read_text(encoding="utf-8"))
    name = f"model_install_{abs(hash(str(install_path)))}"
    spec = importlib.util.spec_from_loader(name, loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(install_path)
    exec(compile(source, str(install_path), "exec"), module.__dict__)
    return module
