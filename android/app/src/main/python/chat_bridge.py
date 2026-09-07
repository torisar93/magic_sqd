"""ИИ-чат под логом установки (см. WebBridge.kt: chatSend/chatConfirmCommand,
app/chat_client.py на десктопе, server/backend.py: POST /chat) — сервер сам
обращается к Gemini/Groq, ключи API нигде на телефоне не хранятся. Только
stdlib urllib, как и mobile_bridge.py/content_sync.py."""
import json
import urllib.error
import urllib.request


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

    body = json.dumps({
        "history": history, "recent_log": recent_log, "client_id": "",
        "provider": provider or None,
    }).encode("utf-8")
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
