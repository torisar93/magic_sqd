"""Единственная точка входа (pywebview-интерфейс) — раньше отдельная
admin-сборка (admin_main_web.py/admin.spec) собиралась и ставилась отдельно
от технической; теперь один и тот же exe, admin_mode просто включает
видимость admin-функций (adb-консоль, "Выгрузить на сервер...", см.
app/web/bridge.py: WebApi.__init__ — либо тихий автовход сохранёнными
логином/паролем, либо явная разблокировка через "Настройки" → 10 тапов по
версии → вход, см. app/web/frontend/js/screens/dialogs.js: adminLogin.
openUnlock).

Запуск: python main_web.py (собранный вариант — magic_sqd.exe, см.
magic_sqd.spec)."""
from __future__ import annotations
import ssl
import os
import shutil
import socket
import sys
import tempfile
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
    """Маркер-файл DEBUG_LOG_ALL рядом с exe (см. app/web/api/settings_api.py:
    set_debug_mode — переключается из "Настроек", раньше это была отдельная
    сборка, installer_debug.iss, убрана) включает максимально подробное
    логирование в debug_logs/<client_id>/debug_all.log — каждый вызов моста
    JS<->Python (аргументы, результат) плюс весь stdout/stderr процесса
    (перехватывает необработанные исключения в фоновых потоках, которые
    иначе нигде не видны). client_id — тот же, что видно в углу окна (см.
    app.js), чтобы можно было сверить, чей это лог, если у нескольких людей
    одновременно включена диагностика. Лог САМ уходит на сервер (см.
    _start_debug_uploader ниже) — клиенту не нужно ничего пересылать руками.
    Для диагностики конкретной проблемы, не для постоянного использования —
    объём лога быстро растёт, содержимое может включать пути файлов и т.п.

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
    """Папка, где лежат cars/apk/assets и куда пишутся логи/JSON-файлы
    состояния. Пользователь кладёт/правит их вручную прямо здесь (на Windows
    и при запуске из исходников — это ВСЕГДА папка exe/скрипта, а не
    _internal, см. get_frontend_dir — там наоборот).

    ИСКЛЮЧЕНИЕ — macOS, реальный собранный .app (не запуск из исходников):
    "рядом с exe" там означает Contents/MacOS/ — то есть ВНУТРИ подписанного
    бандла. Любая запись туда ПОСЛЕ сборки (а cars/apk синкаются с сервера
    буквально на первом же запуске, и продолжают дозаписываться при каждом
    следующем) ломает печать (codesign --verify --deep --strict: "a sealed
    resource is missing or invalid", реальный случай — подтверждено на живой
    установленной копии, множество "file added" внутри cars/apk).
    Gatekeeper после этого сильнее ограничивает приложение именно при
    обычном запуске через Finder/Dock (реальный случай: ADB переставал
    видеть подключённое по USB устройство ИМЕННО при таком запуске, но не
    при запуске из Терминала через `open` — тот исторически не так строго
    проверяет подпись, как полноценный LaunchServices-запуск). Тот же класс
    бага, что и "app is damaged" в v1.0.2 (см. platform_paths.py:
    bundled_tools_root — tools_mac перенесён в Contents/Resources/), только
    вторая половина: там был неподвижный бандленный инструмент (один раз
    на сборке), здесь — постоянно дозаписываемые пользовательские данные (на
    каждом запуске). Внутри подписанного бандла им оставаться нельзя в
    принципе — под изменяемые данные приложения на macOS есть отдельное,
    предназначенное для этого место, вне бандла: ~/Library/Application
    Support/. Печать после этого никогда не портится, т.к. сам .app на диске
    больше не меняется после установки. Первый запуск после обновления
    существующей установки перекачает cars/apk заново (contents_sync и так
    рассчитан на "докачать то, чего не хватает" — не новая возможность)."""
    if getattr(sys, "frozen", False):
        if sys.platform == "darwin":
            return Path.home() / "Library" / "Application Support" / "MagicSQD"
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_frontend_dir(base_dir: Path) -> Path:
    """Статические файлы app/web/frontend/ (см. datas= в magic_sqd.spec) —
    в onedir-сборке PyInstaller 6+ лежат в _internal/ рядом с
    exe, НЕ прямо рядом с ним (в отличие от cars/apk/tools/assets — эти
    пользователь трогает руками, поэтому им обязательно быть прямо у exe).
    При запуске из исходников (sys.frozen нет) — просто base_dir, как раньше.

    На Windows sys._MEIPASS — официальный способ PyInstaller найти данные
    бандла (_internal/ рядом с exe), и здесь он используется как раньше.

    На macOS — НЕ через sys._MEIPASS (см. app/platform_paths.py:
    bundled_tools_root, тот же самый найденный баг 2026-09-22 и то же
    объяснение — для актуального PyInstaller это Contents/Frameworks/, а не
    Contents/Resources/, где реально лежат datas=). Здесь это работало
    только по случайности: PyInstaller сам кладёт в Frameworks/ символические
    ссылки почти на всё, что собрал (app/, certifi/, base_library.zip —
    см. `ls -la Contents/Frameworks/` у собранного .app), включая app/, так
    что app/web/frontend через эту ссылку находился — но это деталь
    реализации текущей версии PyInstaller, а не гарантия. Считаем путь
    напрямую от sys.executable, как и в bundled_tools_root — не зависит от
    того, чту именно PyInstaller решит считать _MEIPASS."""
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        root = Path(sys.executable).resolve().parent.parent / "Resources"
    else:
        meipass = getattr(sys, "_MEIPASS", None)
        root = Path(meipass) if meipass else base_dir
    return root / "app" / "web" / "frontend"


def get_webview_profile_dir(base_dir: Path) -> Path:
    """Постоянный, но полностью технический профиль WebView2.

    По умолчанию pywebview открывает WebView2 в private mode и удаляет его
    профиль после каждого закрытия. Это заставляет Chromium заново создавать
    служебные базы на следующем запуске. Храним их вне папки программы (она
    может быть Program Files), а «Очистить кэш» удаляет эту папку целиком.
    """
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "MagicSQD" / "webview_profile"
    return base_dir / "webview_profile"


def _raise_http_server_backlog() -> None:
    """socketserver.TCPServer.request_queue_size (очередь ещё НЕ принятых
    подключений на уровне ОС/сокета) по умолчанию в CPython равен 5 — это
    и есть request_queue_size у WSGIServer, на котором стоит локальный
    сервер pywebview (webview/http.py: ThreadedAdapter/WSGIServer). index.
    html сейчас разом запрашивает 35 файлов (было 19 на момент появления
    http_server=True в v0.5.8 — см. историю коммитов) — WebView2/Chromium
    открывает НЕСКОЛЬКО соединений на источник параллельно, и когда их
    одновременно в очереди оказывается больше 5, лишние получают
    net::ERR_CONNECTION_REFUSED (подтверждено скриншотом DevTools: стабильно
    несколько CSS падают с этой ошибкой на КАЖДОМ запуске, не только
    изредка). Раньше при 19 файлах очередь из 5 обычно не успевала
    заполниться (WSGIRequestHandler отрабатывает быстро), сейчас — почти
    всегда переполняется. Поднимаем лимит с большим запасом (Windows
    допускает SOMAXCONN значительно выше) ДО того как локальный сервер
    стартует — сам класс общий на процесс, других TCPServer тут нет."""
    import socketserver
    socketserver.TCPServer.request_queue_size = 128


def _start_local_http_server_ready(frontend_url: str, port: int, timeout: float = 5.0) -> None:
    """pywebview с http_server=True сам стартует свой локальный сервер в
    ФОНОВОМ потоке (см. webview/http.py:BottleServer.start_server —
    server.thread.start(), без какого-либо ожидания) и сразу возвращает
    управление — WinForms/WebView2 в основном потоке почти сразу после
    этого начинает грузить index.html и все 20+ <link>/<script> из <head>.
    Реальный подтверждённый случай (скриншот DevTools, вкладка Console):
    первые несколько CSS/JS падали с net::ERR_CONNECTION_REFUSED — сам
    сервер ещё не успел забиндиться на порт к этому моменту. Чистая гонка
    между двумя потоками без какой-либо синхронизации на стороне pywebview.

    Стартуем сервер ЗДЕСЬ сами, через тот же http.start_global_server,
    что использует pywebview изнутри (см. __init__.py:start — проверяет
    `http.global_server is None` и просто НЕ стартует его повторно, раз
    видит, что мы это уже сделали) — и ждём, пока порт реально не начнёт
    принимать соединения, ПРЕЖДЕ чем создавать окно и запускать навигацию."""
    import webview.http as webview_http
    webview_http.start_global_server(http_port=port, urls=[frontend_url])
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            try:
                sock.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.02)
    _log_step(f"local http server on port {port} did not become ready within {timeout}s")


def _pick_safe_http_port() -> int:
    """pywebview (http_server=True, без явного http_port) сам выбирает
    порт для своего локального сервера через random.randint(1023, 65535)
    (см. webview/http.py:_get_random_port) — ничего не зная про список
    "небезопасных" портов Chromium (net/base/port_util.cc: 6000, 6566,
    6665-6669, 6697, 10080 и др. — исторически связанные с другими
    протоколами, IRC и т.п.). Если пал на такой порт — WebView2 отказывается
    открывать страницу вообще (ERR_UNSAFE_PORT), окно остаётся пустым/со
    страницей ошибки. Реальный подтверждённый случай — интерфейс ломался
    СЛУЧАЙНО на части запусков без единой ошибки в нашем логе (сама
    Chromium-проверка происходит до того, как наш код вообще получает
    управление). OS-диапазон динамических/эфемерных портов (49152-65535,
    IANA) целиком выше всех известных "небезопасных" портов Chromium —
    привязка к порту 0 и чтение назначенного ОС порта гарантированно
    остаётся в этом диапазоне, тот же стандартный приём, что и везде для
    "выдай мне свободный порт", без необходимости дублировать список
    Chromium вручную."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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


