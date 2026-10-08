"""ИИ-мастер («Установка с ИИ», платная функция для подписчиков Boosty) на ПК: мост окна ↔ сервер и команды ИИ
на магнитоле.

Чат — общее с Android ядро js/ai_master.js в панели js/screens/ai_panel.js. Ходы идут на сервер (/chat/ai/*, см.
server/ai_master.py) через app/ai_client.py с ключом программы, cookie входа и токеном официальной сборки. Нет связи —
{"network_error": …}: ядро копит ввод и повторяет, когда связь вернётся (у компьютера в Wi-Fi магнитолы интернета
обычно нет). Команду ИИ программа проверяет ещё раз той же политикой, что сервер (app/ai_policy.py): сама — только
читающие, во время установки — ничего, команды adb уровня компьютера — никогда.

pywebview зовёт каждый метод моста в своём потоке, поэтому ход (до ~50 с) окно не держит."""
from __future__ import annotations

from ... import ai_client, ai_policy, content_sync
from ...adb_utils import Adb, list_devices
from ...ping_client import get_or_create_client_id
from ...submit_config import get_submit_config
from ...version import APP_VERSION

PATHS = ("status", "start", "turn")
SHELL_TIMEOUT_SECONDS = 30
OUTPUT_LIMIT = 8000  # вывод команды — ИИ, не технику; сервер всё равно сокращает старые результаты


class AiApi:
    def __init__(self, base_dir, adb_path: str, auth_api, install_api, platform_name: str):
        self.base_dir = base_dir
        self.adb_path = adb_path
        self._auth = auth_api
        self._install = install_api
        self.platform_name = platform_name

    def call(self, path: str, payload=None) -> dict:
        """Запрос к серверу ИИ-мастера: status (GET), start, turn (POST). Ответ сервера как есть (dict со status)."""
        if path not in PATHS:
            return {"status": "error", "error": "неизвестный запрос"}
        config = get_submit_config(self.base_dir)
        if config is None:
            return {"status": "unavailable"}
        body = None
        if path != "status":
            body = dict(payload) if isinstance(payload, dict) else {}
            if path == "start":  # чья это программа — для админки и истории магнитолы
                body.update(client_id=get_or_create_client_id(self.base_dir), platform=self.platform_name,
                            app_version=APP_VERSION)
        cookie = (self._auth.user_cookie if self._auth else None) or ""
        try:
            return ai_client.call(config.chat_url, path, config.submit_key, cookie, content_sync.app_token_header(), body)
        except ai_client.AiNetworkError as exc:
            return {"network_error": str(exc)}

    def shell(self, device, command: str, mode: str, busy: bool = False) -> dict:
        """Команда ИИ в shell магнитолы. mode — "auto" (программа сама: только читающие) или "confirm" (техник нажал
        «Выполнить»). device — выбранная в этапе магнитола; не выбрана — единственная подключённая."""
        allowed, reason = ai_policy.may_run(command, mode, bool(busy) or self._install.busy())
        if not allowed:
            return {"ok": False, "output": f"Не выполнено: {reason}."}
        serial = device or self._single_device()
        if not serial:
            return {"ok": False, "output": "Магнитола не подключена по ADB (или их несколько — выберите в этапе)."}
        try:
            result = Adb(self.adb_path, serial).shell(ai_policy.normalize(command), check=False,
                                                      timeout=SHELL_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 - любой сбой — ответом ИИ, а не исключением в окно
            return {"ok": False, "output": f"Не выполнилось: {exc}"}
        text = "\n".join(part for part in ((result.stdout or "").strip(), (result.stderr or "").strip()) if part)
        if not text:
            text = "(пустой вывод)" if result.returncode == 0 else f"(код возврата {result.returncode})"
        if len(text) > OUTPUT_LIMIT:
            text = text[:OUTPUT_LIMIT] + f"\n…[обрезано, всего {len(text)} символов]"
        return {"ok": result.returncode == 0, "output": text}

    def _single_device(self) -> str | None:
        ready = [d["serial"] for d in list_devices(self.adb_path) if d.get("state") == "device"]
        return ready[0] if len(ready) == 1 else None
