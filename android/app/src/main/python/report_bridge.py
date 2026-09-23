"""«Сообщить о проблеме» (см. WebBridge.kt: reportSend, app.js: showReportModal, desktop
app/report_client.py, server/backend.py: POST /report). Обращение может быть и к конкретной модели, и к работе
приложения в целом (brand/model пустые). Только stdlib urllib, как и install_log_bridge.py."""
import json
import urllib.error
import urllib.request


def send_report(brand: str, model: str, reason: str, description: str, app_version: str,
                client_id: str, url: str, key: str, email: str = "", session_cookie: str = "") -> str:
    """Возвращает JSON-строку {"ok": true} / {"ok": false, "error": "..."} — показывается технику.
    email — почта для ответа от того, кто не вошёл в аккаунт; session_cookie — вошедший: ответ
    придёт на почту аккаунта (сервер берёт её из сессии)."""
    body = json.dumps({
        "brand": brand,
        "model": model,
        "reason": reason,
        "description": description,
        "app_version": app_version,
        "platform": "android",
        "client_id": client_id,
        "email": email,
    }).encode("utf-8")
    headers = {"X-Submit-Key": key, "Content-Type": "application/json"}
    if session_cookie:
        headers["Cookie"] = session_cookie
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read().decode("utf-8")).get("error", str(exc))
        except (json.JSONDecodeError, UnicodeDecodeError):
            message = str(exc)
        return json.dumps({"ok": False, "error": f"Сервер отклонил обращение: {message}"})
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return json.dumps({"ok": False, "error": f"Не удалось связаться с сервером: {exc}"})
    if not data.get("ok"):
        return json.dumps({"ok": False, "error": data.get("error", "неизвестная ошибка")})
    return json.dumps({"ok": True})
