"""Лог одной попытки установки (см. WebBridge.kt: installLogSend,
app/install_log_client.py на десктопе, server/backend.py: POST /install_log)
— приложение шлёт это САМО, без участия техника, по завершении/уходу из
мастера установки, и только если лог не пустой (была реальная активность,
не просто открыл/пролистал инструкцию — см. app.js). Только stdlib urllib,
как и chat_bridge.py/mobile_bridge.py."""
import json
import urllib.error
import urllib.request


def send_install_log(brand: str, model: str, modification: str, success: bool,
                      log_text: str, client_id: str, url: str, key: str) -> str:
    """Возвращает JSON-строку {"ok": true} / {"ok": false, "error": "..."} —
    разбирается на стороне WebBridge.kt (best-effort, ошибка тут не должна
    ничего показывать технику)."""
    body = json.dumps({
        "platform": "android",
        "brand": brand,
        "model": model,
        "modification": modification,
        "success": success,
        "log": log_text,
        "client_id": client_id,
    }).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"X-Submit-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
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
