"""Отправка лога одной попытки установки на сервер (см. server/backend.py:
POST /install_log) — программа шлёт это САМА, без участия техника, по
завершении/уходу из мастера установки, и только если лог не пустой (была
реальная активность — ADB/установка/действия, не просто открыл или
пролистал инструкцию, см. app/web/frontend/js/screens/stage_wizard.js).
Тот же приём, что и report_client.py — маленький JSON через urllib.request,
best-effort (ошибка сети тут не должна мешать технику работать дальше)."""
import json
import urllib.error
import urllib.request

from .submit_config import SubmitConfig


class InstallLogError(RuntimeError):
    pass


def send_install_log(platform: str, brand: str, model: str, modification: str,
                      success: bool, log_text: str, client_id: str, config: SubmitConfig) -> None:
    body = json.dumps({
        "platform": platform,
        "brand": brand,
        "model": model,
        "modification": modification,
        "success": success,
        "log": log_text,
        "client_id": client_id,
    }).encode("utf-8")
    request = urllib.request.Request(
        config.install_log_url,
        data=body,
        method="POST",
        headers={
            "X-Submit-Key": config.submit_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
            message = data.get("error", str(exc))
        except (json.JSONDecodeError, UnicodeDecodeError):
            message = str(exc)
        raise InstallLogError(f"Сервер отклонил лог установки: {message}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise InstallLogError(f"Не удалось связаться с сервером: {exc}") from exc

    if not data.get("ok"):
        raise InstallLogError(data.get("error", "неизвестная ошибка"))
