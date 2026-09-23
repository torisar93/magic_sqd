"""ИИ-чат под логом установки — отправка хода переписки на наш сервер
(см. server/backend.py: POST /chat), который сам обращается к Gemini/Groq.
Ключи API нигде в клиенте не хранятся — только адрес/ключ нашего сервера
из submit.json (см. app/submit_config.py). Только стандартная библиотека,
как и report_client.py — небольшой JSON, urllib.request напрямую."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from .submit_config import SubmitConfig

# Сервер принимает тело запроса к /chat до 256 КБ (server/backend.py: CHAT_MAX_BODY_BYTES).
# Вывод команд и строки лога клиент режет и сам (chat_panel.js), но за долгую сессию набегало
# больше: лог #675 — 401 КБ, и на каждое следующее сообщение сервер отвечал «Слишком большой
# запрос». К тому же json.dumps по умолчанию писал кириллицу как \uXXXX — 6 байт на букву
# вместо 2. Теперь тело в UTF-8, а если всё равно не влезает — выпадают самые старые реплики,
# потом старые строки лога; последняя реплика (вопрос техника / результат команды) остаётся.
# Та же логика — в android/app/src/main/python/chat_bridge.py.
CHAT_BODY_MAX_BYTES = 200 * 1024
CHAT_LAST_TURN_MAX_CHARS = 20000


class ChatError(RuntimeError):
    pass


def _clip_middle(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit * 6 // 10
    tail = limit - head
    return f"{text[:head]}\n… [обрезано {len(text) - limit} симв.] …\n{text[len(text) - tail:]}"


def build_chat_body(history: list, recent_log: list, extra: dict, max_bytes: int = CHAT_BODY_MAX_BYTES) -> bytes:
    history = list(history)
    recent_log = list(recent_log)

    def encode() -> bytes:
        # "replace": JS режет строки посреди суррогатной пары (clipChatText) — без него
        # половинка эмодзи роняла бы отправку UnicodeEncodeError.
        return json.dumps({"history": history, "recent_log": recent_log, **extra},
                          ensure_ascii=False).encode("utf-8", "replace")

    body = encode()
    while len(body) > max_bytes and len(history) > 1:
        history.pop(0)
        body = encode()
    while len(body) > max_bytes and recent_log:
        recent_log.pop(0)
        body = encode()
    if len(body) > max_bytes and history:
        # осталась одна реплика, и она сама больше лимита (например, вставили огромный текст)
        last = dict(history[-1])
        for key in ("content", "output", "command"):
            if key in last:
                last[key] = _clip_middle(str(last[key]), CHAT_LAST_TURN_MAX_CHARS)
        history[-1] = last
        body = encode()
    return body


def send_chat_turn(history: list[dict], recent_log: list[str], config: SubmitConfig,
                    client_id: str = "", session_cookie: str = "", provider: str | None = None) -> dict:
    """history — [{"role": "user"|"assistant", "content": "..."} |
    {"role": "tool_result", "command": "...", "output": "...", "ok": bool}].
    Возвращает {"provider": "deepseek"|"qwen", "type": "text", "content": "..."}
    или {"provider": "...", "type": "command", "command": "...", "reason": "..."}.
    session_cookie — если техник залогинен (см. app/auth_client.py), сервер
    считает лимит запросов по его аккаунту, а не по IP (см.
    server/backend.py:_handle_chat) — так администратор может выдать
    доверенному техническому аккаунту повышенный лимит (см.
    set_user_chat_rate_limit). provider — принудительный выбор ("deepseek"/
    "qwen", команды /deepseek и /qwen в чате, см. chat_panel.js) БЕЗ
    автоматического фолбэка на другой при ошибке; None — обычный
    автоматический режим (DeepSeek первым, Qwen запасным)."""
    body = build_chat_body(history, recent_log, {"client_id": client_id, "provider": provider})
    headers = {
        "X-Submit-Key": config.submit_key,
        "Content-Type": "application/json",
    }
    if session_cookie:
        headers["Cookie"] = session_cookie
    request = urllib.request.Request(config.chat_url, data=body, method="POST", headers=headers)
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
