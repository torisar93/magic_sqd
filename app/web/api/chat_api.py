"""ИИ-чат под логом установки (см. app/chat_client.py, server/backend.py:
POST /chat) — сервер (Gemini/Groq) отвечает текстом или предлагает ОДНУ
shell-команду; техник видит её точный текст и явно подтверждает перед
выполнением. Выполнение переиспользует тот же разбор top-level/shell
команды, что и свободная ADB-консоль (см. InstallApi.console_send), но
результат идёт отдельным событием "chat_command_result" в ленту чата, а
не только в общий лог установки."""
from __future__ import annotations
import threading

from ..events import event_bridge
from ...adb_utils import SERVER_LEVEL_COMMANDS, TOP_LEVEL_COMMANDS, Adb
from ...chat_client import ChatError, send_chat_turn
from ...submit_config import get_submit_config


class ChatApi:
    def __init__(self, base_dir, adb_path: str):
        self.base_dir = base_dir
        self.adb_path = adb_path

    # ------------------------------------------------------------------
    def chat_send(self, history: list, recent_log: list) -> dict:
        config = get_submit_config(self.base_dir)
        if config is None:
            return {"ok": False, "error": "чат не настроен (нет submit.json)"}
        if not isinstance(history, list) or not history:
            return {"ok": False, "error": "пустое сообщение"}
        threading.Thread(target=self._send_worker, args=(history, recent_log, config), daemon=True).start()
        return {"ok": True}

    def _send_worker(self, history: list, recent_log: list, config) -> None:
        try:
            reply = send_chat_turn(history, recent_log, config)
        except ChatError as exc:
            reply = {"type": "text", "content": f"Не удалось получить ответ: {exc}"}
        event_bridge.push({"kind": "chat_reply", "reply": reply})

    # ------------------------------------------------------------------
    def chat_confirm_command(self, device: str | None, command: str) -> dict:
        command = (command or "").strip()
        if not command:
            return {"ok": False}
        threading.Thread(target=self._confirm_worker, args=(device, command), daemon=True).start()
        return {"ok": True}

    def _confirm_worker(self, device: str | None, command: str) -> None:
        first_word = command.split(maxsplit=1)[0].lower() if command else ""
        try:
            if first_word in TOP_LEVEL_COMMANDS:
                target = None if first_word in SERVER_LEVEL_COMMANDS else device
                adb = Adb(self.adb_path, target)
                result = adb.run(*command.split(), check=False)
            else:
                adb = Adb(self.adb_path, device)
                result = adb.shell(command, check=False)
            output = (result.stdout or "").strip()
            error = (result.stderr or "").strip()
            text = "\n".join(part for part in (output, error) if part) or (
                "(готово)" if result.returncode == 0 else f"(код возврата: {result.returncode})")
            ok = result.returncode == 0
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку
            text = f"Ошибка: {exc}"
            ok = False
        event_bridge.push({"kind": "chat_command_result", "command": command, "output": text, "ok": ok})
