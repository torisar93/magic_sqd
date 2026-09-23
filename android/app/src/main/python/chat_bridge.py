"""ИИ-чат под логом установки (см. WebBridge.kt: chatSend/chatConfirmCommand,
app/chat_client.py на десктопе, server/backend.py: POST /chat) — сервер сам
обращается к Gemini/Groq, ключи API нигде на телефоне не хранятся. Только
stdlib urllib, как и mobile_bridge.py/content_sync.py."""
import json
import urllib.error
import urllib.request

# Тело запроса к /chat — не больше 256 КБ на сервере; за долгую сессию набегало больше (лог #675:
# 401 КБ, Android 1.0.33). Та же логика, что в desktop app/chat_client.py: build_chat_body —
# UTF-8 вместо \uXXXX, при переполнении выпадают старые реплики, потом старые строки лога.
CHAT_BODY_MAX_BYTES = 200 * 1024
CHAT_LAST_TURN_MAX_CHARS = 20000


def _clip_middle(text, limit):
    if len(text) <= limit:
        return text
    head = limit * 6 // 10
    tail = limit - head
    return f"{text[:head]}\n… [обрезано {len(text) - limit} симв.] …\n{text[len(text) - tail:]}"


def build_chat_body(history, recent_log, extra, max_bytes=CHAT_BODY_MAX_BYTES):
    history = list(history)
    recent_log = list(recent_log)

    def encode():
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
        last = dict(history[-1])
        for key in ("content", "output", "command"):
            if key in last:
                last[key] = _clip_middle(str(last[key]), CHAT_LAST_TURN_MAX_CHARS)
        history[-1] = last
        body = encode()
    return body


def send_chat_turn(history_json: str, recent_log_json: str, chat_url: str, chat_key: str,
                    provider: str = "") -> str:
    """history_json/recent_log_json — уже сериализованные с Kotlin-стороны
    JSON-массивы (Chaquopy строкам доверяет проще, чем объектам). provider —
    "deepseek"/"qwen"/"" (принудительный выбор, команды /deepseek /qwen в
    чате — см. app.js), пустая строка = обычный автоматический режим на
    сервере. Возвращает JSON-строку {"ok": true, "provider": "...",
    "type": "text"/"command", ...} / {"ok": false, "error": "..."},
    разбирается на стороне WebBridge.kt."""
    try:
        history = json.loads(history_json)
        recent_log = json.loads(recent_log_json)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return json.dumps({"ok": False, "error": "некорректный запрос"})

    body = build_chat_body(history, recent_log, {"client_id": "", "provider": provider or None})
    request = urllib.request.Request(
        chat_url, data=body, method="POST",
        headers={"X-Submit-Key": chat_key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=45) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
            message = data.get("error", str(exc))
        except (json.JSONDecodeError, UnicodeDecodeError):
            message = str(exc)
        return json.dumps({"ok": False, "error": message})
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return json.dumps({"ok": False, "error": f"Не удалось связаться с сервером: {exc}"})

    return json.dumps(data)
