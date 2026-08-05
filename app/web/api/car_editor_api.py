"""Обёртка car_generator.py + admin_client.py/submit_client.py для мастера
"Добавить/Изменить машину" — портировано из app/add_car_dialog.py. Разница с
tkinter-версией: полный NewCarSpec живёт как обычный JSON-объект на стороне
JS (StepSpec/StepVariant поля файлов — {"name":.., "path":..} вместо Path),
Python только (1) отдаёт/читает spec_dict в load_spec()/save(), конвертируя
Path<->str на границе, (2) открывает нативные диалоги выбора файлов
(webview.create_file_dialog — обычный JS/HTML не имеет доступа к реальным
путям файловой системы), и (3) выполняет запись на диск + публикацию на
сервер в фоновом потоке (см. _CreateCarProgressDialog в старом коде)."""
import base64
import mimetypes
import threading
from pathlib import Path

import webview

from ..events import event_bridge
from ...admin_client import (AdminClientError, AdminUploadCancelled, clear_cached_session,
                              get_cached_session, login, set_cached_session, upload_model)
from ...admin_config import get_admin_base_url
from ...car_generator import (CarGenerationError, NewCarSpec, StepSpec, StepVariant,
                               create_car, load_car_spec, update_car)
from ...instruction_html import default_blocks, render_document
from ...ping_client import get_or_create_client_id
from ...submit_client import SubmitCancelled, SubmitError, submit_model
from ...submit_config import get_submit_config

APK_FILE_TYPES = ("APK (*.apk)", "Все файлы (*.*)")
EXE_FILE_TYPES = ("Исполняемый файл (*.exe)", "Все файлы (*.*)")
IMAGE_FILE_TYPES = ("Изображения (*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp)", "Все файлы (*.*)")
ANY_FILE_TYPES = ("Все файлы (*.*)",)


def _file_dict(path: Path) -> dict:
    return {"name": path.name, "path": str(path)}


def _files_to_dicts(paths: list[Path]) -> list[dict]:
    return [_file_dict(p) for p in paths]


def _variant_to_dict(v: StepVariant) -> dict:
    return {"name": v.name, "usb_files": _files_to_dicts(v.usb_files),
            "standard_apks": _files_to_dicts(v.standard_apks)}


def _step_to_dict(step: StepSpec) -> dict:
    return {
        "type": step.type, "title": step.title, "description": step.description,
        "instruction_blocks": step.instruction_blocks,
        "usb_files": _files_to_dicts(step.usb_files),
        "usb_copy_selected_apks": step.usb_copy_selected_apks,
        "commands": step.commands,
        "adb_install_selected_apks": step.adb_install_selected_apks,
        "adb_files": _files_to_dicts(step.adb_files),
        "standard_apks": _files_to_dicts(step.standard_apks),
        "exe_file": _file_dict(step.exe_file) if step.exe_file else None,
        "check_var": step.check_var, "check_options": step.check_options,
        "condition_var": step.condition_var, "condition_values": step.condition_values,
        "variants": [_variant_to_dict(v) for v in step.variants],
    }


def _files_from_dicts(items: list[dict]) -> list[Path]:
    return [Path(item["path"]) for item in (items or [])]


def _variant_from_dict(data: dict) -> StepVariant:
    return StepVariant(name=data.get("name", ""),
                        usb_files=_files_from_dicts(data.get("usb_files")),
                        standard_apks=_files_from_dicts(data.get("standard_apks")))


def _step_from_dict(data: dict) -> StepSpec:
    exe_file = data.get("exe_file")
    return StepSpec(
        type=data["type"], title=data.get("title", ""), description=data.get("description", ""),
        instruction_blocks=data.get("instruction_blocks") or [],
        usb_files=_files_from_dicts(data.get("usb_files")),
        usb_copy_selected_apks=data.get("usb_copy_selected_apks", False),
        commands=data.get("commands") or [],
        adb_install_selected_apks=data.get("adb_install_selected_apks", False),
        adb_files=_files_from_dicts(data.get("adb_files")),
        standard_apks=_files_from_dicts(data.get("standard_apks")),
        exe_file=Path(exe_file["path"]) if exe_file else None,
        check_var=data.get("check_var", ""), check_options=data.get("check_options") or [],
        condition_var=data.get("condition_var", ""), condition_values=data.get("condition_values") or [],
        variants=[_variant_from_dict(v) for v in (data.get("variants") or [])],
    )