def _webview2_temp_root() -> str:
    """Родительский каталог для webview2_storage ниже (см. webview.start:
    storage_path) — обычно tempfile.gettempdir(), но на Windows это путь ВНУТРИ
    профиля пользователя (C:\\Users\\<имя>\\AppData\\Local\\Temp), а WebView2 —
    нативный компонент (.NET/WinForms → COM → Chromium), а не чистый Python:
    в отличие от os/pathlib (Unicode-API с PEP 529), у него бывают проблемы,
    если имя пользователя не ASCII (кириллица) или слишком длинное — реальная
    жалоба клиента, лицензионная Windows: диалог WebView2 "Не удалось создать
    каталог данных" с путём вида C:\\Users\\E106~1\\... — САМА Windows уже
    показывает укороченное 8.3-имя в тексте ошибки, то есть WebView2 споткнулся
    именно на этом сегменте пути.

    GetShortPathNameW возвращает ASCII-безопасный АЛИАС уже существующего
    каталога (не создаёт новый путь и не меняет прав доступа — тот же самый
    каталог на диске, просто под другим именем), поэтому это самый безопасный
    из возможных фиксов. Если короткие имена отключены на этом томе (fsutil
    8dot3name) или что-то ещё пошло не так — тихо возвращаемся к обычному
    tempfile.gettempdir(), как было раньше (ничего не ломаем для остальных)."""
    base = tempfile.gettempdir()
    if sys.platform != "win32":
        return base
    import ctypes
    try:
        buf = ctypes.create_unicode_buffer(260)
        length = ctypes.windll.kernel32.GetShortPathNameW(base, buf, len(buf))
        if length and length < len(buf):
            return buf.value
    except Exception as exc:
        _log_step(f"_webview2_temp_root: GetShortPathNameW failed: {exc}")
    return base


