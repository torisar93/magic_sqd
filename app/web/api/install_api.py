"""Обёртка runner.py/install_context.py/stage_runner.py для stage wizard.
Настоящий run(ctx) конкретного этапа НИКОГДА не сериализуется в JSON — наружу
уходят только описательные поля этапа (см. _stage_to_dict), запуск идёт по
stage_index через install_load_stages(model_key), а сам run_fn достаётся
заново из stages.py на стороне Python (см. start_stage). Прогресс/ask_input
идут через app/web/events.py — тот же приём, что раньше был queue.Queue +
self.after(100, ...) в stage_wizard.py, только транспорт — evaluate_js вместо
Tk-виджетов (см. план миграции)."""
from __future__ import annotations
import base64
import mimetypes
import re
import subprocess
import threading
from pathlib import Path

from ..events import event_bridge, input_broker
from ...adb_utils import (SERVER_LEVEL_COMMANDS, TOP_LEVEL_COMMANDS, Adb, get_default_gateway_ip,
                           list_devices, scan_for_adb_hosts)
from ...content_sync import sync_model_files
from ...runner import InstallRunner
from ...scanner import scan_apk_dir
from ...stage_runner import StageDefinitionError, load_stages, stage_instruction_html_path
from .scanner_api import apk_to_dict

_IMG_SRC_RE = re.compile(r'(<img\b[^>]*\bsrc=")([^"]+)(")', re.IGNORECASE)


def _inline_relative_images(html_text: str, base_dir: Path) -> str:
    """instruction.html на диске ссылается на фото ОТНОСИТЕЛЬНЫМ путём
    ("images/foo.jpg", см. instruction_html.save_instruction — так вся
    папка модели остаётся переносимой сама по себе). Раньше это грузилось
    в tkinterweb (Tkhtml3) прямо с диска — здесь же мы отдаём HTML в JS и
    показываем его через `<iframe srcdoc>` на странице, обслуживаемой
    pywebview с http://127.0.0.1:.../ (см. main_web.py) — Chromium не
    грузит file:// ресурсы из не-file:// документа, поэтому относительный
    src просто не найдётся. Подменяем каждый такой src на data:-URI —
    единственный способ показать фото без отдельного HTTP-роута на cars/."""
    def _replace(match: re.Match) -> str:
        prefix, src, suffix = match.group(1), match.group(2), match.group(3)
        if src.startswith(("http://", "https://", "data:")):
            return match.group(0)
        path = (base_dir / src).resolve()
        try:
            data = path.read_bytes()
        except OSError:
            return match.group(0)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return f'{prefix}data:{mime};base64,{base64.b64encode(data).decode("ascii")}{suffix}'

    return _IMG_SRC_RE.sub(_replace, html_text)


