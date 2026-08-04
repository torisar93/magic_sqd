"""Точка входа технической сборки (pywebview-интерфейс). Тот же app/web/,
что и admin_main_web.py — разница только в admin_mode=False и именах
лог-файлов, как раньше у main.py/admin_main.py (см. app/gui.py: admin_mode
прячет adb-консоль и показывает кнопку "Выгрузить на сервер...").

Запуск: python main_web.py (собранный вариант — magic_sqd.exe, см.
magic_sqd.spec)."""
import sys
import time
from pathlib import Path

APP_TITLE = "Magic SQD — установщик приложений для мультимедиа"

_STARTUP_LOG_PATH = None  # выставляется в main() — путь к startup.log рядом с программой


def _log_step(message: str) -> None:
    """Построчный лог запуска с немедленным flush — переживает нативный
    краш без Python-трассировки, видно последний достигнутый шаг."""
    if _STARTUP_LOG_PATH is None:
        return
    try:
        with open(_STARTUP_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {message}\n")
            f.flush()
    except OSError:
        pass


def get_base_dir() -> Path:
    """Папка рядом с magic_sqd.exe (или со скриптом при запуске из исходников)
    — где лежат cars/apk/tools/assets и куда пишутся логи. Пользователь
    кладёт/правит их вручную прямо здесь, поэтому это ВСЕГДА папка exe, а не
    _internal (см. get_frontend_dir — там наоборот)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_frontend_dir(base_dir: Path) -> Path:
    """Статические файлы app/web/frontend/ (см. datas= в magic_sqd.spec/
    admin.spec) — в onedir-сборке PyInstaller 6+ лежат в _internal/ рядом с
    exe, НЕ прямо рядом с ним (в отличие от cars/apk/tools/assets — эти
    пользователь трогает руками, поэтому им обязательно быть прямо у exe).
    sys._MEIPASS — официальный способ PyInstaller найти данные бандла
    независимо от onedir/onefile; при запуске из исходников такого атрибута
    нет, тогда просто используем base_dir, как раньше."""
    meipass = getattr(sys, "_MEIPASS", None)
    root = Path(meipass) if meipass else base_dir
    return root / "app" / "web" / "frontend"


def _set_dpi_aware():
    """Без этого окно центрируется/масштабируется со сдвигом при масштабе
    экрана не 100%. ОТКРЫТЫЙ РИСК: не проверено на всех версиях WebView2,
    вызывает ли сам pywebview свой SetProcessDpiAwareness с ДРУГИМ
    значением — именно двойная установка с разными значениями роняла
    customtkinter без трассировки в старом main.py. Если после перехода на
    pywebview появится немотивированный краш на старте без исключения в
    startup.log — начать проверку отсюда."""
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


def run(admin_mode: bool, log_prefix: str, title: str) -> None:
    """Общая точка входа technician/admin сборок — main_web.py и
    admin_main_web.py вызывают её с разными admin_mode/log_prefix/title,
    как main.py/admin_main.py делали для tkinter-версии."""
    global _STARTUP_LOG_PATH
    base_dir = get_base_dir()
    _STARTUP_LOG_PATH = base_dir / f"{log_prefix}startup.log"
    try:
        _STARTUP_LOG_PATH.unlink(missing_ok=True)
    except OSError:
        pass

    _log_step("_set_dpi_aware()")
    _set_dpi_aware()

    sys.path.insert(0, str(base_dir))

    _log_step("import webview")
    import webview

    from app.web.bridge import WebApi
    from app.web import events

    api = WebApi(base_dir, admin_mode=admin_mode)
    frontend_dir = get_frontend_dir(base_dir)

    debug = "--debug" in sys.argv  # DevTools — только по явному флагу, не в обычном запуске

    _log_step("webview.create_window()")
    window = webview.create_window(
        title,
        str(frontend_dir / "index.html"),
        js_api=api,
        width=1380,
        height=990,
        min_size=(1040, 740),
    )
    events.set_window(window)
    events.event_bridge.start_pump()

    _log_step("webview.start()")
    try:
        webview.start(debug=debug)
    finally:
        _log_step("webview.start() returned (normal close)")


def _run_with_crash_log(admin_mode: bool, log_prefix: str, title: str) -> None:
    try:
        run(admin_mode, log_prefix, title)
    except Exception:
        import traceback
        text = traceback.format_exc()
        _log_step("EXCEPTION:\n" + text)
        base_dir = get_base_dir()
        try:
            (base_dir / f"{log_prefix}crash.log").write_text(text, encoding="utf-8")
        except OSError:
            pass
        try:
            import tkinter.messagebox as messagebox
            messagebox.showerror(
                f"{title} — не удалось запустить",
                f"Подробности сохранены в {log_prefix}startup.log/{log_prefix}crash.log "
                f"рядом с программой.\n\n" + text[-1500:],
            )
        except Exception:
            pass
        raise


if __name__ == "__main__":
    # --admin: временный флаг для разработчика, чтобы проверить admin_mode
    # из исходников без сборки admin_main_web.py отдельным .exe — реальная
    # админ-сборка всегда идёт через admin_main_web.py/admin.spec.
    admin_mode = "--admin" in sys.argv
    _run_with_crash_log(admin_mode=admin_mode, log_prefix="", title=APP_TITLE)
