"""Локальный стейджинг заявок клиентов для просмотра/правки в
визуальном редакторе (см. app/web/api/submissions_api.py). Заявка на
сервере — это .zip с содержимым папки модели БЕЗ вложенности марка/модель
(см. app/submit_client.py: shutil.make_archive(..., root_dir=model_dir)),
поэтому распакованная как есть она по форме ничем не отличается от
обычной папки модели в cars/ — car_generator.load_car_spec/update_car
работают с ней без изменений.

Стейджинг делается НЕ внутри cars/ (см. app/scanner.py: scan_cars
пропускает только папки МАРОК с "_", а не произвольную вложенность), а
рядом, в base_dir/_pending/<имя_заявки>/ — тот же принцип, что и
app/qt_fallback.py:_qt_fallback (появляется во время работы, не через
инсталлятор, чистится через [UninstallDelete], см. installer.iss)."""
from __future__ import annotations
import shutil
import zipfile
from pathlib import Path

PENDING_DIRNAME = "_pending"


class ZipSlipError(RuntimeError):
    pass


def _staged_dir(base_dir: Path, name: str) -> Path:
    stem = Path(name).stem
    return base_dir / PENDING_DIRNAME / stem


def is_staged(base_dir: Path, name: str) -> bool:
    return _staged_dir(base_dir, name).is_dir()


def staged_dir(base_dir: Path, name: str) -> Path:
    return _staged_dir(base_dir, name)


def stage(base_dir: Path, name: str, zip_path: Path) -> Path:
    """Распаковывает zip_path (уже скачанный, см. app/admin_client.py:
    download_submission) в base_dir/_pending/<стем имени заявки>/. Защита
    от zip-slip — тот же принцип, что серверный safe_extract
    (server/backend.py): каждый распакованный путь обязан остаться внутри
    целевой папки. Перезаписывает предыдущий стейдж той же заявки, если он
    уже был (повторное открытие)."""
    dest = _staged_dir(base_dir, name)
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            dest_resolved = dest.resolve()
            for info in zf.infolist():
                if info.is_dir():
                    continue
                target = (dest / info.filename).resolve()
                if not target.is_relative_to(dest_resolved):
                    raise ZipSlipError(f"Небезопасный путь в архиве заявки: {info.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
    except (zipfile.BadZipFile, OSError):
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return dest


def discard(base_dir: Path, name: str) -> None:
    """Убирает локальный стейдж после публикации/отклонения заявки."""
    shutil.rmtree(_staged_dir(base_dir, name), ignore_errors=True)
