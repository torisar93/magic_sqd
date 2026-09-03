"""Обёртка car_generator.py + admin_client.py/submit_client.py для мастера
"Добавить/Изменить машину" — портировано из app/add_car_dialog.py. Разница с
tkinter-версией: полный NewCarSpec живёт как обычный JSON-объект на стороне
JS (StepSpec/StepVariant поля файлов — {"name":.., "path":..} вместо Path),
Python только (1) отдаёт/читает spec_dict в load_spec()/save(), конвертируя
Path<->str на границе, (2) открывает нативные диалоги выбора файлов
(webview.create_file_dialog — обычный JS/HTML не имеет доступа к реальным
путям файловой системы), и (3) выполняет запись на диск + публикацию на
сервер в фоновом потоке (см. _CreateCarProgressDialog в старом коде)."""
from __future__ import annotations
import base64
import json
import mimetypes
import shutil
import threading
from pathlib import Path

import webview

from ..events import event_bridge
from ...admin_client import (AdminClientError, AdminUploadCancelled, clear_cached_session,
                              delete_cars_path, get_cached_session, login, set_cached_session, upload_model)
from ...admin_config import get_admin_base_url
from ...car_generator import (INVALID_NAME_CHARS, ActionSpec, CarGenerationError, NewCarSpec,
                               StandardApkSpec, StepSpec, StepVariant, create_car, load_car_spec, update_car)
from ...content_sync import (clear_local_edit_marker, fetch_manifest, get_base_url, mark_local_edit,
                              sync_model_subfolder)
from ...instruction_html import default_blocks, render_document, validate_video_file
from ...ping_client import get_or_create_client_id
from ...submit_client import SubmitCancelled, SubmitError, submit_model
from ...submit_config import get_submit_config

APK_FILE_TYPES = ("APK (*.apk)", "Все файлы (*.*)")
EXE_FILE_TYPES = ("Исполняемый файл (*.exe)", "Все файлы (*.*)")
IMAGE_FILE_TYPES = ("Изображения (*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp)", "Все файлы (*.*)")
VIDEO_FILE_TYPES = ("Видео H.264 (*.mp4)", "Все файлы (*.*)")
ANY_FILE_TYPES = ("Все файлы (*.*)",)


def _file_dict(path: Path) -> dict:
    return {"name": path.name, "path": str(path)}


def _files_to_dicts(paths: list[Path]) -> list[dict]:
    return [_file_dict(p) for p in paths]


def _apk_entry_to_dict(apk: StandardApkSpec) -> dict:
    """Как _file_dict, но плюс display_name/description для "красивого"
    названия обязательного/необязательного APK (см. StandardApkSpec) —
    "name" здесь по-прежнему имя файла (как у остальных списков файлов, на
    случай если что-то в JS полагается на это единообразие), а
    display_name — то, что реально показывается технику при установке
    (см. app/car_generator.py: StandardApkSpec, _write_model_files)."""
    return {"name": apk.path.name, "path": str(apk.path),
            "display_name": apk.name, "description": apk.description}


def _apk_entries_to_dicts(items: list[StandardApkSpec]) -> list[dict]:
    return [_apk_entry_to_dict(a) for a in items]


def _variant_to_dict(v: StepVariant) -> dict:
    return {"name": v.name, "usb_files": _files_to_dicts(v.usb_files),
            "standard_apks": _apk_entries_to_dicts(v.standard_apks),
            "standard_apks_optional": _apk_entries_to_dicts(v.standard_apks_optional)}


def _step_to_dict(step: StepSpec) -> dict:
    return {
        "type": step.type, "title": step.title, "description": step.description,
        "instruction_blocks": step.instruction_blocks,
        "usb_files": _files_to_dicts(step.usb_files),
        "usb_copy_selected_apks": step.usb_copy_selected_apks,
        "usb_apks_dest": step.usb_apks_dest,
        "usb_shared_folder": step.usb_shared_folder,
        "commands": step.commands,
        "adb_install_selected_apks": step.adb_install_selected_apks,
        "adb_files": _files_to_dicts(step.adb_files),
        "standard_apks": _apk_entries_to_dicts(step.standard_apks),
        "standard_apks_optional": _apk_entries_to_dicts(step.standard_apks_optional),
        "apps_connection": step.apps_connection,
        "apps_install_method": step.apps_install_method,
        "apps_wifi_port": step.apps_wifi_port,
        "actions_connection": step.actions_connection,
        "actions_wifi_port": step.actions_wifi_port,
        "uart_wifi_port": step.uart_wifi_port,
        "exe_file": _file_dict(step.exe_file) if step.exe_file else None,
        "check_options": step.check_options,
        "id": step.id, "next": step.next, "next_options": step.next_options,
        "variants": [_variant_to_dict(v) for v in step.variants],
        "pos_x": step.pos_x, "pos_y": step.pos_y,
        "uart_baudrate": step.uart_baudrate,
        "actions": [
            {"label": a.label, "kind": a.kind, "commands": a.commands, "files": _files_to_dicts(a.files)}
            for a in step.actions
        ],
    }


