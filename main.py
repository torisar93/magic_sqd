"""Точка входа. Запуск: python main.py (или собранный magic_sqd.exe)."""
import sys
from pathlib import Path


def get_base_dir() -> Path:
    """Папка рядом с magic_sqd.exe (или со скриптом при запуске из исходников)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _show_splash(root, base_dir: Path):
    """Показывает окно-заглушку (Toplevel уже созданного root) на время
    загрузки — импорт app.gui/tkinterweb занимает заметное время. Возвращает
    окно сплеша или None, если картинки нет.

    Важно: сплеш — это Toplevel уже существующего root, а не отдельный
    tk.Tk(). На Windows если создать и уничтожить один tk.Tk(), а потом
    создать для главного окна ещё один — у второго перестаёт применяться
    iconbitmap (родной значок окна). Поэтому root создаётся один раз в
    main() и переиспользуется для сплеша и для главного окна."""
    splash_path = base_dir / "assets" / "splash.png"
    if not splash_path.exists():
        return None

    import tkinter as tk

    win = tk.Toplevel(root)
    win.overrideredirect(True)
    try:
        image = tk.PhotoImage(file=str(splash_path))
    except tk.TclError:
        win.destroy()
        return None

    w, h = image.width(), image.height()
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 2
    win.geometry(f"{w}x{h}+{x}+{y}")

    label = tk.Label(win, image=image, borderwidth=0)
    label.image = image  # держим ссылку, иначе PhotoImage подчистит GC
    label.pack()
    win.update()
    return win


def main():
    base_dir = get_base_dir()
    sys.path.insert(0, str(base_dir))

    import tkinter as tk

    root = tk.Tk()
    root.withdraw()  # пока видна только заглушка, не пустое главное окно

    splash = _show_splash(root, base_dir)

    from app.gui import App

    App(base_dir, root)  # строит UI поверх root, пока показан сплеш

    if splash is not None:
        splash.destroy()
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    main()
