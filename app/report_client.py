"""Отправка жалобы ("Сообщить о проблеме" в главном окне, см.
app/report_dialog.py) на сервер — сервер сам пересылает её на почту
разработчика (см. server/backend.py: POST /report, send_report_email).
Только стандартная библиотека, как и submit_client.py — небольшой JSON,
поэтому используем urllib.request напрямую, без ручного стриминга
по кускам (в отличие от submit_client.py, там гигабайтные .zip)."""
import json
import urllib.error
import urllib.request

from .submit_config import SubmitConfig


class ReportError(RuntimeError):
    pass


def send_report(brand: str, model: str, reason: str, description: str, config: SubmitConfig) -> None:
    body = json.dumps({
        "brand": brand,
        "model": model,
        "reason": reason,
        "description": description,
    }).encode("utf-8")
    request = urllib.request.Request(
        config.report_url,
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
        raise ReportError(f"Сервер отклонил обращение: {message}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ReportError(f"Не удалось связаться с сервером: {exc}") from exc

    if not data.get("ok"):
        raise ReportError(data.get("error", "неизвестная ошибка"))
