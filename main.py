"""Точка входа. Запуск: python main.py (или собранный magic_sqd.exe)."""
import sys
import time
from pathlib import Path

SPLASH_BG = "#ff00fe"       # хромакей — этот цвет вырезается через -transparentcolor
MIN_SPLASH_SECONDS = 1.8    # сплеш держим на экране не меньше этого времени

_STARTUP_LOG_PATH = None  # выставляется в main() — путь к startup.log рядом с программой


def _log_step(message: str) -> None:
    """Построчный лог запуска с немедленным сбросом на диск — в отличие от
    traceback в except-блоке ниже, переживает и жёсткое падение (нативный
    краш без Python-исключения): по последней записанной строке видно, до
    какого шага программа вообще дошла."""
    if _STARTUP_LOG_PATH is None:
        return
    try:
        with open(_STARTUP_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {message}\n")
            f.flush()
    except OSError:
        pass


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
    global _STARTUP_LOG_PATH
    _STARTUP_LOG_PATH = get_base_dir() / "startup.log"
    try:
        _STARTUP_LOG_PATH.unlink(missing_ok=True)  # только текущий запуск, не копится
    except OSError:
        pass

    _log_step("_set_dpi_aware()")
    _set_dpi_aware()

    base_dir = get_base_dir()
    sys.path.insert(0, str(base_dir))

    _log_step("import customtkinter")
    import customtkinter

    # customtkinter иначе сам вызывает SetProcessDpiAwareness() при первом
    # окне — а _set_dpi_aware() выше его уже вызвала. Повторный вызов этого
    # WinAPI с другим значением DPI-awareness в связке PyInstaller+
    # customtkinter на некоторых машинах приводит к падению процесса без
    # трассировки (ровно то, что мы ловим — сплеш, затем окно, затем всё
    # исчезает без единой ошибки в консоли).
    _log_step("deactivate_automatic_dpi_awareness()")
    customtkinter.deactivate_automatic_dpi_awareness()

    _log_step("customtkinter.CTk()")
    root = customtkinter.CTk()
    # Раньше здесь был root.withdraw() (сплеш поверх скрытого окна, потом
    # root.deiconify() под конец) — лог показал, что падение происходит
    # ровно в момент входа в mainloop(), сразу после deiconify: похоже,
    # резкий переход "окно ни разу не отрисовывалось" → "сразу видимое и
    # полностью построенное" на некоторых машинах роняет процесс на уровне
    # Tcl (без единого Python-исключения) — новые CTk-виджеты (в частности
    # CTkScrollableFrame под капотом CTkListbox) получают самый первый цикл
    # реальной отрисовки одним скачком. Теперь root виден с самого начала
    # (сплеш поверх него) — виджеты строятся и красятся постепенно, как
    # обычно у Tk-приложений, без "мгновенного" первого рендера.
    _log_step("CTk() created OK")

    from app.theme import apply_theme
    _log_step("apply_theme()")
    apply_theme(root, base_dir)
    _log_step("apply_theme() OK")

    splash_started = time.time()
    _log_step("_show_splash()")
    splash = _show_splash(root, base_dir)
    _log_step(f"_show_splash() OK, shown={splash is not None}")

    from app.gui import App

    _log_step("App(base_dir, root) - building main window UI")
    App(base_dir, root)  # строит UI поверх root, пока показан сплеш
    _log_step("App() OK")

    if splash is not None:
        remaining = MIN_SPLASH_SECONDS - (time.time() - splash_started)
        if remaining > 0:
            time.sleep(remaining)
        splash.destroy()
        _log_step("splash destroyed")
    _log_step("entering mainloop()")
    root.mainloop()
    _log_step("mainloop() returned (normal close)")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # console=False в сборке — без этого сбой на старте выглядит как
        # "окно мелькнуло и пропало", без единого шанса понять причину.
        import traceback
        text = traceback.format_exc()
        _log_step("EXCEPTION:\n" + text)
        try:
            (get_base_dir() / "crash.log").write_text(text, encoding="utf-8")
        except OSError:
            pass
        try:
            import tkinter.messagebox as messagebox
            messagebox.showerror(
                "Magic SQD — не удалось запустить",
                "Подробности сохранены в startup.log/crash.log рядом с программой.\n\n"
                + text[-1500:],
            )
        except Exception:
            pass
        raise
