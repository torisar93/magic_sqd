"""Загрузка исходного bugreport-*.zip (этап "Пароль ADB по QR-коду", см.
app/qr_adb_password.py) на сервер целиком (см. server/backend.py: POST
/qr_adb_debug) — временная мера (владелец, 2026-09-22): жалобы клиентов
"пароль неверный", пока формула не подтверждена на достаточном разнообразии
реальных магнитол. Раньше копия сохранялась только ЛОКАЛЬНО на машине
техника (app/qr_adb_password.py:save_debug_copy) — разработчику приходилось
просить технику переслать файл руками, ненадёжно и с задержкой. Теперь
уходит сама, централизованно; убрать эту возможность — когда накопится
несколько точно успешных установок (см. память
project_qr_adb_password_logging_and_macos_bug).

Сырое тело (не JSON+base64) — реальные zip "десятки мегабайт", лишние +33%
от base64 ни к чему; метаданные — в query, тот же приём, что и у
POST /submit?target=... (см. app/admin_client.py:upload_model)."""
from __future__ import annotations
import json
import urllib.error
import urllib.parse
import urllib.request

from .submit_config import SubmitConfig

_UPLOAD_TIMEOUT_SECONDS = 60


class QrAdbDebugUploadError(RuntimeError):
    pass


def upload_debug_copy(config: SubmitConfig, client_id: str, platform: str, logs_folder: str, zip_bytes: bytes) -> None:
    """logs_folder — имя папки logs_<таймстемп> на флешке (см.
    app/qr_adb_password.py:find_latest_logs_folder), не серийный номер
    устройства: он появляется только ПОСЛЕ разбора полей внутри zip, а
    отправка происходит ДО разбора (чтобы сырые данные ушли на сервер, даже
    если сам разбор потом упадёт) — используется просто как понятный
    человеку, уже готовый и гарантированно уникальный (содержит время)
    идентификатор конкретной попытки для имени файла на сервере.

    Бросает QrAdbDebugUploadError с понятной причиной при любом сбое —
    вызывающий код (app/web/api/qr_adb_api.py:QrAdbApi.get_password) ловит
    её и откатывается на локальное сохранение (см.
    app/qr_adb_password.py:save_debug_copy), чтобы диагностика не потерялась
    совсем из-за недоступности сети/сервера."""
    query = urllib.parse.urlencode({"client_id": client_id, "platform": platform, "logs_folder": logs_folder})
    request = urllib.request.Request(
        f"{config.qr_adb_debug_url}?{query}",
        data=zip_bytes,
        method="POST",
        headers={"X-Submit-Key": config.submit_key, "Content-Type": "application/zip"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_UPLOAD_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            data = json.loads(exc.read().decode("utf-8"))
            message = data.get("error", str(exc))
        except (ValueError, UnicodeDecodeError):
            message = str(exc)
        raise QrAdbDebugUploadError(f"Сервер отклонил файл: {message}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise QrAdbDebugUploadError(f"Не удалось связаться с сервером: {exc}") from exc

    if not data.get("ok"):
        raise QrAdbDebugUploadError(data.get("error", "неизвестная ошибка"))