def _files_from_dicts(items: list[dict]) -> list[Path]:
    return [Path(item["path"]) for item in (items or [])]


def _apk_entry_from_dict(item: dict) -> StandardApkSpec:
    return StandardApkSpec(path=Path(item["path"]), name=item.get("display_name", ""),
                            description=item.get("description", ""))


def _apk_entries_from_dicts(items: list[dict]) -> list[StandardApkSpec]:
    return [_apk_entry_from_dict(item) for item in (items or [])]


def _variant_from_dict(data: dict) -> StepVariant:
    return StepVariant(name=data.get("name", ""),
                        usb_files=_files_from_dicts(data.get("usb_files")),
                        standard_apks=_apk_entries_from_dicts(data.get("standard_apks")),
                        standard_apks_optional=_apk_entries_from_dicts(data.get("standard_apks_optional")))


def _step_from_dict(data: dict) -> StepSpec:
    exe_file = data.get("exe_file")
    return StepSpec(
        type=data["type"], title=data.get("title", ""), description=data.get("description", ""),
        instruction_blocks=data.get("instruction_blocks") or [],
        usb_files=_files_from_dicts(data.get("usb_files")),
        usb_copy_selected_apks=data.get("usb_copy_selected_apks", False),
        usb_apks_dest=data.get("usb_apks_dest", ""),
        usb_shared_folder=data.get("usb_shared_folder", ""),
        commands=data.get("commands") or [],
        adb_install_selected_apks=data.get("adb_install_selected_apks", False),
        adb_files=_files_from_dicts(data.get("adb_files")),
        standard_apks=_apk_entries_from_dicts(data.get("standard_apks")),
        standard_apks_optional=_apk_entries_from_dicts(data.get("standard_apks_optional")),
        apps_connection=data.get("apps_connection", "wired"),
        apps_install_method=data.get("apps_install_method", ""),
        apps_wifi_port=(int(data["apps_wifi_port"]) if data.get("apps_wifi_port") not in (None, "") else None),
        actions_connection=data.get("actions_connection", "wired"),
        actions_wifi_port=(int(data["actions_wifi_port"]) if data.get("actions_wifi_port") not in (None, "") else None),
        uart_wifi_port=(int(data["uart_wifi_port"]) if data.get("uart_wifi_port") not in (None, "") else None),
        exe_file=Path(exe_file["path"]) if exe_file else None,
        check_options=data.get("check_options") or [],
        id=data.get("id"), next=data.get("next"), next_options=data.get("next_options") or [],
        variants=[_variant_from_dict(v) for v in (data.get("variants") or [])],
        pos_x=data.get("pos_x", 0.0), pos_y=data.get("pos_y", 0.0),
        uart_baudrate=data.get("uart_baudrate", 115200),
        actions=[
            ActionSpec(label=a.get("label", ""), kind=a.get("kind", "command"), commands=a.get("commands") or [],
                       files=_files_from_dicts(a.get("files")))
            for a in (data.get("actions") or [])
        ],
    )


