"""Обёртка runner.py/install_context.py/stage_runner.py для stage wizard.
Настоящий run(ctx) конкретного этапа НИКОГДА не сериализуется в JSON — наружу
уходят только описательные поля этапа (см. _stage_to_dict), запуск идёт по
stage_index через install_load_stages(model_key), а сам run_fn достаётся
заново из stages.py на стороне Python (см. start_stage). Прогресс/ask_input
идут через app/web/events.py — тот же приём, что раньше был queue.Queue +
self.after(100, ...) в stage_wizard.py, только транспорт — evaluate_js вместо
Tk-виджетов (см. план миграции)."""
import base64
import mimetypes
import re
import subprocess
from pathlib import Path

from ..events import event_bridge, input_broker
from ...adb_utils import list_devices
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
            "check_var": stage.get("check_var", ""),
            "check_options": stage.get("check_options"),
            "exe_path": exe_path,
            "exe_name": Path(exe_path).name if exe_path else None,
            "exe_exists": Path(exe_path).exists() if exe_path else None,
        }

    # ------------------------------------------------------------------
    def standard_apks(self, model_key: str, stage_index: int, variant: str | None) -> list[dict]:
        """Соответствует _standard_apks() в старом stage_wizard.py — APK
        "Стандартных приложений" этапа, с поправкой на выбранный вариант
        (Full/Lite/...) для этапов с standard_dir_base+variant_names."""
        model = self._scanner_api.get_model(model_key)
        if model is None:
            return []
        stages = load_stages(model)
        stage = stages[stage_index]
        standard_dir = stage.get("standard_dir")
        if standard_dir is None and stage.get("standard_dir_base"):
            variant_names = stage.get("variant_names") or []
            variant = variant or (variant_names[0] if variant_names else None)
            if not variant:
                return []
            standard_dir = Path(stage["standard_dir_base"]) / variant
        if not standard_dir:
            return []
        standard_dir = Path(standard_dir)
        if not standard_dir.exists():
            return []
        return [apk_to_dict(apk) for apk in scan_apk_dir(standard_dir)]

    # ------------------------------------------------------------------
    def list_devices(self) -> list[dict]:
        return list_devices(self.adb_path)

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
