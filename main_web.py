"""Точка входа технической сборки (pywebview-интерфейс). Тот же app/web/,
что и admin_main_web.py — разница только в admin_mode=False и именах
лог-файлов, как раньше у main.py/admin_main.py (см. app/gui.py: admin_mode
прячет adb-консоль и показывает кнопку "Выгрузить на сервер...").

Запуск: python main_web.py (собранный вариант — magic_sqd.exe, см.
magic_sqd.spec)."""
from __future__ import annotations
import ssl
import sys
import time
from pathlib import Path

import certifi

# Реальный случай в поле: на машине техника (заблокированной политикой
# администратора — см. app/webview2_check.py) все HTTPS-запросы программы
# (проверка обновлений, докачка контента, качание резервного Qt-движка)
# падали с "certificate has expired", хотя сам сертификат magicsqd.ru
# действителен и в браузере на той же машине открывается нормально —
# у Windows на этой машине не обновлён системный список корневых
# сертификатов (тот же класс политики, что блокирует WebView2/Windows
# Update), а Python по умолчанию на Windows проверяет цепочку именно через
# него. certifi — независимый, регулярно обновляемый набор доверенных
# корней (тот же, что использует pip/requests) — переключаем на него ssl-
# контекст по умолчанию ОДИН РАЗ здесь, до того как что-либо успеет открыть
# HTTPS-соединение (urllib.request И http.client.HTTPSConnection оба берут
# контекст по умолчанию именно через ssl._create_default_https_context,
# если не передан свой — значит одной правки хватает на весь процесс,
# менять каждый вызов по отдельности не нужно).
ssl._create_default_https_context = lambda: ssl.create_default_context(cafile=certifi.where())

from app.version import APP_VERSION  # noqa: E402 — лёгкий модуль без зависимостей, безопасно тут

# Версия — в заголовке окна (единственное место, где её вообще было видно
# раньше — нигде: ни в интерфейсе, ни в диалоге "О программе", которого
# просто нет; реальный запрос — не понять, что установлено, без похода в
# файл version.json рядом с exe).
APP_TITLE = f"Magic SQD — установщик приложений для мультимедиа (v{APP_VERSION})"

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


