"""ИИ-чат под логом установки — отправка хода переписки на наш сервер
(см. server/backend.py: POST /chat), который сам обращается к Gemini/Groq.
Ключи API нигде в клиенте не хранятся — только адрес/ключ нашего сервера
из submit.json (см. app/submit_config.py). Только стандартная библиотека,
как и report_client.py — небольшой JSON, urllib.request напрямую."""
import json
import urllib.error
import urllib.request

from .submit_config import SubmitConfig


class ChatError(RuntimeError):
    pass


def send_chat_turn(history: list[dict], recent_log: list[str], config: SubmitConfig,
                    client_id: str = "") -> dict:
    """history — [{"role": "user"|"assistant", "content": "..."} |
    {"role": "tool_result", "command": "...", "output": "...", "ok": bool}].
    Возвращает {"type": "text", "content": "..."} или
    {"type": "command", "command": "...", "reason": "..."}."""
    body = json.dumps({
        "history": history,
        "recent_log": recent_log,
        "client_id": client_id,
    }).encode("utf-8")
    request = urllib.request.Request(
        config.chat_url,
        data=body,
        method="POST",
        headers={
            "X-Submit-Key": config.submit_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
            message = data.get("error", str(exc))
        except (json.JSONDecodeError, UnicodeDecodeError):
            message = str(exc)
        raise ChatError(message) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ChatError(f"Не удалось связаться с сервером: {exc}") from exc

    if not data.get("ok"):
        raise ChatError(data.get("error", "неизвестная ошибка"))
    return {k: v for k, v in data.items() if k != "ok"}
