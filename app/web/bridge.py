"""WebApi — единственный js_api-объект, отдаваемый в webview.create_window().
Методы плоские (без вложенных объектов — см. план миграции: у pywebview
проблемы с object-of-objects в JS), принимают/возвращают только
JSON-совместимые dict/list/str/bool/number/None. Каждая группа методов —
тонкая обёртка над своим app/web/api/*_api.py, который сам оборачивает
существующий бизнес-код (scanner.py, runner.py, ...) — см. критичные файлы в
плане миграции."""
from __future__ import annotations
import sys
import threading
from pathlib import Path

from .api.admin_api import AdminApi
from .api.auth_api import AuthApi
from .api.car_editor_api import CarEditorApi
from .api.chat_api import ChatApi
from .api.install_api import InstallApi
from .api.install_log_api import InstallLogApi
from .api.qr_adb_api import QrAdbApi
from .api.report_api import ReportApi
from .api.scanner_api import ScannerApi
from .api.settings_api import DEBUG_LOG_ALL_MARKER, SettingsApi, is_under_program_files
from .api.submissions_api import SubmissionsApi
from .api.sync_api import SyncApi
from .api.update_api import UpdateApi
from .api.usb_api import UsbApi
from ..adb_utils import find_adb_path
from ..pending_install_logs import (
    append_current, finalize_to_queue, recover_stale_current, seal_abandoned_session, send_one, send_queue,
)
from ..ping_client import get_or_create_client_id
from ..version import APP_VERSION


