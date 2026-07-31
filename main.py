"""Точка входа. Запуск: python main.py (или собранный magic_sqd.exe)."""
import sys
import time
from pathlib import Path

SPLASH_BG = "#ff00fe"       # хромакей — этот цвет вырезается через -transparentcolor
MIN_SPLASH_SECONDS = 1.8    # сплеш держим на экране не меньше этого времени


def get_base_dir() -> Path:
    """Папка рядом с magic_sqd.exe (или со скриптом при запуске из исходников)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _set_dpi_aware():
    """Без этого при масштабировании экрана (не 100%) Windows виртуализирует
    координаты для приложения, и winfo_screenwidth/height не совпадает с
    реальными пикселями — сплеш и окно центрируются со сдвигом."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # PROCESS_SYSTEM_DPI_AWARE
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


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
    win.configure(bg=SPLASH_BG)
    try:
        image = tk.PhotoImage(file=str(splash_path))
    except tk.TclError:
        win.destroy()
        return None

    w, h = image.width(), image.height()
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 2
    win.geometry(f"{w}x{h}+{x}+{y}")

    # Прозрачные пиксели картинки Tk подложит цветом SPLASH_BG (хромакей),
    # а -transparentcolor на Windows делает именно этот цвет окна "дырой" —
    # сквозь неё виден рабочий стол, вместо чёрного/белого прямоугольника
    # виден только силуэт логотипа.
    label = tk.Label(win, image=image, borderwidth=0, bg=SPLASH_BG)
    label.image = image  # держим ссылку, иначе PhotoImage подчистит GC
    label.pack()
    try:
        win.wm_attributes("-transparentcolor", SPLASH_BG)
    except tk.TclError:
        pass  # платформа не поддерживает — останется сплошной фон
    win.update()
    return win


def main():
    _set_dpi_aware()

    base_dir = get_base_dir()
    sys.path.insert(0, str(base_dir))

    import tkinter as tk

    root = tk.Tk()
    root.withdraw()  # пока видна только заглушка, не пустое главное окно

    splash_started = time.time()
    splash = _show_splash(root, base_dir)

    from app.gui import App

    App(base_dir, root)  # строит UI поверх root, пока показан сплеш

    if splash is not None:
        remaining = MIN_SPLASH_SECONDS - (time.time() - splash_started)
        if remaining > 0:
            time.sleep(remaining)
        splash.destroy()
    root.deiconify()
    root.mainloop()


if __name__ == "__main__":
    main()
