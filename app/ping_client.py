""""Пульс" программы — небольшой POST на сервер при старте и периодически,
пока программа открыта (см. gui.py), чтобы админка могла показать счётчик
пользователей: total (сколько разных client_id видели хоть раз) и online
(чей последний пульс не старше ONLINE_WINDOW_SECONDS, см. server/backend.py:
POST /ping). client_id — случайный, ни к чему личному не привязанный,
генерируется один раз и хранится рядом с программой (см. get_or_create_client_id)
— только чтобы отличать "тот же пользователь снова" от "новый пользователь"."""
import json
import secrets
import urllib.error
import urllib.request
from pathlib import Path

from .submit_config import SubmitConfig

CLIENT_ID_FILE_NAME = "client_id.txt"


class PingError(RuntimeError):
    pass


def get_or_create_client_id(base_dir: Path) -> str:
    path = base_dir / CLIENT_ID_FILE_NAME
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        return existing
    client_id = secrets.token_urlsafe(16)
    try:
        path.write_text(client_id, encoding="utf-8")
    except OSError:
        pass  # не страшно — просто "новый" пользователь на каждом запуске
    return client_id


def send_ping(client_id: str, config: SubmitConfig, timeout: float = 10) -> None:
    body = json.dumps({"client_id": client_id}).encode("utf-8")
    request = urllib.request.Request(
        config.ping_url,
        data=body,
        method="POST",
        headers={
            "X-Submit-Key": config.submit_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise PingError(f"сервер отклонил пульс: {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PingError(f"не удалось связаться с сервером: {exc}") from exc

    if not data.get("ok"):
        raise PingError(data.get("error", "неизвестная ошибка"))
