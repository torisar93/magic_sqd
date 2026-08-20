"""Точка входа для отдельной Windows 7 (x86)-сборки (см. installer_win7_x86.iss,
magic_sqd_win7.spec) — НЕ используется обычной x64/x86-сборкой (main_web.py).

Почему отдельный файл, а не ветка внутри main_web.py: на настоящей
Windows 7 WebView2 в принципе недоступен — не наша логика чинить,
официальный инсталлятор WebView2 Runtime от Microsoft сам больше не
запускается на этой ОС (реальный случай, проверено на живой машине,
"точка входа не найдена" — GetPackagesByPackageFamily/PssQuerySnapshot,
Windows 8.1+ API, которых в kernel32.dll настоящей "семёрки" просто нет).
Единственный рабочий движок — PyQt5/Qt5 (последняя версия Qt, ещё
поддерживающая Windows 7 — см. magic_sqd_win7.spec за тем, почему именно
PyQt5, а не PySide2/LGPL — короткая версия: у pywebview 6.2.1 настоящий
баг с PySide2+QtWebEngine, JS-мост не работает, с PyQt5 — работает,
проверено), вшитый прямо в сборку. Вся логика выбора/проверки WebView2 в
main_web.py (WebView2-диалоги, скачивание резервного PySide6 и т.п.) этой
сборке просто не нужна и не должна там жить — здесь путь всегда один:
сразу Qt, без сети, без реестра, без обращения к WebView2 вообще.

Собирается ОБЯЗАТЕЛЬНО 32-битным Python 3.8 (.venv-win7) — последняя
версия CPython с официальной поддержкой Windows 7 (Python 3.9+ требует
Windows 8.1+, см. PEP 11). main_web.py (Python 3.9-3.12 синтаксис,
from __future__ import annotations делает его безопасным и под 3.8 тоже)
импортируется как есть — переиспользуем общие мелочи (get_base_dir,
get_frontend_dir, _set_dpi_aware, _enable_debug_log_all, APP_TITLE), а не
дублируем их, но вся ЛОГИКА выбора рендерера — своя, ниже."""
import sys

import main_web


def run() -> None:
    base_dir = main_web.get_base_dir()
    main_web._STARTUP_LOG_PATH = base_dir / "startup.log"
    try:
        main_web._STARTUP_LOG_PATH.unlink(missing_ok=True)
    except OSError:
        pass

    main_web._log_step("_set_dpi_aware()")
    main_web._set_dpi_aware()

    sys.path.insert(0, str(base_dir))

    main_web._log_step("import webview")
    import webview

    from app.web.bridge import WebApi
    from app.web import events

    api = WebApi(base_dir, admin_mode=False)
    debug_upload_once = main_web._enable_debug_log_all(base_dir, api)
    frontend_dir = main_web.get_frontend_dir(base_dir)

    main_web._log_step("Windows 7 (x86)-сборка: рендерер всегда Qt (PyQt5), WebView2 не проверяется")

    debug = "--debug" in sys.argv  # DevTools — только по явному флагу, не в обычном запуске

    # Меньше, чем в main_web.py (1380x990) — старые ноутбуки, для которых и
    # существует эта сборка (см. шапку файла), часто имеют разрешение вроде
    # 1280x800 — реальный случай, окно 1380x990 туда просто не помещалось
    # целиком. 1200x740 с запасом влезает под панель задач/рамку окна.
    window = webview.create_window(
        main_web.APP_TITLE,
        str(frontend_dir / "index.html"),
        js_api=api,
        width=1200,
        height=740,
        min_size=(1000, 650),
    )
    # _fix_window_position_win32 (см. main_web.py) — WinForms-специфика
    # (window.native.Handle), у Qt-окна такого атрибута нет — не вызываем.
    events.set_window(window)
    events.event_bridge.start_pump()

    main_web._log_step("webview.start()")
    try:
        webview.start(debug=debug, gui="qt")
    finally:
        main_web._log_step("webview.start() returned (normal close)")
        from app.adb_utils import kill_server
        kill_server(api.adb_path)
        main_web._log_step("kill_server() done")
        if debug_upload_once is not None:
            debug_upload_once()
            main_web._log_step("debug_upload_once() done")


def _run_with_crash_log() -> None:
    try:
        run()
    except Exception:
        import traceback
        text = traceback.format_exc()
        main_web._log_step("EXCEPTION:\n" + text)
        base_dir = main_web.get_base_dir()
        try:
            (base_dir / "crash.log").write_text(text, encoding="utf-8")
        except OSError:
            pass
        try:
            import tkinter.messagebox as messagebox
            messagebox.showerror(
                f"{main_web.APP_TITLE} — не удалось запустить",
                f"Подробности сохранены в startup.log/crash.log рядом с программой.\n\n"
                + text[-1500:],
            )
        except Exception:
            pass
        raise


if __name__ == "__main__":
    _run_with_crash_log()
