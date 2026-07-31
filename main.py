"""Точка входа. Запуск: python main.py (или собранный magic_sqd.exe)."""
import sys
from pathlib import Path


def get_base_dir() -> Path:
    """Папка рядом с magic_sqd.exe (или со скриптом при запуске из исходников)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main():
    base_dir = get_base_dir()
    sys.path.insert(0, str(base_dir))
    from app.gui import App

    app = App(base_dir)
    app.mainloop()


if __name__ == "__main__":
    main()
