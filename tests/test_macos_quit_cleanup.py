"""main_web._install_macos_quit_cleanup — уборка при выходе через Cmd+Q/Dock.

Выход через [NSApp terminate:] (так устроен и пункт Quit в меню pywebview)
заканчивается exit() прямо из Cocoa: ни finally вокруг цикла событий, ни
atexit Python не выполняются — поэтому незакрытая сессия установки на macOS
раньше всегда доезжала до сервера как вылет («Предыдущий запуск не
завершился штатно», install_logs #590/#594). Проверяется настоящим циклом
событий Cocoa в отдельном процессе (сам процесс завершает terminate:), без
окон и иконки в Dock. Только macOS — в CI (Linux) пропускается."""
from __future__ import annotations
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Cocoa terminate: есть только на macOS")

REPO = Path(__file__).resolve().parents[1]


def _run_until_terminate(tmp_path: Path, body: str) -> set[str]:
    pytest.importorskip("AppKit")
    out = tmp_path / "marks"
    out.mkdir(parents=True)
    header = textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(REPO)!r})
        from pathlib import Path
        import AppKit
        import main_web
        out = Path({str(out)!r})
        def mark(name):
            (out / name).write_text("1")
        app = AppKit.NSApplication.sharedApplication()
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyProhibited)
        app.performSelector_withObject_afterDelay_("terminate:", None, 0.2)
    """)
    subprocess.run([sys.executable, "-c", header + textwrap.dedent(body)], timeout=60, check=True)
    return {p.name for p in out.iterdir()}


def test_cleanup_runs_on_terminate_although_finally_does_not(tmp_path):
    marks = _run_until_terminate(tmp_path, """
        import atexit
        atexit.register(lambda: mark("atexit"))
        main_web._install_macos_quit_cleanup(lambda: mark("cleanup"))
        try:
            app.run()
            mark("run_returned")
        finally:
            mark("finally")
    """)
    assert marks == {"cleanup"}


def test_quit_while_other_threads_use_openssl_does_not_crash(tmp_path):
    # Отчёт о сбое 2026-09-23: exit() из Cocoa запускал OPENSSL_cleanup, пока
    # другие потоки работали с OpenSSL (поток, создававший SSLContext с
    # certifi, упал SIGSEGV). Без os._exit в хуке этот сценарий падает с
    # кодом 139 примерно в 2 запусках из 3 — несколько прогонов подряд почти
    # наверняка ловят регрессию (check=True ниже роняет тест на ненулевом коде).
    for attempt in range(4):
        marks = _run_until_terminate(tmp_path / str(attempt), """
            import ssl, threading, certifi
            def hammer():
                while True:
                    ssl.create_default_context(cafile=certifi.where())
            for _ in range(8):
                threading.Thread(target=hammer, daemon=True).start()
            main_web._install_macos_quit_cleanup(lambda: mark("cleanup"))
            app.run()
        """)
        assert marks == {"cleanup"}


def test_failing_cleanup_does_not_break_quit(tmp_path):
    marks = _run_until_terminate(tmp_path, """
        def cleanup():
            mark("cleanup_started")
            raise RuntimeError("уборка упала")
        main_web._install_macos_quit_cleanup(cleanup)
        app.run()
    """)
    # check=True выше — процесс всё равно завершился штатно (код 0).
    assert marks == {"cleanup_started"}