class InstallApi:
    def __init__(self, adb_path: str, base_dir, scanner_api):
        self.adb_path = adb_path
        self.base_dir = base_dir
        self._scanner_api = scanner_api
        self._runner = InstallRunner(
            adb_path, self._on_log, self._on_finished,
            base_dir=base_dir, ask_input_fn=input_broker.request,
        )
        self._pending_stage_index: int | None = None

    # ------------------------------------------------------------------
    def load_stages(self, model_key: str) -> dict:
        model = self._scanner_api.get_model(model_key)
        if model is None:
            return {"error": "unknown model key"}
        if not model.stages_script:
            return {"stages": []}
        # Свои files/usb_files модели (в т.ч. instruction.html — см.
        # stage_instruction_html_path) синхронизируются JIT прямо перед
        # установкой (runner.py) — но техник должен УВИДЕТЬ инструкцию уже
        # на экране этапов, до нажатия "Установка", иначе на свежем клиенте
        # (files/ ещё не скачаны) шаги показываются без текста инструкций,
        # хотя в админке она уже загружена на сервер.
        try:
            sync_model_files(self.base_dir, model, log=self._on_log)
        except Exception as exc:  # noqa: BLE001 - сбой сети не должен мешать открыть уже скачанное
            self._on_log(f"Не удалось проверить обновления файлов модели: {exc}")
        try:
            stages = load_stages(model)
        except StageDefinitionError as exc:
            return {"error": f"Ошибка в stages.py: {exc}"}
        except Exception as exc:  # noqa: BLE001 - показать пользователю любую ошибку загрузки скрипта
            return {"error": f"Не удалось загрузить stages.py: {exc}"}
        return {"stages": [self._stage_to_dict(model, i, s) for i, s in enumerate(stages)]}

    def _stage_to_dict(self, model, index: int, stage: dict) -> dict:
        html_path = stage_instruction_html_path(model, stage)
        exe_path = stage.get("exe_path")
        return {
            "index": index,
            "type": stage["type"],
            "title": stage["title"],
            "description": stage.get("description"),
            "instruction_html": (_inline_relative_images(html_path.read_text(encoding="utf-8"), html_path.parent)
                                  if html_path else None),
            "condition_var": stage.get("condition_var"),
            "condition_values": stage.get("condition_values"),
            "variant_names": stage.get("variant_names"),
            "standard_label": stage.get("standard_label", "Стандартные приложения"),
            "usb_copy_selected_apks": stage.get("usb_copy_selected_apks", False),
            "check_var": stage.get("check_var", ""),
            "check_options": stage.get("check_options"),
            "exe_path": exe_path,
            "exe_name": Path(exe_path).name if exe_path else None,
            "exe_exists": Path(exe_path).exists() if exe_path else None,
            "actions": [{"label": a.get("label", ""), "kind": a.get("kind", "command")}
                        for a in (stage.get("actions") or [])],
        }

    # ------------------------------------------------------------------
    _EMPTY_STANDARD_APKS = {"required": [], "optional": []}

    def standard_apks(self, model_key: str, stage_index: int, variant: str | None) -> dict:
        """Соответствует _standard_apks() в старом stage_wizard.py — APK
        "Стандартных приложений" этапа, с поправкой на выбранный вариант
        (Full/Lite/...) для этапов с standard_dir_base+variant_names.
        Разделены на обязательные (files/pack*/required — устанавливаются
        всегда, без чекбокса) и необязательные (.../optional — техник
        выбирает сам) — см. app/car_generator.py: StepSpec.standard_apks/
        standard_apks_optional за тем, как эти подпапки формируются."""
        model = self._scanner_api.get_model(model_key)
        if model is None:
            return dict(self._EMPTY_STANDARD_APKS)
        stages = load_stages(model)
        stage = stages[stage_index]
        standard_dir = stage.get("standard_dir")
        if standard_dir is None and stage.get("standard_dir_base"):
            variant_names = stage.get("variant_names") or []
            variant = variant or (variant_names[0] if variant_names else None)
            if not variant:
                return dict(self._EMPTY_STANDARD_APKS)
            standard_dir = Path(stage["standard_dir_base"]) / variant
        if not standard_dir:
            return dict(self._EMPTY_STANDARD_APKS)
        standard_dir = Path(standard_dir)
        return {
            "required": [apk_to_dict(apk) for apk in scan_apk_dir(standard_dir / "required")],
            "optional": [apk_to_dict(apk) for apk in scan_apk_dir(standard_dir / "optional")],
        }

    # ------------------------------------------------------------------
    def list_devices(self) -> list[dict]:
        return list_devices(self.adb_path)

    # -- мини-консоль ADB под логом главного окна (была в tkinter-версии до
    # перехода на pywebview, см. app/gui.py:_send_console_command в истории
    # git) — свободная "adb shell <команда>" на выбранное устройство, не
    # привязанная к конкретному этапу мастера установки. -------------------
    def console_send(self, device: str | None, command: str) -> dict:
        command = command.strip()
        if not command:
            return {"ok": False}
        threading.Thread(target=self._console_worker, args=(device, command), daemon=True).start()
        return {"ok": True}

    @staticmethod
    def _console_log(message) -> None:
        event_bridge.push({"kind": "log", "text": str(message)})

    def _console_worker(self, device: str | None, command: str) -> None:
        first_word = command.split(maxsplit=1)[0].lower() if command else ""
        try:
            if first_word in TOP_LEVEL_COMMANDS:
                # "connect"/"pair"/"devices" и т.п. — команды самого adb, не
                # шелла устройства (см. adb_utils.TOP_LEVEL_COMMANDS). Для
                # серверных команд не привязываемся к выбранному устройству —
                # при "connect" оно ещё и не может быть известно заранее.
                target = None if first_word in SERVER_LEVEL_COMMANDS else device
                adb = Adb(self.adb_path, target, log=self._console_log)
                adb.run(*command.split(), check=False)
            else:
                adb = Adb(self.adb_path, device, log=self._console_log)
                adb.shell(command, check=False)
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку
            self._console_log(f"Ошибка: {exc}")

    # -- кнопка «Подключить Wi-Fi» под логом главного окна — то же самое, что
    # печатать "connect <ip>:<port>" в консоли выше, но без необходимости
    # знать IP магнитолы вручную (см. get_default_gateway_ip) — до этого
    # единственным способом подключиться по Wi-Fi ADB вне готовой модели с
    # галочкой "Wi-Fi" в мастере был именно ручной ввод команды, что
    # оказалось не всем очевидно (устройство ведь ещё НЕ появилось в
    # списке — выбирать пока нечего, сначала нужно подключиться).
    # Синхронная (не в фоновом потоке, в отличие от console_send) — JS-сторона
    # (см. app.js: connectAdbWifi) ждёт результат, чтобы решить, предлагать
    # ли технику ввести IP вручную: автоопределение по шлюзу работает,
    # только если ноутбук подключён именно к собственной точке доступа
    # магнитолы — не всегда так (например, магнитола в общей сети, или
    # локальный тест через 127.0.0.1). -------------------------------------
    def wifi_connect(self, port: int, ip: str | None = None) -> dict:
        ip = (ip or "").strip() or None
        auto = ip is None
        if not ip:
            ip = get_default_gateway_ip()
            if not ip:
                return {"ok": False, "auto": True, "ip": None,
                        "error": "Не удалось определить IP магнитолы автоматически "
                                 "(нет активного Wi-Fi-подключения с шлюзом)."}
        self._console_log(f"Подключаюсь по Wi-Fi ADB: {ip}:{port}")
        adb = Adb(self.adb_path, None, log=self._console_log)
        try:
            result = adb.run("connect", f"{ip}:{port}", check=False)
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку
            return {"ok": False, "auto": auto, "ip": ip, "error": str(exc)}
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        ok = "connected to" in output.lower() or "already connected" in output.lower()
        return {"ok": ok, "auto": auto, "ip": ip, "message": output}

    # Скан локальной подсети на открытый port — для случая, когда шлюз (см.
    # wifi_connect выше) не помог: магнитола сама подключилась к сети/точке
    # доступа ноутбука, поэтому IP не совпадает с гейтвеем. Синхронный, как и
    # wifi_connect — JS-сторона (см. app.js:connectAdbWifi) ждёт результат,
    # чтобы сразу показать список найденного техникy.
    def scan_wifi(self, port: int) -> list[str]:
        self._console_log(f"Сканирую локальную сеть на открытый порт {port}...")
        found = scan_for_adb_hosts(port)
        if found:
            self._console_log(f"Найдены устройства с открытым портом {port}: {', '.join(found)}")
        else:
            self._console_log(f"Не нашёл в сети устройств с открытым портом {port}.")
        return found

    def start_stage(self, model_key: str, stage_index: int, device_serial: str | None,
                     selected_apk_paths: list[str]) -> dict:
        if self._runner.running:
            return {"ok": False, "error": "Установка уже выполняется."}
        model = self._scanner_api.get_model(model_key)
        if model is None:
            return {"ok": False, "error": "unknown model key"}
        stages = load_stages(model)
        stage = stages[stage_index]
        self._pending_stage_index = stage_index
        event_bridge.push({"kind": "install_log", "text": f"=== Этап {stage_index + 1}: {stage['title']} ==="})
        try:
            self._runner.start(model, device_serial, selected_apk_paths, run_fn=stage["run"])
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def cancel_stage(self) -> dict:
        if self._runner.running:
            self._runner.cancel()
            event_bridge.push({"kind": "install_log", "text": "Останавливаю этап..."})
        return {"ok": True}

    # -- "actions"-этап: в отличие от start_stage выше, у самого этапа нет
    # единого run — техник может нажать любую из кнопок (см. StepSpec.actions
    # в car_generator.py) в любом порядке и по несколько раз, поэтому запуск
    # идёт по (stage_index, action_index), а не только по stage_index. Тот же
    # InstallRunner (один запущенный run_fn одновременно), тот же поток
    # событий install_log/install_finished — JS-сторона (см. stage_wizard.js:
    # renderActionsStage) отличает "этап выполняется" от "действие
    # выполняется" только тем, что не переходит на следующий этап по success.
    def run_action(self, model_key: str, stage_index: int, action_index: int,
                    device_serial: str | None, selected_apk_paths: list[str]) -> dict:
        if self._runner.running:
            return {"ok": False, "error": "Установка уже выполняется."}
        model = self._scanner_api.get_model(model_key)
        if model is None:
            return {"ok": False, "error": "unknown model key"}
        stages = load_stages(model)
        stage = stages[stage_index]
        actions = stage.get("actions") or []
        if not (0 <= action_index < len(actions)):
            return {"ok": False, "error": "unknown action index"}
        action = actions[action_index]
        self._pending_stage_index = stage_index
        event_bridge.push({"kind": "install_log", "text": f"--- {action.get('label') or 'Действие'} ---"})
        try:
            self._runner.start(model, device_serial, selected_apk_paths, run_fn=action["run"])
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def answer_input(self, req_id: str, value: str | None) -> dict:
        input_broker.answer(req_id, value)
        return {"ok": True}

    # ------------------------------------------------------------------
    def run_exe(self, exe_path: str) -> dict:
        path = Path(exe_path)
        try:
            subprocess.Popen([str(path)], cwd=str(path.parent))
        except OSError as exc:
            return {"ok": False, "error": f"Не удалось запустить {path.name}: {exc}"}
        return {"ok": True}

    # ------------------------------------------------------------------
    # Колбэки InstallRunner — вызываются из фонового потока установки,
    # только толкают событие в очередь (см. app/web/events.py), ничего не
    # трогают в webview напрямую.
    def _on_log(self, message: str) -> None:
        event_bridge.push({"kind": "install_log", "text": message})

    def _on_finished(self, success: bool, message: str) -> None:
        event_bridge.push({
            "kind": "install_finished",
            "success": success,
            "message": message,
            "stage_index": self._pending_stage_index,
        })