def _ensure_renderer(base_dir: Path, title: str, force_qt: bool = False) -> dict | None:
    """Без WebView2 Runtime окно pywebview открывается пустым белым — сам
    рендерер (msedgewebview2.exe) не запускается вовсе, ни один JS не
    выполняется, и ни startup.log, ни debug-лог это не ловят как ошибку
    (реальный случай на машине техника без WebView2 — см. app/
    webview2_check.py). Инсталлятор уже сам молча ставит рантайм при
    установке/обновлении (installer.iss) — эта проверка
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

    Возвращает {"gui": None} (обычный WebView2/winforms-путь на Windows или
    нативный бэкенд pywebview на другой ОС), {"gui": "qt"} (резервный
    Qt-движок — уже готов к использованию, sys.path настроен) или None, если
    продолжать нельзя (отказ или ни один вариант не сработал) — тогда run()
    выходит, не пытаясь создать окно, которое всё равно будет пустым.

    Вся эта проверка — обход конкретной болячки WebView2 на Windows (см.
    docstring выше). На macOS у pywebview свой нативный бэкенд (WKWebView
    через pyobjc) — часть самой ОС, отдельно не ставится и никогда не
    отсутствует, поэтому там проверять и чинить нечего."""
    if sys.platform != "win32":
        return {"gui": None}

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
    """log_prefix/title остались параметрами с прошлых времён отдельной
    admin-сборки (main.py/admin_main.py у tkinter-версии, потом
    admin_main_web.py) — сейчас всегда "" и APP_TITLE (единственный
    оставшийся вызывающий — блок __main__ ниже), но трогать сигнатуру ради
    этого не стали: работает и так, а параметры дёшевы."""
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

    # pywebview с private_mode=True и БЕЗ явного storage_path сам вычисляет
    # cache_dir как tempfile.TemporaryDirectory().name (см. webview/platforms/
    # winforms.py:init_storage) — берёт только .name и тут же теряет ссылку
    # на сам объект TemporaryDirectory, а у него есть finalizer, который
    # удаляет папку при сборке мусора. В CPython это может сработать (через
    # refcounting) почти сразу же, ДО того как WebView2 успеет создать в ней
    # свои файлы профиля — реальный найденный случай: интерфейс на части
    # запусков рендерился с пустыми/съехавшими блоками СЛУЧАЙНО, без единой
    # ошибки в debug_all.log (там только наши js_api-вызовы, до этой гонки
    # внутри самого pywebview/WinForms он не достаёт). Явный storage_path
    # (сделанный через mkdtemp, у которого никакого finalizer нет) убирает
    # эту гонку полностью, сохраняя тот же смысл private_mode — свежий
    # профиль на каждый запуск, чистим сами после закрытия окна.
    # dir=_webview2_temp_root() — см. её докстринг: ASCII-безопасный алиас
    # %TEMP% на случай не-ASCII/длинного имени пользователя Windows.
    webview2_storage = Path(tempfile.mkdtemp(prefix="magicsqd_webview2_", dir=_webview2_temp_root()))

    _raise_http_server_backlog()
    http_port = _pick_safe_http_port()
    _log_step(f"starting local http server on port {http_port} and waiting for it to be ready")
    _start_local_http_server_ready(str(frontend_dir / "index.html"), http_port)

    _log_step("webview.start()")
    try:
        webview.start(
            debug=debug,
            # Статические файлы фронтенда отдаём через встроенный loopback
            # HTTP-сервер pywebview, а не прямо из file://. В постоянном
            # профиле WebView2 file:// мог подхватывать устаревшие CSS/JS
            # после обновления; отдельный локальный origin корректно
            # инвалидирует эти ресурсы и не открывает приложение в сеть.
            # Сам сервер уже запущен и проверен на готовность выше
            # (_start_local_http_server_ready) — pywebview здесь видит
            # http.global_server уже выставленным и не стартует повторно.
            http_server=True,
            # Без этого pywebview сам выбирает порт случайно на всём
            # диапазоне 1023-65535 (см. _pick_safe_http_port выше) — и
            # изредка попадает на порт из "чёрного списка" Chromium
            # (ERR_UNSAFE_PORT), тогда WebView2 отказывается открывать
            # страницу вообще. Подтверждённый реальный случай — окно
            # показывало "Не удаётся открыть эту страницу" на порте 6668
            # (диапазон IRC, 6665-6669, в списке небезопасных у Chromium).
            http_port=http_port,
            # Постоянный профиль не дал выигрыша в скорости, но на практике
            # залипал на старых локальных CSS/JS между обновлениями. Для
            # установщика важнее всегда открыть актуальный интерфейс.
            private_mode=True,
            storage_path=str(webview2_storage),
            **({"gui": renderer["gui"]} if renderer["gui"] else {}),
        )
    finally:
        _log_step("webview.start() returned (normal close)")
        shutil.rmtree(webview2_storage, ignore_errors=True)
        # adb.exe запускает свой собственный фоновый сервер-процесс при
        # первом обращении (adb devices/connect/...) и живёт отдельно от
        # клиентских вызовов — без явного "adb kill-server" он остаётся в
        # диспетчере задач и после закрытия программы, держа файлы (мешает
        # пересборке при разработке, а на машине техника просто висит
        # процессом без дела).
        from app.adb_utils import kill_server
        kill_server(api.adb_path)
        _log_step("kill_server() done")
        api.flush_abandoned_install_log()
        _log_step("flush_abandoned_install_log() done")
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
    # --admin: только для разработки — сразу включить admin_mode из
    # исходников, не проходя разблокировку через "Настройки" (10 тапов по
    # версии → вход, см. app/web/bridge.py: WebApi.__init__). В собранном
    # виде разблокировка постоянная (тихий автовход сохранёнными логином/
    # паролем при следующих запусках) — отдельной admin-сборки больше нет.
    admin_mode = "--admin" in sys.argv
    _run_with_crash_log(admin_mode=admin_mode, log_prefix="", title=APP_TITLE)