class CarEditorApi:
    def __init__(self, base_dir, cars_dir, scanner_api):
        self.base_dir = base_dir
        self.cars_dir = cars_dir
        self._scanner_api = scanner_api
        self._thread: threading.Thread | None = None
        self._cancel_flag = threading.Event()

    # -- загрузка существующей модели (режим редактирования) --------------
    def load_spec(self, model_key: str) -> dict:
        model = self._scanner_api.get_model(model_key)
        if model is None:
            return {"error": "unknown model key"}
        spec = load_car_spec(model.dir, model.brand, model.name, model.modification or "")
        if spec is None:
            return {"error": (
                "Эта модель не была создана мастером «Добавить машину...» (нет "
                "_wizard_spec.json) — редактирование через мастер недоступно, "
                "правьте install.py/stages.py вручную."
            )}
        return {
            "brand": spec.brand, "model": spec.model, "modification": spec.modification,
            "wifi": spec.wifi, "wifi_port": spec.wifi_port,
            "steps": [_step_to_dict(s) for s in spec.steps],
        }

    # -- выбор файлов — нативный диалог pywebview (нужны реальные пути) ---
    def pick_files(self, kind: str, multiple: bool) -> list[dict]:
        file_types = {"apk": APK_FILE_TYPES, "exe": EXE_FILE_TYPES,
                      "image": IMAGE_FILE_TYPES}.get(kind, ANY_FILE_TYPES)
        dialog_type = webview.OPEN_DIALOG
        result = webview.windows[0].create_file_dialog(
            dialog_type, allow_multiple=multiple, file_types=file_types)
        if not result:
            return []
        return [_file_dict(Path(p)) for p in result]

    # -- блочный редактор "Инструкция" (app/instruction_html.py, чистые данные) --
    def instruction_default_blocks(self, brand: str, model: str) -> list[dict]:
        return default_blocks(brand, model)

    def instruction_render_preview(self, blocks: list[dict]) -> str:
        """Как instruction_html.render_preview, но фото — data:-URI, а не
        file://: страница мастера рендерится в WebView2 с http://-адреса
        (см. main_web.py — pywebview обслуживает frontend/ через встроенный
        bottle-сервер), а Chromium блокирует загрузку file:// ресурсов из
        не-file:// документа. render_document (та же чистая функция
        instruction_html.py) уже параметризована по href_fn для ровно
        такого случая — tkinterweb (не Chromium) мог грузить file:// без
        этого ограничения, поэтому render_preview там годился как есть."""
        return render_document(blocks, self._photo_data_uri)

    @staticmethod
    def _photo_data_uri(block: dict) -> str:
        path = Path(block["path"])
        try:
            data = path.read_bytes()
        except OSError:
            return ""
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    # -- публикация на сервере после сохранения ----------------------------
    def get_publish_target(self) -> dict:
        admin_base_url = get_admin_base_url(self.base_dir)
        if admin_base_url:
            return {"mode": "admin", "base_url": admin_base_url,
                    "session_cached": get_cached_session(admin_base_url) is not None}
        submit_config = get_submit_config(self.base_dir)
        return {"mode": "submit" if submit_config else "none", "base_url": None, "session_cached": False}

    def admin_login(self, base_url: str, username: str, password: str) -> dict:
        try:
            cookie = login(base_url, username, password)
        except AdminClientError as exc:
            return {"ok": False, "error": str(exc)}
        set_cached_session(base_url, cookie)
        return {"ok": True}

    # -- сохранение (создание/правка) — фоновый поток, прогресс через events --
    def save(self, spec_data: dict, edit_model_key: str | None) -> dict:
        if self._thread is not None and self._thread.is_alive():
            return {"ok": False, "error": "Сохранение уже выполняется."}

        edit_model_dir = None
        if edit_model_key:
            model = self._scanner_api.get_model(edit_model_key)
            if model is None:
                return {"ok": False, "error": "unknown model key"}
            edit_model_dir = model.dir

        spec = NewCarSpec(
            brand=spec_data["brand"], model=spec_data["model"],
            modification=spec_data.get("modification", ""),
            wifi=spec_data.get("wifi", False), wifi_port=spec_data.get("wifi_port", 5555),
            steps=[_step_from_dict(s) for s in spec_data.get("steps", [])],
            changelog=spec_data.get("changelog", ""),
        )

        self._cancel_flag = threading.Event()
        self._thread = threading.Thread(target=self._worker, args=(spec, edit_model_dir), daemon=True)
        self._thread.start()
        return {"ok": True}

    def cancel_save(self) -> dict:
        self._cancel_flag.set()
        return {"ok": True}

    def _check_cancelled(self):
        if self._cancel_flag.is_set():
            raise AdminUploadCancelled("Отменено пользователем.")

    def _log(self, message) -> None:
        event_bridge.push({"kind": "car_save_log", "text": str(message)})

    def _worker(self, spec: NewCarSpec, edit_model_dir) -> None:
        try:
            if edit_model_dir:
                update_car(edit_model_dir, spec)
                model_dir = edit_model_dir
            else:
                model_dir = create_car(self.cars_dir, spec)
        except CarGenerationError as exc:
            event_bridge.push({"kind": "car_save_finished", "success": False, "message": str(exc)})
            return
        except OSError as exc:
            event_bridge.push({"kind": "car_save_finished", "success": False,
                                "message": f"Ошибка при сохранении файлов: {exc}"})
            return

        event_bridge.push({
            "kind": "car_saved",
            "brand": spec.brand, "model": spec.model, "modification": spec.modification,
        })

        admin_base_url = get_admin_base_url(self.base_dir)
        admin_session_cookie = get_cached_session(admin_base_url) if admin_base_url else None
        submit_config = get_submit_config(self.base_dir) if not admin_base_url else None

        if admin_base_url and admin_session_cookie:
            try:
                self._log("Упаковываю в архив...")
                upload_model(admin_base_url, admin_session_cookie, self.cars_dir, model_dir,
                             log=self._log, check_cancelled=self._check_cancelled)
                self._log("Опубликовано на сервере.")
            except AdminUploadCancelled:
                self._log("Публикация отменена (локально сохранено).")
            except AdminClientError as exc:
                clear_cached_session(admin_base_url)
                self._log(f"Не опубликовано на сервере: {exc}")
        elif submit_config:
            try:
                client_id = get_or_create_client_id(self.base_dir)
                self._log("Отправляю на проверку разработчику...")
                submit_model(model_dir, spec.brand, spec.model, submit_config,
                             modification=spec.modification, client_id=client_id,
                             log=self._log, check_cancelled=self._check_cancelled)
            except SubmitCancelled:
                self._log("Отправка на проверку отменена (локально сохранено).")
            except SubmitError as exc:
                self._log(f"Не отправлено на проверку: {exc}")

        event_bridge.push({"kind": "car_save_finished", "success": True, "message": "Готово."})