class WebApi:
    def __init__(self, base_dir: Path, admin_mode: bool = False, is_win7: bool = False):
        self.base_dir = base_dir
        # Win7-сборка (см. main_web_win7.py) — QtWebEngine на старом железе
        # реального техника (не в реестре, а на живой машине с "семёркой")
        # оказался сильно медленнее WebView2: app.js ставит класс "low-perf"
        # на <html>, отключающий backdrop-filter (см. css/tokens.css —
        # blur() позади полупрозрачных элементов на КАЖДОЙ кнопке/диалоге,
        # дорогой per-frame эффект, которого WebView2-сборка не замечает
        # только благодаря аппаратному ускорению).
        self.is_win7 = is_win7
        self.is_mac = sys.platform == "darwin"
        self.cars_dir = base_dir / "cars"
        self.apk_dir = base_dir / "apk"
        self.adb_path = find_adb_path(base_dir)
        # DEBUG-логирование (см. main_web.py:_enable_debug_log_all) — раньше
        # отдельный debug-установщик, теперь маркер-файл переключается прямо
        # из "Настроек" (см. app/web/api/settings_api.py:set_debug_mode) и
        # только читается здесь при каждом запуске. client_id тот же, что и
        # обычный "пульс" (см. ping_client.py) — читается один раз, чтобы
        # main_web.py мог сразу знать папку лога и app.js мог показать его в
        # углу окна (см. app_get_info) для сверки "чей это лог".
        self.debug_mode = (base_dir / DEBUG_LOG_ALL_MARKER).exists()
        self.client_id = get_or_create_client_id(base_dir) if self.debug_mode else ""
        self._scanner = ScannerApi(self.cars_dir, self.apk_dir)
        self._install = InstallApi(self.adb_path, base_dir, self._scanner)
        self._usb = UsbApi(base_dir, self._scanner)
        self._qr_adb = QrAdbApi(base_dir, self.cars_dir, platform=self._install_log_platform())
        self._report = ReportApi(base_dir)
        self._install_log = InstallLogApi(base_dir)
        self._admin = AdminApi(base_dir, self.apk_dir)
        self._auth = AuthApi(base_dir, self._scanner)
        # Раньше отдельная admin-сборка (admin_main_web.py, убрана) — теперь
        # одна и та же программа, admin_mode просто определяет видимость
        # соответствующих кнопок (см. app.js). Единая кнопка "Войти" (см.
        # auth_login ниже) — тихая попытка восстановить сохранённую сессию
        # аккаунта техника (auth_config.py, НЕ логин/пароль в открытом виде,
        # а уже выданный сервером токен) сама включает admin_mode, если у
        # этого аккаунта есть права администратора (см. AuthApi.status —
        # заодно поднимает и кешированную ADMIN_SESSION_COOKIE, которой
        # пользуется весь существующий admin_client.py). Старый отдельный
        # admin_saved_login.json (AdminApi.try_saved_login) оставлен как
        # запасной путь для уже разблокировавших админ-режим ДО этой правки
        # — но новых входов через него больше нет, только через auth_login.
        auth_status = self._auth.status()
        self.auth_email = auth_status.get("email") if auth_status.get("ok") else None
        self.auth_subscriber = bool(auth_status.get("subscriber")) if auth_status.get("ok") else False
        self.admin_mode = (admin_mode or auth_status.get("is_admin", False)
                            or self._admin.try_saved_login().get("ok", False))
        self._car_editor = CarEditorApi(base_dir, self.cars_dir, self._scanner, self._auth)
        self._submissions = SubmissionsApi(base_dir, self.cars_dir, self._scanner)
        self._sync = SyncApi(base_dir, self.cars_dir, self.apk_dir, self._scanner,
                             platform_name=self._install_log_platform())
        self._settings = SettingsApi(base_dir, self.cars_dir, self.apk_dir, self.admin_mode)
        self._update = UpdateApi(base_dir, is_win7=is_win7)
        self._chat = ChatApi(base_dir, self.adb_path, self._auth)
        # Прочный журнал сессий (см. app/pending_install_logs.py) — если
        # прошлый запуск не дошёл до штатного завершения (вылет/принудительное
        # закрытие) или не смог отправить лог (офлайн — например Wi-Fi ADB,
        # см. install_log_send ниже), досылаем/дозапоминаем его именно сейчас,
        # при следующем старте. Фоновым daemon-потоком — не должно задерживать
        # появление окна, а недренированная сетевая попытка (до 30 с на
        # запись) не должна держать процесс живым после закрытия окна.
        threading.Thread(target=self._recover_pending_install_logs, daemon=True).start()
        # См. seal_abandoned_install_log — при закрытии программы её может
        # позвать и хук выхода macOS (main_web.py: _install_macos_quit_cleanup),
        # и finally-блок после webview.start(); запечатываем один раз.
        self._abandoned_log_sealed = False
        self._abandoned_log_path: Path | None = None

    def _recover_pending_install_logs(self) -> None:
        platform = self._install_log_platform()
        recover_stale_current(self.base_dir, platform)
        send_queue(self.base_dir, self._install_log, self.auth_email)

    # -- метаданные окна ------------------------------------------------
    def app_get_info(self) -> dict:
        return {
            "admin_mode": self.admin_mode, "debug_mode": self.debug_mode,
            "client_id": self.client_id, "is_win7": self.is_win7,
            "under_program_files": is_under_program_files(self.base_dir),
            "auth_email": self.auth_email,
            "auth_subscriber": self.auth_subscriber,
            "app_version": APP_VERSION,
        }

    def client_log_error(self, message: str, stack: str = "") -> None:
        """window.onerror/unhandledrejection в app.js шлют сюда любую
        необработанную JS-ошибку (см. events.js/app.js) — раньше такие
        ошибки были видны ТОЛЬКО в DevTools, которую обычный техник открыть
        не может (F12 в обычной сборке не работает), и даже DEBUG_LOG_ALL
        (см. main_web.py:_enable_debug_log_all) их не ловил — он оборачивает
        только сами js_api-методы, а не то, что происходит на стороне JS
        ДО или ПОМИМО вызова моста. Пишем в отдельный файл ВСЕГДА (не только
        при включённом подробном логировании) — разовая JS-ошибка достаточно
        редкое и важное событие, чтобы не требовать заранее включённой
        диагностики, чтобы её поймать."""
        try:
            log_path = self.base_dir / "js_errors.log"
            with open(log_path, "a", encoding="utf-8") as f:
                import time
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
                if stack:
                    f.write(f"{stack}\n")
                f.write("---\n")
        except OSError:
            pass

    # -- sync_api -----------------------------------------------------------
    def sync_startup(self) -> dict:
        return self._sync.startup_sync()

    # -- settings_api -------------------------------------------------------
    def settings_info(self) -> dict:
        return self._settings.info()

    def settings_preferences(self) -> dict:
        return self._settings.preferences()

    def settings_clear_cache(self) -> dict:
        return self._settings.clear_cache()

    def settings_set_preferences(self, preferences: dict) -> dict:
        return self._settings.set_preferences(preferences)

    def settings_set_debug_mode(self, enabled: bool) -> dict:
        return self._settings.set_debug_mode(enabled)

    # -- «Спасибо вам» (окно «Готово!», см. supporters_client.py) -------------
    def supporters_get(self) -> dict:
        from ..supporters_client import fetch_supporters
        return fetch_supporters(self.base_dir) or {}

    # -- update_api -----------------------------------------------------------
    def update_check(self) -> dict:
        return self._update.check()

    def update_install(self, download_url: str) -> dict:
        return self._update.install(download_url)

    # -- scanner_api ------------------------------------------------------
    def scanner_list_cars(self) -> dict:
        return self._scanner.list_cars()

    def scanner_select_model(self, key: str) -> dict:
        return self._scanner.select_model(key)

    def scanner_list_apks(self) -> list:
        return self._scanner.list_apks()

    def scanner_apk_icon(self, path: str):
        # Локальный импорт (не в шапке файла) — apk_icons.py тянет свои
        # собственные кэши/зависимости (urllib, zipfile) только когда
        # интерфейс реально запросил иконку, а не при каждом старте
        # программы вместе с остальным WebApi. См. app/apk_icons.py и
        # docs "Получение иконки APK" в UI-transfer пакете.
        from ..apk_icons import apk_icon
        return apk_icon(path, self.base_dir)

    # -- install_api ------------------------------------------------------
    def install_load_stages(self, model_key: str) -> dict:
        return self._install.load_stages(model_key)

    def install_standard_apks(self, model_key: str, stage_index: int, variant) -> dict:
        return self._install.standard_apks(model_key, stage_index, variant)

    def install_list_devices(self) -> list:
        return self._install.list_devices()

    def install_console_send(self, device, command: str) -> dict:
        return self._install.console_send(device, command)

    def install_wifi_connect(self, port: int, ip: str | None = None) -> dict:
        return self._install.wifi_connect(port, ip)

    def install_scan_wifi(self, port: int) -> list:
        return self._install.scan_wifi(port)

    def install_start_stage(self, model_key: str, stage_index: int, device_serial, selected_apk_paths: list,
                            prefetched: bool = False) -> dict:
        return self._install.start_stage(model_key, stage_index, device_serial, selected_apk_paths, prefetched)

    def install_prefetch_apks(self, model_key: str, stage_index: int, selected_apk_paths: list) -> dict:
        return self._install.prefetch_apks(model_key, stage_index, selected_apk_paths)

    def install_cancel_stage(self) -> dict:
        return self._install.cancel_stage()

    def install_run_action(self, model_key: str, stage_index: int, action_index: int, device_serial,
                            selected_apk_paths: list, prefetched: bool = False) -> dict:
        return self._install.run_action(model_key, stage_index, action_index, device_serial, selected_apk_paths, prefetched)

    def install_prefetch_stage(self, model_key: str, stage_index: int, action_index=None) -> dict:
        return self._install.prefetch_stage(model_key, stage_index, action_index)

    def install_answer_input(self, req_id: str, value) -> dict:
        return self._install.answer_input(req_id, value)

    def install_run_exe(self, exe_path: str) -> dict:
        return self._install.run_exe(exe_path)

    def install_open_video(self, video_path: str) -> dict:
        return self._install.open_video(video_path)

    # -- chat_api (ИИ-чат под логом установки) -------------------------------
    def chat_send(self, history: list, recent_log: list, provider: str | None = None) -> dict:
        return self._chat.chat_send(history, recent_log, provider)

    def chat_confirm_command(self, device, command: str) -> dict:
        return self._chat.chat_confirm_command(device, command)

    # -- usb_api ------------------------------------------------------------
    def usb_list_drives(self, include_all: bool = False) -> list:
        return self._usb.list_drives(include_all)

    # block — номер блока записи этапа «Флешка» (см. usb_api._flash_write_block),
    # None — весь этап по-старому.
    def usb_start(self, model_key: str, stage_index: int, variant, selected_apk_paths: list,
                  drive_letter: str, do_format: bool, filesystem: str, block=None) -> dict:
        return self._usb.start(model_key, stage_index, variant, selected_apk_paths,
                                drive_letter, do_format, filesystem, block)

    def usb_list_items(self, model_key: str, stage_index: int, variant,
                       selected_apk_paths: list, block=None) -> dict:
        return self._usb.list_items(model_key, stage_index, variant, selected_apk_paths, block)

    def usb_cancel(self) -> dict:
        return self._usb.cancel()

    # -- qr_adb_api -----------------------------------------------------
    # Список дисков — тот же usb_list_drives выше, отдельного не заводим.
    def qr_adb_write_prep_flag(self, drive_letter: str) -> dict:
        return self._qr_adb.write_prep_flag(drive_letter)

    def qr_adb_write_flag(self, drive_letter: str) -> dict:
        return self._qr_adb.write_flag(drive_letter)

    def qr_adb_get_password(self, drive_letter: str) -> dict:
        return self._qr_adb.get_password(drive_letter)

    # -- report_api ------------------------------------------------------
    def report_get_info(self) -> dict:
        return self._report.get_info()

    def report_send(self, brand: str, model: str, reason: str, description: str) -> dict:
        # brand/model пустые — обращение к работе программы в целом (кнопка есть и на главной).
        return self._report.send(brand, model, reason, description, platform=self._install_log_platform())

    # -- install_log_api --------------------------------------------------
    def _install_log_platform(self) -> str:
        """"windows"/"win7"/"macos" — раньше тут было только "win7"/
        "windows" (написано до macOS-порта), из-за чего установки с Mac
        уходили в админку ПОМЕЧЕННЫМИ КАК WINDOWS (не терялись, а просто
        неверно подписывались — реальный найденный случай при разборе
        логов в админке). Значение "android" сюда не попадает — Android
        шлёт свои логи отдельно (см. android/.../install_log_bridge.py),
        этот класс — только desktop-сборки."""
        if self.is_win7:
            return "win7"
        if self.is_mac:
            return "macos"
        return "windows"

    def install_log_append(self, token: str, line: str, has_activity: bool = False) -> None:
        """Дозаписывает одну строку в прочный журнал сессии на диске (см.
        app/pending_install_logs.py) — зовётся из stage_wizard.js на КАЖДУЮ
        строку, которую видит техник (и бэкендовые, и JS-собственные — этот
        путь единственный, где строки собираются целиком, в отличие от
        _on_log/_session_log_lines, которые видят только бэкендовые). Не
        должно ничего показывать технику при сбое — как и остальная
        best-effort телеметрия здесь."""
        append_current(self.base_dir, token, line, bool(has_activity))

    def install_log_send(self, brand: str, model: str, modification: str,
                          success: bool, log_text: str, token: str = "") -> None:
        # Фоновым потоком и без возврата результата в JS (тот и не ждёт
        # промис, см. stage_wizard.js: flushSessionLog) — сеть может занять
        # время, а это должно происходить незаметно, не задерживая ничего в
        # интерфейсе (в отличие от report_send, где отправка — явное действие
        # с "Отправка..." в диалоге и есть что ждать).
        platform = self._install_log_platform()
        # Помечаем сессию отправленной СРАЗУ, а не после ответа сети —
        # аварийная отправка того же самого при закрытии окна (см.
        # flush_abandoned_install_log) была бы хуже, чем редкая потеря одной
        # попытки при обрыве сети (best-effort телеметрия).
        self._install.mark_install_log_sent()
        # Запечатываем в очередь ДО попытки отправки (см.
        # pending_install_logs.finalize_to_queue) — сбой сети/процесса после
        # этого момента больше не теряет лог целиком, только откладывает его
        # до следующего запуска (см. WebApi.__init__: _recover_pending_install_logs).
        finalize_to_queue(self.base_dir, token, platform, brand, model, modification,
                           success, log_text)
        # send_queue, а не send_one — заодно опустошает и то, что накопилось
        # раньше (например прошлые попытки без интернета, Wi-Fi ADB), даром:
        # этот поток и так фоновый и никого не задерживает.
        threading.Thread(
            target=send_queue,
            # self.auth_email — живое значение НА МОМЕНТ отправки (обновляется
            # при входе/выходе, см. auth_login/auth_logout выше), не то, что
            # было при старте программы. None, если техник не залогинен —
            # анонимные логи по-прежнему работают как раньше.
            args=(self.base_dir, self._install_log, self.auth_email),
            daemon=True,
        ).start()

    def seal_abandoned_install_log(self) -> Path | None:
        """Запечатывает в очередь на диске (см. app/pending_install_logs.py)
        сессию, которую JS не успела отправить сама (см. install_log_send
        выше), — БЕЗ сети, только локальные файлы: это зовёт и хук выхода
        macOS прямо на главном потоке Cocoa, где сетевой таймаут заморозил бы
        окно (техник по Wi-Fi ADB магнитолы обычно как раз без интернета).
        Идемпотентно — второй вызов возвращает то же, что первый.

        Источник — сначала бэкендовый буфер строк (как и раньше); если он
        пуст — прочный журнал на диске: этапы, где активность логирует только
        JS (QR ADB, запись флешки), бэкендовый буфер не видит вовсе, и раньше
        такие сессии молча оставались в _current.* до следующего запуска —
        а тот слал их как вылет («Предыдущий запуск не завершился штатно»),
        хотя закрытие было штатным (install_logs #471, #589 — Windows)."""
        if self._abandoned_log_sealed:
            return self._abandoned_log_path
        self._abandoned_log_sealed = True
        self._abandoned_log_path = seal_abandoned_session(
            self.base_dir, self._install_log_platform(), self._install.pending_session_log())
        return self._abandoned_log_path

    def flush_abandoned_install_log(self) -> None:
        """Аварийный запасной путь на случай, если окно закрыли раньше, чем
        JS успела сама отправить лог сессии (см. install_log_send выше) —
        зовётся из main_web.py/main_web_win7.py в finally-блоке при закрытии
        окна. Молча ничего не делает, если сессии в процессе не было, или
        JS уже её отправила. Синхронно (как и раньше) — окно к этому моменту
        уже закрыто, но теперь только ОДНУ попытку отправки именно этой
        записи (send_one), а не всю накопленную очередь (send_queue) — иначе
        закрытие могло бы растянуться на несколько таймаутов подряд, если
        очередь успела накопиться. Не отправилось — уйдёт при следующем
        запуске вместе с остальной очередью (send_queue в __init__)."""
        path = self.seal_abandoned_install_log()
        send_one(self.base_dir, path, self._install_log, self.auth_email)

    # -- admin_api ------------------------------------------------------
    def admin_get_info(self) -> dict:
        return self._admin.get_info()

    def admin_login(self, username: str, password: str, remember: bool = False) -> dict:
        # Единственный вход в админку теперь (см. WebApi.__init__ — отдельной
        # admin-сборки больше нет) — успешный логин сразу включает admin_mode
        # для текущего сеанса (см. settings.js: 10 тапов по версии в "О
        # приложении" → dialogs.js: adminLogin.openUnlock).
        result = self._admin.login_only(username, password, remember)
        if result.get("ok"):
            self.admin_mode = True
            self._settings.admin_mode = True
        return result

    def admin_try_saved_login(self) -> dict:
        return self._admin.try_saved_login()

    def admin_forget_saved_login(self) -> dict:
        return self._admin.forget_saved_login()

    def admin_logout(self) -> dict:
        """Выход из режима администратора в текущем сеансе — обратное к
        admin_login (см. выше): чистит сохранённый логин/пароль и
        кешированную сессию (см. AdminApi.logout), и сразу выключает
        admin_mode здесь и в SettingsApi, без перезапуска программы."""
        result = self._admin.logout()
        if result.get("ok"):
            self.admin_mode = False
            self._settings.admin_mode = False
        return result

    def admin_start_upload(self, username: str, password: str) -> dict:
        return self._admin.start_upload(username, password)

    # -- auth_api (единая кнопка "Войти" — аккаунт техника, заодно и
    # admin_mode, если у аккаунта есть права, см. app/web/api/auth_api.py) --
    def auth_register(self, email: str, password: str) -> dict:
        return self._auth.register(email, password)

    def auth_login(self, email: str, password: str) -> dict:
        result = self._auth.login(email, password)
        if result.get("ok"):
            self.auth_email = result.get("email")
            self.auth_subscriber = bool(result.get("subscriber"))
            if result.get("is_admin"):
                self.admin_mode = True
                self._settings.admin_mode = True
        return result

    def auth_logout(self) -> dict:
        result = self._auth.logout()
        if result.get("ok"):
            self.auth_email = None
            self.auth_subscriber = False
            self.admin_mode = False
            self._settings.admin_mode = False
        return result

    def auth_refresh_subscriber(self) -> dict:
        result = self._auth.refresh_subscriber()
        if result.get("ok"):
            self.auth_subscriber = bool(result.get("subscriber"))
        return result

    def auth_change_password(self, current_password: str, new_password: str) -> dict:
        return self._auth.change_password(current_password, new_password)

    def auth_forgot_password(self, email: str) -> dict:
        return self._auth.forgot_password(email)

    def admin_cancel_upload(self) -> dict:
        return self._admin.cancel_upload()

    def admin_list_apk_categories(self) -> list:
        return self._admin.list_apk_categories()

    def admin_create_apk_category(self, name: str) -> dict:
        return self._admin.create_apk_category(name)

    def admin_add_apk(self, file_path: str, name: str, description: str, category: str,
                      mock_location: bool = False) -> dict:
        return self._admin.add_apk(file_path, name, description, category, mock_location)

    def admin_browse_server_cars(self, rel_path: str) -> dict:
        return self._admin.browse_server_cars(rel_path)

    def admin_delete_server_cars_path(self, rel_path: str) -> dict:
        return self._admin.delete_server_cars_path(rel_path)

    def admin_browse_tree(self, root: str, rel_path: str) -> dict:
        return self._admin.browse_tree(root, rel_path)

    def admin_delete_tree_path(self, root: str, rel_path: str) -> dict:
        return self._admin.delete_tree_path(root, rel_path)

    def admin_move_path(self, root: str, from_rel: str, to_rel: str) -> dict:
        return self._admin.move_path(root, from_rel, to_rel)

    def admin_copy_path(self, root: str, from_rel: str, to_rel: str) -> dict:
        return self._admin.copy_path(root, from_rel, to_rel)

    def admin_create_folder(self, root: str, rel_path: str) -> dict:
        return self._admin.create_folder(root, rel_path)

    # -- car_editor_api ---------------------------------------------------
    def car_load_spec(self, model_key: str) -> dict:
        return self._car_editor.load_spec(model_key)

    def car_pick_files(self, kind: str, multiple: bool) -> list:
        return self._car_editor.pick_files(kind, multiple)

    def car_list_shared_usb_folders(self) -> list:
        return self._car_editor.list_shared_usb_folders()

    def car_save_shared_usb_files(self, name: str, files: list) -> dict:
        return self._car_editor.save_shared_usb_files(name, files)

    def car_instruction_default_blocks(self, brand: str, model: str) -> list:
        return self._car_editor.instruction_default_blocks(brand, model)

    def car_instruction_render_preview(self, blocks: list) -> str:
        return self._car_editor.instruction_render_preview(blocks)

    def car_validate_video(self, path: str) -> dict:
        return self._car_editor.validate_video(path)

    def car_get_publish_target(self) -> dict:
        return self._car_editor.get_publish_target(self.admin_mode)

    def car_admin_login(self, base_url: str, username: str, password: str) -> dict:
        return self._car_editor.admin_login(base_url, username, password)

    def car_save(self, spec_data: dict, edit_model_key) -> dict:
        return self._car_editor.save(spec_data, edit_model_key, self.admin_mode)

    def car_cancel_save(self) -> dict:
        return self._car_editor.cancel_save()

    # -- submissions_api ("На модерации", только admin_mode) ----------------
    def submissions_list(self) -> dict:
        return self._submissions.list()

    def submissions_peek(self, name: str) -> dict:
        return self._submissions.peek(name)

    def submissions_stage(self, name: str, brand, model, modification) -> dict:
        return self._submissions.stage(name, brand, model, modification)

    def submissions_publish(self, model_key: str) -> dict:
        return self._submissions.publish(model_key)

    def submissions_reject(self, name: str) -> dict:
        return self._submissions.reject(name)
