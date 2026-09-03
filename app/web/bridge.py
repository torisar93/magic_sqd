"""WebApi — единственный js_api-объект, отдаваемый в webview.create_window().
Методы плоские (без вложенных объектов — см. план миграции: у pywebview
проблемы с object-of-objects в JS), принимают/возвращают только
JSON-совместимые dict/list/str/bool/number/None. Каждая группа методов —
тонкая обёртка над своим app/web/api/*_api.py, который сам оборачивает
существующий бизнес-код (scanner.py, runner.py, ...) — см. критичные файлы в
плане миграции."""
from __future__ import annotations
from pathlib import Path

from .api.admin_api import AdminApi
from .api.car_editor_api import CarEditorApi
from .api.install_api import InstallApi
from .api.qr_adb_api import QrAdbApi
from .api.report_api import ReportApi
from .api.scanner_api import ScannerApi
from .api.settings_api import DEBUG_LOG_ALL_MARKER, SettingsApi, is_under_program_files
from .api.submissions_api import SubmissionsApi
from .api.sync_api import SyncApi
from .api.update_api import UpdateApi
from .api.usb_api import UsbApi
from ..adb_utils import find_adb_path
from ..ping_client import get_or_create_client_id


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
        self._qr_adb = QrAdbApi(self.cars_dir)
        self._report = ReportApi(base_dir)
        self._admin = AdminApi(base_dir, self.apk_dir)
        # Раньше отдельная admin-сборка (admin_main_web.py, убрана) — теперь
        # одна и та же программа, admin_mode просто определяет видимость
        # соответствующих кнопок (см. app.js). Помимо явного параметра (dev-
        # флаг main_web.py --admin) — тихая попытка входа сохранёнными
        # логином/паролем (см. admin_config.save_saved_login,
        # AdminApi.try_saved_login): если на этой машине уже разблокировали
        # функции администратора раньше (см. admin_login ниже — "Настройки"
        # → 10 тапов по версии → вход, всегда с запоминанием), они снова
        # включатся сами, без повторного входа. Без admin.json (обычная
        # свежая установка) try_saved_login тут же возвращает {"ok": False}
        # без сетевого запроса — старту это ничего не стоит.
        self.admin_mode = admin_mode or self._admin.try_saved_login().get("ok", False)
        self._car_editor = CarEditorApi(base_dir, self.cars_dir, self._scanner)
        self._submissions = SubmissionsApi(base_dir, self.cars_dir, self._scanner)
        self._sync = SyncApi(base_dir, self.cars_dir, self.apk_dir, self._scanner)
        self._settings = SettingsApi(base_dir, self.cars_dir, self.apk_dir, self.admin_mode)
        self._update = UpdateApi(base_dir, is_win7=is_win7)

    # -- метаданные окна ------------------------------------------------
    def app_get_info(self) -> dict:
        return {
            "admin_mode": self.admin_mode, "debug_mode": self.debug_mode,
            "client_id": self.client_id, "is_win7": self.is_win7,
            "under_program_files": is_under_program_files(self.base_dir),
        }

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

    def install_start_stage(self, model_key: str, stage_index: int, device_serial, selected_apk_paths: list) -> dict:
        return self._install.start_stage(model_key, stage_index, device_serial, selected_apk_paths)

    def install_cancel_stage(self) -> dict:
        return self._install.cancel_stage()

    def install_run_action(self, model_key: str, stage_index: int, action_index: int, device_serial,
                            selected_apk_paths: list) -> dict:
        return self._install.run_action(model_key, stage_index, action_index, device_serial, selected_apk_paths)

    def install_answer_input(self, req_id: str, value) -> dict:
        return self._install.answer_input(req_id, value)

    def install_run_exe(self, exe_path: str) -> dict:
        return self._install.run_exe(exe_path)

    def install_open_video(self, video_path: str) -> dict:
        return self._install.open_video(video_path)

    # -- usb_api ------------------------------------------------------------
    def usb_list_drives(self, include_all: bool = False) -> list:
        return self._usb.list_drives(include_all)

    def usb_start(self, model_key: str, stage_index: int, variant, selected_apk_paths: list,
                  drive_letter: str, do_format: bool, filesystem: str) -> dict:
        return self._usb.start(model_key, stage_index, variant, selected_apk_paths,
                                drive_letter, do_format, filesystem)

    def usb_cancel(self) -> dict:
        return self._usb.cancel()

    # -- qr_adb_api -----------------------------------------------------
    # Список дисков — тот же usb_list_drives выше, отдельного не заводим.
    def qr_adb_write_flag(self, drive_letter: str) -> dict:
        return self._qr_adb.write_flag(drive_letter)

    def qr_adb_get_password(self, drive_letter: str) -> dict:
        return self._qr_adb.get_password(drive_letter)

    # -- report_api ------------------------------------------------------
    def report_get_info(self) -> dict:
        return self._report.get_info()

    def report_send(self, brand: str, model: str, reason: str, description: str) -> dict:
        return self._report.send(brand, model, reason, description)

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

    def admin_cancel_upload(self) -> dict:
        return self._admin.cancel_upload()

    def admin_list_apk_categories(self) -> list:
        return self._admin.list_apk_categories()

    def admin_create_apk_category(self, name: str) -> dict:
        return self._admin.create_apk_category(name)

    def admin_add_apk(self, file_path: str, name: str, description: str, category: str) -> dict:
        return self._admin.add_apk(file_path, name, description, category)

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