class CarEditorApi:
    def __init__(self, base_dir, cars_dir, scanner_api):
        self.base_dir = base_dir
        self.cars_dir = cars_dir
        self._scanner_api = scanner_api
        self._thread: threading.Thread | None = None
        self._cancel_flag = threading.Event()

    # -- загрузка существующей модели (режим редактирования) --------------
    def _sync_instruction_folders(self, model_dir: Path) -> None:
        """Докачивает files/instruction_N/ для каждого instruction-этапа
        ПЕРЕД тем, как load_car_spec() прочитает их в редактор — иначе на
        машине, где эти файлы ещё не синканы (ленивая докачка — см.
        install_api.py: load_stages, тот же приём), load_car_spec() прочтёт
        пустой текст и посчитает инструкцию пустой. При следующем
        сохранении update_car() тогда тихо стирает ссылку "instruction" в
        stages.py, хотя сам файл цел на сервере — реальный инцидент
        (2026-08): 41 модель именно так осталась без инструкции после
        массовой пересборки stages.py на машине с несинканными файлами.
        Сеть недоступна/сервер не настроен — просто работаем с тем, что уже
        есть локально, как и раньше (не должно мешать редактированию)."""
        spec_path = model_dir / "_wizard_spec.json"
        if not spec_path.exists():
            return
        try:
            raw = json.loads(spec_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        steps = raw.get("steps", [])
        if not any(s.get("type") == "instruction" for s in steps):
            return
        base_url = get_base_url(self.base_dir)
        if not base_url:
            return
        try:
            manifest = fetch_manifest(base_url)
        except Exception:  # noqa: BLE001 - сбой сети не должен мешать открыть уже скачанное
            return
        for i, step_data in enumerate(steps, start=1):
            if step_data.get("type") != "instruction":
                continue
            instr_dir = model_dir / "files" / f"instruction_{i}"
            try:
                sync_model_subfolder(self.base_dir, instr_dir, manifest=manifest)
            except Exception:  # noqa: BLE001 - см. докстринг выше
                continue

    def load_spec(self, model_key: str) -> dict:
        model = self._scanner_api.get_model(model_key)
        if model is None:
            return {"error": "unknown model key"}
        self._sync_instruction_folders(model.dir)
        spec = load_car_spec(model.dir, model.brand, model.name, model.modification or "")
        if spec is None:
            return {"error": (
                "Эта модель не была создана мастером «Добавить машину...» (нет "
                "_wizard_spec.json) — редактирование через мастер недоступно, "
                "правьте install.py/stages.py вручную."
            )}
        return {
            "brand": spec.brand, "model": spec.model, "modification": spec.modification,
            "wifi": spec.wifi, "wifi_port": spec.wifi_port, "status": spec.status,
            "steps": [_step_to_dict(s) for s in spec.steps],
        }

    # -- выбор файлов — нативный диалог pywebview (нужны реальные пути) ---
    def pick_files(self, kind: str, multiple: bool) -> list[dict]:
        if kind == "folder":
            # Отдельный нативный диалог (ОС не даёт выбирать вперемешку
            # файлы и папки в одном окне) — для usb_files, где нужно
            # положить на флешку не только отдельные файлы, но и целые
            # папки как есть (см. car_generator.py: _copy_path копирует
            # рекурсивно). multiple здесь фактически игнорируется — папку
            # обычно выбирают по одной за раз.
            result = webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG, allow_multiple=multiple)
        else:
            file_types = {"apk": APK_FILE_TYPES, "exe": EXE_FILE_TYPES,
                          "image": IMAGE_FILE_TYPES, "video": VIDEO_FILE_TYPES}.get(kind, ANY_FILE_TYPES)
            result = webview.windows[0].create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=multiple, file_types=file_types)
        if not result:
            return []
        return [_file_dict(Path(p)) for p in result]

    # -- общие наборы файлов (cars/_shared/) — см. app/install_context.py,
    # app/usb_context.py: ctx.shared_dir, app/car_generator.py:
    # StepSpec.usb_shared_folder. Один и тот же набор файлов на много
    # разных моделей — хранится и синхронизируется один раз, а не
    # копируется в каждую модель отдельно.
    def list_shared_usb_folders(self) -> list[str]:
        shared_dir = self.cars_dir / "_shared"
        if not shared_dir.is_dir():
            return []
        return sorted(
            p.name for p in shared_dir.iterdir()
            if p.is_dir() and not p.name.startswith((".", "__"))
        )

    def save_shared_usb_files(self, name: str, files: list[dict]) -> dict:
        """Копирует выбранные файлы/папки (см. pick_files) в
        cars/_shared/<name>/ — создаёт папку при первом использовании, при
        повторном добавлении файлов в тот же набор — дополняет (не стирает
        то, что там уже было). name — такое же имя папки на диске, поэтому
        проверяется тем же набором запрещённых символов, что и марка/
        модель модели (см. car_generator.create_car)."""
        name = name.strip()
        if not name:
            return {"ok": False, "error": "Не указано имя общего набора."}
        if any(c in INVALID_NAME_CHARS for c in name):
            return {"ok": False, "error": 'Имя не должно содержать символы: < > : " / \\ | ? *'}
        if not files:
            return {"ok": False, "error": "Не выбрано ни одного файла или папки."}
        shared_dir = (self.cars_dir / "_shared" / name).resolve()
        try:
            shared_dir.mkdir(parents=True, exist_ok=True)
            for item in files:
                src = Path(item["path"])
                dst = (shared_dir / src.name).resolve()
                if src.resolve() == dst:
                    continue
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.copy2(src, dst)
        except OSError as exc:
            return {"ok": False, "error": f"Не удалось скопировать: {exc}"}
        return {"ok": True, "name": name}

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
        этого ограничения, поэтому render_preview там годился как есть.

        Видео — исключение: там href не грузится В документ (не <img>, а
        <a target="_blank"> — сама кнопка ничего не встраивает, см.
        instruction_html._render_block), а уходит через pywebview во
        внешний браузер/плеер (webbrowser.open() понимает file:// как
        обычный URL) — значит ограничение Chromium тут вообще не
        применяется, и base64 всего файла (до 150 МБ, см.
        instruction_html.MAX_VIDEO_BYTES) в srcdoc предпросмотра был бы
        просто прямым расточительством памяти/времени без всякой пользы."""
        def href_fn(block: dict) -> str:
            if block.get("type") == "video":
                try:
                    return Path(block["path"]).resolve().as_uri()
                except (OSError, ValueError):
                    return ""
            return self._photo_data_uri(block)
        return render_document(blocks, href_fn)

    @staticmethod
    def _photo_data_uri(block: dict) -> str:
        path = Path(block["path"])
        try:
            data = path.read_bytes()
        except OSError:
            return ""
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    def validate_video(self, path: str) -> dict:
        """Проверка перед принятием файла в видео-блок (см.
        instruction_html.validate_video_file — формат/размер/кодек) —
        вызывается из instruction_editor.js сразу после выбора файла в
        диалоге, до того как он попадёт в список блоков."""
        error = validate_video_file(Path(path))
        return {"ok": error is None, "error": error or ""}

    # -- публикация на сервере после сохранения ----------------------------
    def get_publish_target(self, admin_mode: bool = False) -> dict:
        # admin_mode — реально разблокированный режим администратора (см.
        # app/web/bridge.py: WebApi.admin_mode), а не просто наличие
        # admin.json на диске (он есть в любой сборке — см. _worker за
        # полным обоснованием того же самого фикса). Без этой проверки
        # обычный техник видел запрос логина в админку, хотя должен просто
        # сохранять локально и отправлять на модерацию без какого-либо входа.
        admin_base_url = get_admin_base_url(self.base_dir) if admin_mode else None
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
    def save(self, spec_data: dict, edit_model_key: str | None, admin_mode: bool = False) -> dict:
        if self._thread is not None and self._thread.is_alive():
            return {"ok": False, "error": "Сохранение уже выполняется."}

        edit_model_dir = None
        is_pending = False
        if edit_model_key:
            model = self._scanner_api.get_model(edit_model_key)
            if model is None:
                return {"ok": False, "error": "unknown model key"}
            edit_model_dir = model.dir
            is_pending = model.is_pending

        spec = NewCarSpec(
            brand=spec_data["brand"], model=spec_data["model"],
            modification=spec_data.get("modification", ""),
            wifi=spec_data.get("wifi", False), wifi_port=spec_data.get("wifi_port", 5555),
            steps=[_step_from_dict(s) for s in spec_data.get("steps", [])],
            changelog=spec_data.get("changelog", ""),
            status=spec_data.get("status") or "ok",
        )

        self._cancel_flag = threading.Event()
        self._thread = threading.Thread(target=self._worker, args=(spec, edit_model_dir, is_pending, admin_mode), daemon=True)
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

    def _worker(self, spec: NewCarSpec, edit_model_dir, is_pending: bool = False, admin_mode: bool = False) -> None:
        old_rel_path = None  # относительный путь ДО переименования (если оно было) — для удаления на сервере
        try:
            if edit_model_dir:
                if is_pending:
                    model_dir = update_car(self.cars_dir, edit_model_dir, spec, allow_rename=False)
                else:
                    if edit_model_dir != self.cars_dir and self.cars_dir in edit_model_dir.parents:
                        old_rel_path = edit_model_dir.relative_to(self.cars_dir)
                    model_dir = update_car(self.cars_dir, edit_model_dir, spec)
                    if model_dir == edit_model_dir:
                        old_rel_path = None  # не переименовали — нечего удалять на сервере
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
            "dir": str(model_dir),
        })

        if is_pending:
            # Правка застейдженной заявки клиента (см. app/web/api/
            # submissions_api.py) — "Сохранить" здесь означает только
            # "переписать локальный _pending/<...>/", НЕ публиковать: заявка
            # ещё не одобрена, публикация — отдельное явное действие
            # ("Опубликовать" в том же экране, см. submissions_publish).
            # allow_rename=False выше — марку/модель/модификацию можно
            # поменять в самой заявке (уйдёт в спеке), но папку заявки
            # (base_dir/_pending/<имя>/) это не переносит.
            event_bridge.push({"kind": "car_save_finished", "success": True, "message": "Сохранено."})
            return

        # admin.json (адрес админ-API) шьётся в КАЖДУЮ сборку, не только
        # админскую (см. app/web/bridge.py: WebApi.__init__) — раньше здесь
        # проверялось только "есть ли admin_base_url на диске", а он есть
        # всегда, поэтому обычный техник (без реально разблокированного
        # admin_mode) молча проваливался мимо обеих веток ниже: ветка admin
        # требовала сессии, которой у него нет и не будет, а ветка "submit"
        # даже не рассматривалась (submit_config принудительно None, раз
        # admin_base_url не пуст) — заявка на модерацию никуда не уходила,
        # без единой ошибки в логе. Настоящий гейт — admin_mode (передан из
        # bridge.py, живое значение на момент сохранения, не то, что было
        # при старте программы).
        admin_base_url = get_admin_base_url(self.base_dir) if admin_mode else None
        admin_session_cookie = get_cached_session(admin_base_url) if admin_base_url else None
        submit_config = get_submit_config(self.base_dir) if not admin_base_url else None

        # Локальная копия только что стала отличаться от сервера (обычный
        # техник — публикация ниже её ещё не коснулась, только заявка на
        # модерацию). Ставим маркер заранее — даже если публикация ниже
        # всё-таки случится и снимет его, до этого момента она может упасть
        # (см. except-ветки) или вовсе не случиться (нет сохранённой сессии/
        # submit.json), а между этим моментом и следующей попыткой
        # установки этой модели sync_model_files не должен успеть затереть
        # только что сохранённую правку версией с сервера (см.
        # content_sync.py: mark_local_edit).
        mark_local_edit(model_dir)

        if admin_base_url and admin_session_cookie:
            try:
                # upload_model ниже льёт строго слиянием (см. server/
                # backend.py: _handle_cars_delete — "upload_dir/upload_model
                # никогда сами не удаляют лишнее с сервера") — файл, который
                # только что убрали из спеки (например APK из обязательных),
                # локально пропадает из архива, но на сервере молча
                # остаётся лежать в старой версии модели и потом снова
                # подмешивается в список установки (см. install_api.py:
                # standard_apks — сливает локальную папку с манифестом
                # сервера). Поэтому сначала стираем текущую опубликованную
                # версию этой модели целиком, а затем заливаем актуальную —
                # реальный баг: техник убрал APK из обязательных, сохранил,
                # опубликовал, а этап установки продолжал его предлагать.
                new_rel = str(model_dir.relative_to(self.cars_dir)).replace("\\", "/")
                try:
                    delete_cars_path(admin_base_url, admin_session_cookie, new_rel)
                except AdminClientError as exc:
                    if "(404)" not in str(exc):
                        raise
                    # Модели ещё не было на сервере (первая публикация) —
                    # нечего стирать, это ожидаемо, а не ошибка.
                self._log("Упаковываю в архив...")
                shared_names = {s.usb_shared_folder for s in spec.steps if s.usb_shared_folder}
                extra_dirs = [self.cars_dir / "_shared" / name for name in shared_names]
                upload_model(admin_base_url, admin_session_cookie, self.cars_dir, model_dir,
                             extra_dirs=extra_dirs, log=self._log, check_cancelled=self._check_cancelled)
                self._log("Опубликовано на сервере.")
                # Локальная копия только что стала совпадать с сервером —
                # маркер (если остался от прошлой несинхронизированной
                # правки) больше не нужен (см. mark_local_edit ниже/
                # content_sync.py: sync_model_files/sync_scripts).
                clear_local_edit_marker(model_dir)
                if old_rel_path is not None:
                    old_rel = str(old_rel_path).replace("\\", "/")
                    try:
                        self._log(f"Удаляю старый путь на сервере: {old_rel}...")
                        delete_cars_path(admin_base_url, admin_session_cookie, old_rel)
                        self._log("Старый путь удалён.")
                    except AdminClientError as exc:
                        self._log(f"Не удалось удалить старый путь на сервере ({old_rel}): {exc}")
            except AdminUploadCancelled:
                self._log("Публикация отменена (локально сохранено).")
            except AdminClientError as exc:
                if "истекла" in str(exc):
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
