"""ИИ-мастер на Android: склейка WebBridge.kt ↔ сервер (/chat/ai/*, ai_client.py) и проверка команд (ai_policy.py).

Простые типы на входе, JSON-строки на выходе — как mobile_bridge.py. Токен официальной сборки берётся у
content_sync (его ставит catalog_setup при запуске), cookie входа и адрес чата передаёт Kotlin."""
import json

import ai_client
import ai_policy
import content_sync


def call(chat_url: str, path: str, submit_key: str, cookie: str, payload_json: str) -> str:
    """path — status/start/turn. Ответ сервера JSON-строкой; нет связи — {"network_error": "..."}: ядро чата
    (ai_master.js) копит ввод и повторяет, когда связь вернётся."""
    payload = json.loads(payload_json) if payload_json else None
    try:
        body = ai_client.call(chat_url, path, submit_key, cookie or "", content_sync.app_token_header(), payload)
    except ai_client.AiNetworkError as exc:
        return json.dumps({"network_error": str(exc)}, ensure_ascii=False)
    return json.dumps(body, ensure_ascii=False)


def check_shell(cmd: str, mode: str, busy: bool) -> str:
    """Можно ли выполнить команду ИИ: {"ok": bool, "cmd": команда для shell, "reason": почему нельзя}."""
    allowed, reason = ai_policy.may_run(cmd, mode, bool(busy))
    return json.dumps({"ok": allowed, "cmd": ai_policy.normalize(cmd), "reason": reason}, ensure_ascii=False)
