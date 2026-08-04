"""Отправка модели, собранной в мастере "Добавить машину...", на проверку
разработчику (см. server/README.md, server/backend.py: POST /submit). Только
стандартная библиотека — используем http.client напрямую (а не
urllib.request.urlopen), чтобы отправлять файл кусками и иметь возможность
проверять отмену между кусками (прошивки — гигабайты, отправка не должна
ни разбухать в памяти целиком, ни виснуть намертво по кнопке "Отмена")."""
import http.client
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .submit_config import SubmitConfig

CHUNK_SIZE = 1024 * 1024


class SubmitError(RuntimeError):
    pass


class SubmitCancelled(RuntimeError):
    pass


def submit_model(model_dir: Path, brand: str, model: str, config: SubmitConfig,
                  modification: str = "", client_id: str = "",
                  log=lambda m: None, check_cancelled=lambda: None) -> None:
    """Упаковывает model_dir в .zip и отправляет на config.submit_url.
    modification — необязательный третий слой (см. app/scanner.py:
    ModelGroup), сервер кладёт заявку в content/cars/<brand>/<model>/
    <modification>/ при одобрении, если задана. client_id — анонимный
    идентификатор установки (см. app/ping_client.py:
    get_or_create_client_id) — не авторизация, а просто способ для
    разработчика отличить в очереди на рассмотрение заявки от разных людей
    по одной и той же машине (см. server/backend.py: повторная отправка
    ТЕМ ЖЕ client_id той же машины заменяет его собственную предыдущую
    заявку, а не копится рядом). Бросает SubmitError при сетевой проблеме/
    отказе сервера, SubmitCancelled — если check_cancelled() сама бросила
    исключение во время отправки."""
    with tempfile.TemporaryDirectory() as tmp:
        log("Упаковываю модель в архив...")
        archive_base = Path(tmp) / "model"
        try:
            archive_path = Path(shutil.make_archive(str(archive_base), "zip", root_dir=model_dir))
        except OSError as exc:
            raise SubmitError(f"Не удалось собрать архив: {exc}") from exc

        size = archive_path.stat().st_size
        log(f"Отправляю ({size / (1024 * 1024):.1f} МБ)...")
        _send(archive_path, size, brand, model, modification, client_id, config, check_cancelled)
        log("Отправлено, спасибо! Разработчик проверит и добавит модель в общий список.")


def _send(archive_path: Path, size: int, brand: str, model: str, modification: str,
          client_id: str, config: SubmitConfig, check_cancelled) -> None:
    parts = urlsplit(config.submit_url)
    query = urlencode({"brand": brand, "model": model, "modification": modification,
                        "client_id": client_id, "filename": archive_path.name})
    path = f"{parts.path or '/'}?{query}"
    conn_cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection

    conn = conn_cls(parts.netloc, timeout=120)
    try:
        conn.putrequest("POST", path)
        conn.putheader("X-Submit-Key", config.submit_key)
        conn.putheader("Content-Length", str(size))
        conn.putheader("Content-Type", "application/zip")
        conn.endheaders()

        with open(archive_path, "rb") as f:
            while True:
                check_cancelled()
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                conn.send(chunk)

        response = conn.getresponse()
        body = response.read()
        if response.status != 200:
            raise SubmitError(
                f"Сервер отклонил отправку ({response.status}): {body.decode('utf-8', 'replace')}")
    except (OSError, http.client.HTTPException) as exc:
        raise SubmitError(f"Не удалось связаться с сервером: {exc}") from exc
    finally:
        conn.close()
