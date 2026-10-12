"""Доступ к каталогу по региону — сторона программы (с 1.1.4; правило — server/access_policy.py). Владелец,
12.10.2026: бесплатно — наши языки и часовые пояса, остальным — по подписке Boosty.

Сервер сам не отдаёт каталог тем, кому нужна подписка, но модели, скачанные раньше, лежат у программы на диске —
поэтому программа спрашивает GET /auth/access и, если нужна подписка, закрывает каталог окном «Нужна подписка»
(js/components/access_gate.js). Решение запоминается (access_state.json): без интернета (техник в Wi-Fi магнитолы)
действует последнее известное, а не «всё закрыто». Ни разу не проверяли и нет сети — пускаем.

Файл общий для ПК (app/access_gate.py) и Android (android/app/src/main/python/access_gate.py) — побайтно
(tests/test_access_gate.py сверяет копии). Только стандартная библиотека."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
from pathlib import Path

try:
    from . import content_sync  # ПК: модуль пакета app
except ImportError:  # Android (Chaquopy): модули верхнего уровня
    import content_sync

STATE_FILE = "access_state.json"
_lock = threading.Lock()
_cache: dict = {"at": 0.0, "state": None}


def _origin(base_url: str) -> str:
    return base_url.rstrip("/").rsplit("/content", 1)[0]


def load(base_dir) -> dict | None:
    """Последнее решение сервера, сохранённое на диске; нет — None."""
    try:
        data = json.loads((Path(base_dir) / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("allowed"), bool) else None


def save(base_dir, state: dict) -> None:
    path = Path(base_dir) / STATE_FILE
    tmp = path.with_name(path.name + ".part")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # не записать — решение продержится до перезапуска


def invalidate() -> None:
    """Следующий check() — заново у сервера (после входа и выхода из аккаунта, «Проверить снова»)."""
    with _lock:
        _cache["state"] = None


def check(base_dir, base_url: str | None, fresh_seconds: float = 60.0, timeout: float = 15.0) -> dict:
    """{"allowed": bool, "access": off|free|…|subscription_required|unknown, "boosty": ссылка, "checked_at": время,
    "offline": bool}. Нет сети — последнее сохранённое решение (offline: true)."""
    now = time.time()
    with _lock:
        if _cache["state"] is not None and now - _cache["at"] < fresh_seconds:
            return dict(_cache["state"])
    state = None
    if base_url:
        try:
            with content_sync.open_url(_origin(base_url) + "/auth/access", timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if isinstance(data, dict) and data.get("ok"):
                state = {"allowed": bool(data.get("allowed", True)), "access": str(data.get("access") or ""),
                         "boosty": str(data.get("boosty") or ""), "checked_at": now, "offline": False}
                save(base_dir, state)
        except urllib.error.HTTPError:
            state = None  # нет токена сборки (запуск из исходников) или старый сервер — решать нечем
        except (urllib.error.URLError, OSError, ValueError):
            state = None  # нет сети
    if state is None:
        stored = load(base_dir)
        state = (dict(stored, offline=True) if stored else
                 {"allowed": True, "access": "unknown", "boosty": "", "checked_at": 0, "offline": True})
    with _lock:
        _cache.update(at=now, state=state)
    return dict(state)