def _enable_debug_log_all(base_dir: Path, api):
    """DEBUG-СБОРКА (не часть обычного релиза, см. installer_debug.iss):
    маркер-файл DEBUG_LOG_ALL рядом с exe (см. app/web/bridge.py:
    WebApi.debug_mode) включает максимально подробное логирование в
    debug_logs/<client_id>/debug_all.log — каждый вызов моста JS<->Python
    (аргументы, результат) плюс весь stdout/stderr процесса (перехватывает
    необработанные исключения в фоновых потоках, которые иначе нигде не
    видны). client_id — тот же, что видно в углу окна (см. app.js), чтобы
    можно было сверить, чей это лог, если дебаг-сборку ставят нескольким
    людям одновременно. Лог САМ уходит на сервер (см. _start_debug_uploader
    ниже) — клиенту не нужно ничего пересылать руками. Временная мера для
    диагностики конкретной проблемы (флешка не видна на Windows 11), не для
    обычных пользователей — объём лога быстро растёт, содержимое может
    включать пути файлов и т.п.

    Возвращает функцию upload_once() (для финальной отправки при закрытии
    окна, см. run()) или None, если DEBUG_LOG_ALL не включён."""
    if not api.debug_mode:
        return None

    import functools
    import traceback

    log_dir = base_dir / "debug_logs" / (api.client_id or "unknown")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "debug_all.log"

    def log_line(text: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {text}\n")
        except OSError:
            pass

    # Перехватываем ВЕСЬ stdout/stderr процесса — иначе исключения в
    # фоновых потоках (usb_api/install_api-воркеры и т.п.) видны только как
    # traceback в консоли, которой у собранного exe нет (console=False).
    stream = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream

    for name in dir(api):
        if name.startswith("_"):
            continue
        attr = getattr(api, name)
        if not callable(attr):
            continue

        def make_wrapper(method_name, method):
            @functools.wraps(method)
            def wrapper(*args, **kwargs):
                log_line(f"CALL {method_name}(args={args!r}, kwargs={kwargs!r})")
                try:
                    result = method(*args, **kwargs)
                except Exception:
                    log_line(f"EXC {method_name}:\n{traceback.format_exc()}")
                    raise
                log_line(f"RESULT {method_name} -> {result!r}"[:2000])
                return result
            return wrapper

        setattr(api, name, make_wrapper(name, attr))

    log_line(f"=== DEBUG_LOG_ALL включён, client_id={api.client_id} ===")

    return _start_debug_uploader(base_dir, api.client_id, log_path, log_line)


def _start_debug_uploader(base_dir: Path, client_id: str, log_path: Path, log_line):
    """Периодически (и один раз при закрытии окна, см. run()) отправляет
    ТЕКУЩЕЕ содержимое debug_all.log на сервер (POST /diagnostics — тот же
    приём, что и app/report_client.py, сохраняется файлом на сервере, см.
    server/backend.py: DIAGNOSTICS_DIR) — клиенту не нужно ничего искать и
    пересылать руками, лог сам долетает по мере того, как пишется. Если
    submit.json не настроен или сети нет — тихо не отправляет, локальный
    файл остаётся в любом случае.

    Возвращает upload_once() для финального вызова при закрытии окна."""
    import json
    import threading
    import urllib.error
    import urllib.request

    from app.submit_config import get_submit_config

    config = get_submit_config(base_dir)
    if not config:
        log_line("DEBUG_LOG_ALL: нет submit.json — лог только локально, на сервер не уйдёт.")
        return lambda: None

    def upload_once() -> None:
        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        if not text:
            return
        body = json.dumps({"client_id": client_id, "text": text}).encode("utf-8")
        request = urllib.request.Request(
            config.diagnostics_url, data=body, method="POST",
            headers={"X-Submit-Key": config.submit_key, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as resp:
                resp.read()
        except (urllib.error.URLError, OSError):
            pass  # сеть недоступна/сервер не ответил — попробуем на следующем цикле

    UPLOAD_INTERVAL_SECONDS = 60

    def worker() -> None:
        while True:
            time.sleep(UPLOAD_INTERVAL_SECONDS)
            upload_once()

    threading.Thread(target=worker, daemon=True).start()
    return upload_once


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


def _fix_window_position_win32(window) -> None:
    """Подстраховка поверх pywebview's штатного центрирования — на Windows
    (WinForms-бэкенд) конвертация координат экрана в физические пиксели
    берёт DPI-масштаб ТЕКУЩЕГО монитора окна В МОМЕНТ СОЗДАНИЯ (см.
    webview/platforms/winforms.py: Window._scale через GetDpiForWindow,
    используется и в StartPosition-логике конструктора, и в Window.move()).
    У пользователя два монитора с РАЗНЫМ DPI-масштабом (главный — 1.25x,
    второй, повёрнутый, смещён в отрицательные Y — 1.0x) — если WinForms
    создаёт HWND не на главном мониторе, масштаб для пересчёта берётся не
    тот, и итоговая позиция получается неверной (проверено: и штатный
    x=y=None/CenterScreen, и ручной расчёт через webview.screens, и
    screen=<Screen главного монитора> — все давали окно частично за
    пределами видимой области, каждый раз по-своему). GetSystemMetrics
    SM_CXSCREEN/SM_CYSCREEN — размеры ИМЕННО главного монитора в физических
    пикселях всегда однозначно (это системная константа, не зависит от
    того, на каком мониторе создано конкретное окно) — двигаем окно туда
    напрямую через SetWindowPos, в обход pywebview-обёртки целиком."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = window.native.Handle.ToInt32()
        screen_w = user32.GetSystemMetrics(0)  # SM_CXSCREEN
        screen_h = user32.GetSystemMetrics(1)  # SM_CYSCREEN
        rect = wintypes.RECT()
        user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect))
        win_w = rect.right - rect.left
        win_h = rect.bottom - rect.top
        x = max(0, (screen_w - win_w) // 2)
        y = max(0, (screen_h - win_h) // 2)
        SWP_NOSIZE = 0x0001
        SWP_NOZORDER = 0x0004
        user32.SetWindowPos(wintypes.HWND(hwnd), None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER)
        _log_step(
            f"_fix_window_position_win32: screen={screen_w}x{screen_h} "
            f"window={win_w}x{win_h} -> ({x},{y})"
        )
    except Exception as exc:
        _log_step(f"_fix_window_position_win32 failed: {exc}")


def _ensure_renderer(base_dir: Path, title: str, force_qt: bool = False) -> dict | None:
    """Без WebView2 Runtime окно pywebview открывается пустым белым — сам
    рендерер (msedgewebview2.exe) не запускается вовсе, ни один JS не
    выполняется, и ни startup.log, ни debug-лог это не ловят как ошибку
    (реальный случай на машине техника без WebView2 — см. app/
    webview2_check.py). Инсталлятор уже сам молча ставит рантайм при
    установке/обновлении (installer.iss/admin_installer.iss) — эта проверка
    здесь на случай уже установленных копий без этого шага (старые версии)
    или если у инсталлятора в момент установки не было интернета.

    Если и это не помогло (или техник отказался) — предлагаем резервный
    движок отображения на Qt WebEngine (см. app/qt_fallback.py): свой
    встроенный Chromium, вообще не зависящий от WebView2, интерфейс тот же
    самый (app/web/frontend/ как есть). Скачивается один раз (~200 МБ), при
    следующих запусках уже стоит — проверяем это ПЕРВЫМ делом, до WebView2,
    чтобы машина, которая уже прошла этот путь, не проходила его заново.

    force_qt — флаг --qt-fallback (см. run()): реальный случай — WebView2
    ЕСТЬ в реестре (is_installed() честно отвечает True), но администратор
    домена заблокировал сам процесс msedgewebview2.exe политикой — окно всё
    равно пустое, а наша проверка реестра в принципе не может отличить
    "не установлен" от "установлен, но заблокирован". Штатный путь такую
    машину никогда сам не поймает (is_installed() ведь не соврал), поэтому
    нужен ручной обход — техник (или тот, кто ему помогает удалённо)
    запускает `magic_sqd.exe --qt-fallback`, пропуская проверку WebView2
    целиком.

    Диалоги — tkinter, а не наш собственный UI: он не зависит ни от
    WebView2, ни от Qt, поэтому единственный, кто гарантированно способен
    что-то показать именно в этой ситуации.

    Возвращает {"gui": None} (обычный WebView2/winforms-путь), {"gui": "qt"}
    (резервный Qt-движок — уже готов к использованию, sys.path настроен) или
    None, если продолжать нельзя (отказ или ни один вариант не сработал) —
    тогда run() выходит, не пытаясь создать окно, которое всё равно будет
    пустым."""
    from app import qt_fallback
    from app.webview2_check import install_silently, is_installed

    if qt_fallback.is_downloaded(base_dir):
        _log_step("резервный Qt-движок уже установлен, использую его")
        qt_fallback.prepare_sys_path(base_dir)
        return {"gui": "qt"}

    if force_qt:
        _log_step("--qt-fallback: пропускаю проверку WebView2, качаю резервный движок")
        ok = qt_fallback.download_and_extract(base_dir, log=_log_step)
        if not ok:
            return None
        qt_fallback.prepare_sys_path(base_dir)
        return {"gui": "qt"}

    if is_installed():
        return {"gui": None}

    import tkinter as tk
    import tkinter.messagebox as messagebox

    root = tk.Tk()
    root.withdraw()
    proceed = messagebox.askyesno(
        title,
        "Не найден компонент WebView2 Runtime — без него окно программы "
        "останется пустым белым.\n\nУстановить сейчас? Нужен интернет, "
        "займёт около минуты.",
    )
    root.destroy()
    if proceed:
        _log_step("устанавливаю WebView2...")
        bootstrapper = base_dir / "tools" / "MicrosoftEdgeWebview2Setup.exe"
        ok = install_silently(bootstrapper)
        _log_step(f"webview2 install ok={ok}")
        if ok:
            return {"gui": None}

    root = tk.Tk()
    root.withdraw()
    proceed = messagebox.askyesno(
        title,
        "WebView2 Runtime поставить не удалось.\n\nУстановить резервный "
        "движок отображения (~200 МБ, устанавливается один раз)? Это "
        "позволит запустить программу без WebView2.",
    )
    root.destroy()
    if not proceed:
        return None

    _log_step("скачиваю резервный Qt-движок...")
    ok = qt_fallback.download_and_extract(base_dir, log=_log_step)
    _log_step(f"qt fallback install ok={ok}")
    if not ok:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            title,
            "Не удалось установить резервный движок отображения.\n\n"
            "Проверьте подключение к интернету и запустите программу заново.",
        )
        root.destroy()
        return None

    qt_fallback.prepare_sys_path(base_dir)
    return {"gui": "qt"}


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
    debug_upload_once = _enable_debug_log_all(base_dir, api)
    frontend_dir = get_frontend_dir(base_dir)

    _log_step("renderer check")
    renderer = _ensure_renderer(base_dir, title, force_qt="--qt-fallback" in sys.argv)
    if renderer is None:
        _log_step("ни WebView2, ни резервный движок недоступны, выходим без создания окна")
        return

    debug = "--debug" in sys.argv  # DevTools — только по явному флагу, не в обычном запуске

    _log_step(f"webview.create_window() gui={renderer['gui']!r}")
    window = webview.create_window(
        title,
        str(frontend_dir / "index.html"),
        js_api=api,
        width=1380,
        height=990,
        min_size=(1040, 740),
    )
    if renderer["gui"] is None:
        # _fix_window_position_win32 читает window.native.Handle — это
        # WinForms/.NET-специфика (см. саму функцию), у Qt-окна (PySide6)
        # такого атрибута нет вовсе. Функция и так безопасна (except
        # Exception внутри), но при резервном движке просто нечего чинить
        # (мультимониторный DPI-сдвиг — особенность именно WinForms-бэкенда).
        window.events.shown += lambda: _fix_window_position_win32(window)
    events.set_window(window)
    events.event_bridge.start_pump()

    _log_step("webview.start()")
    try:
        webview.start(debug=debug, **({"gui": renderer["gui"]} if renderer["gui"] else {}))
    finally:
        _log_step("webview.start() returned (normal close)")
        # adb.exe запускает свой собственный фоновый сервер-процесс при
        # первом обращении (adb devices/connect/...) и живёт отдельно от
        # клиентских вызовов — без явного "adb kill-server" он остаётся в
        # диспетчере задач и после закрытия программы, держа файлы (мешает
        # пересборке при разработке, а на машине техника просто висит
        # процессом без дела).
        from app.adb_utils import kill_server
        kill_server(api.adb_path)
        _log_step("kill_server() done")
        if debug_upload_once is not None:
            debug_upload_once()
            _log_step("debug_upload_once() done")


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
