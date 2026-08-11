"""WebApi — единственный js_api-объект, отдаваемый в webview.create_window().
Методы плоские (без вложенных объектов — см. план миграции: у pywebview
проблемы с object-of-objects в JS), принимают/возвращают только
JSON-совместимые dict/list/str/bool/number/None. Каждая группа методов —
тонкая обёртка над своим app/web/api/*_api.py, который сам оборачивает
существующий бизнес-код (scanner.py, runner.py, ...) — см. критичные файлы в
плане миграции."""
from pathlib import Path

from .api.admin_api import AdminApi
from .api.car_editor_api import CarEditorApi
from .api.install_api import InstallApi
from .api.report_api import ReportApi
from .api.scanner_api import ScannerApi
from .api.sync_api import SyncApi
from .api.update_api import UpdateApi
from .api.usb_api import UsbApi
from ..adb_utils import find_adb_path
from ..ping_client import get_or_create_client_id

# DEBUG-СБОРКА (не часть обычного релиза, см. installer_debug.iss/
# main_web.py:_enable_debug_log_all) — маркер-файл рядом с exe включает
# подробное логирование КАЖДОГО вызова моста + весь stdout/stderr в
# base_dir/debug_logs/<client_id>/debug_all.log. client_id тот же, что и
# обычный "пульс" (см. ping_client.py) — читается здесь один раз, чтобы
# main_web.py мог сразу знать папку лога и app.js мог показать его в углу
# окна (см. app_get_info) для сверки "чей это лог", если дебаг-сборку
# ставят нескольким людям одновременно.
DEBUG_LOG_ALL_MARKER = "DEBUG_LOG_ALL"


class WebApi:
    def __init__(self, base_dir: Path, admin_mode: bool = False):
        self.base_dir = base_dir
        self.admin_mode = admin_mode
        self.cars_dir = base_dir / "cars"
        self.apk_dir = base_dir / "apk"
        self.adb_path = find_adb_path(base_dir)
        self.debug_mode = (base_dir / DEBUG_LOG_ALL_MARKER).exists()
        self.client_id = get_or_create_client_id(base_dir) if self.debug_mode else ""
        self._scanner = ScannerApi(self.cars_dir, self.apk_dir)
        self._install = InstallApi(self.adb_path, base_dir, self._scanner)
        self._usb = UsbApi(base_dir, self._scanner)
        self._report = ReportApi(base_dir)
        self._admin = AdminApi(base_dir, self.apk_dir)
        self._car_editor = CarEditorApi(base_dir, self.cars_dir, self._scanner)
        self._sync = SyncApi(base_dir, self.cars_dir, self.apk_dir, self._scanner)
        self._update = UpdateApi(base_dir)

    # -- метаданные окна ------------------------------------------------
    def app_get_info(self) -> dict:
        return {"admin_mode": self.admin_mode, "debug_mode": self.debug_mode, "client_id": self.client_id}

    # -- sync_api -----------------------------------------------------------
    def sync_startup(self) -> dict:
        return self._sync.startup_sync()

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

    def install_standard_apks(self, model_key: str, stage_index: int, variant) -> list:
        return self._install.standard_apks(model_key, stage_index, variant)

    def install_list_devices(self) -> list:
        return self._install.list_devices()

    def install_console_send(self, device, command: str) -> dict:
        return self._install.console_send(device, command)

    def install_wifi_connect(self, port: int, ip: str | None = None) -> dict:
        return self._install.wifi_connect(port, ip)

    def install_start_stage(self, model_key: str, stage_index: int, device_serial, selected_apk_paths: list) -> dict:
        return self._install.start_stage(model_key, stage_index, device_serial, selected_apk_paths)

    def install_cancel_stage(self) -> dict:
        return self._install.cancel_stage()

    def install_answer_input(self, req_id: str, value) -> dict:
        return self._install.answer_input(req_id, value)

    def install_run_exe(self, exe_path: str) -> dict:
        return self._install.run_exe(exe_path)

    # -- usb_api ------------------------------------------------------------
    def usb_list_drives(self, include_all: bool = False) -> list:
        return self._usb.list_drives(include_all)

    def usb_start(self, model_key: str, stage_index: int, variant, selected_apk_paths: list,
                  drive_letter: str, do_format: bool, filesystem: str) -> dict:
        return self._usb.start(model_key, stage_index, variant, selected_apk_paths,
                                drive_letter, do_format, filesystem)

    def usb_cancel(self) -> dict:
        return self._usb.cancel()

    # -- report_api ------------------------------------------------------
    def report_get_info(self) -> dict:
        return self._report.get_info()

    def report_send(self, brand: str, model: str, reason: str, description: str) -> dict:
        return self._report.send(brand, model, reason, description)

    # -- admin_api ------------------------------------------------------
    def admin_get_info(self) -> dict:
        return self._admin.get_info()

    def admin_login(self, username: str, password: str) -> dict:
        return self._admin.login_only(username, password)

    def admin_start_upload(self, username: str, password: str) -> dict:
        return self._admin.start_upload(username, password)

    def admin_cancel_upload(self) -> dict:
        return self._admin.cancel_upload()

    def admin_list_apk_categories(self) -> list:
        return self._admin.list_apk_categories()

    def admin_create_apk_category(self, name: str) -> dict:
        return self._admin.create_apk_category(name)

    def admin_delete_apk_category(self, name: str) -> dict:
        return self._admin.delete_apk_category(name)

    def admin_add_apk(self, file_path: str, name: str, description: str, category: str) -> dict:
        return self._admin.add_apk(file_path, name, description, category)

    def admin_browse_server_cars(self, rel_path: str) -> dict:
        return self._admin.browse_server_cars(rel_path)

    def admin_delete_server_cars_path(self, rel_path: str) -> dict:
        return self._admin.delete_server_cars_path(rel_path)

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

    def car_get_publish_target(self) -> dict:
        return self._car_editor.get_publish_target()

    def car_admin_login(self, base_url: str, username: str, password: str) -> dict:
        return self._car_editor.admin_login(base_url, username, password)

    def car_save(self, spec_data: dict, edit_model_key) -> dict:
        return self._car_editor.save(spec_data, edit_model_key)

    def car_cancel_save(self) -> dict:
        return self._car_editor.cancel_save()
