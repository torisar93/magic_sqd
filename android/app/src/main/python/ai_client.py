"""ИИ-мастер: HTTP к серверу (/chat/ai/status|start|turn, см. server/backend.py и server/ai_master.py).

Сетевой сбой и 5xx — AiNetworkError: общее ядро чата (js/ai_master.js) считает это «нет интернета», копит ввод и
повторяет позже (ПК в Wi-Fi магнитолы без интернета). Ответы 4xx — понятная ошибка в теле ответа, без повторов.

Файл ОДИНАКОВЫЙ на ПК (app/ai_client.py) и Android (android/app/src/main/python/ai_client.py): адрес, ключ программы,
cookie входа и заголовок токена сборки передаёт платформа. Только стандартная библиотека."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

TIMEOUT_SECONDS = 50  # ход сервера — до ~35 с (дольше он отвечает continue), плюс сеть


class AiNetworkError(RuntimeError):
    """Нет связи с сервером (или он не отвечает) — ход повторится, когда связь вернётся."""


def call(chat_url: str, path: str, submit_key: str, cookie: str = "", headers: dict | None = None,
         payload: dict | None = None, timeout: float = TIMEOUT_SECONDS) -> dict:
    """path — "status" (GET), "start" или "turn" (POST с payload). Возвращает тело ответа сервера (dict со status)."""
    url = chat_url.rstrip("/") + "/ai/" + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    request.add_header("X-Submit-Key", submit_key or "")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if cookie:
        request.add_header("Cookie", cookie)
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code >= 500:
            raise AiNetworkError(f"сервер ответил {exc.code}") from exc
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (ValueError, OSError):
            body = {}
        error = body.get("error") if isinstance(body, dict) else ""
        return {"status": "error", "error": error or f"сервер отказал ({exc.code})"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AiNetworkError(str(exc)) from exc
    except ValueError as exc:
        raise AiNetworkError(f"непонятный ответ сервера: {exc}") from exc
    if not isinstance(body, dict):
        return {"status": "error", "error": "непонятный ответ сервера"}
    body.setdefault("status", "error")
    return body
