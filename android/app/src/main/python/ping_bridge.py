"""Пульс приложения (см. WebBridge.kt: startHeartbeat, desktop app/ping_client.py, server/backend.py:
POST /ping) — раз в несколько минут, пока приложение открыто. Только счётчик пользователей и распределение
по версиям в админке; client_id случайный, ни к чему личному не привязан. Любая ошибка молча
проглатывается — пульс необязателен."""
import json
import urllib.error
import urllib.request


def send_ping(client_id: str, app_version: str, url: str, key: str) -> str:
    body = json.dumps({"client_id": client_id, "app_version": app_version, "platform": "android"}).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"X-Submit-Key": key, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as resp:
            resp.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return json.dumps({"ok": False})
    return json.dumps({"ok": True})
