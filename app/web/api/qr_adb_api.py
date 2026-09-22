"""Bridge для этапа "Пароль ADB по QR-коду" (app/web/frontend/js/screens/
stage_wizard.js: renderQrAdbStage) — см. app/qr_adb_password.py за самим
алгоритмом. Список дисков переиспользует usb_api.py (тот же
usb_utils.list_drives), отдельного списка тут нет."""
from __future__ import annotations
import shutil
from pathlib import Path

from ...ping_client import get_or_create_client_id
from ...qr_adb_debug_client import QrAdbDebugUploadError, upload_debug_copy
from ...qr_adb_password import QrAdbError, find_bugreport_zip, find_latest_logs_folder, get_adb_password
from ...submit_config import get_submit_config
from ...usb_utils import drive_root_path

_FLAG_FILENAME = "svlog.flag"
# Только для моделей с qr_adb_engineering_menu=True (сейчас — Haval Jolion 2026, Desay
# x9h) — см. car_generator.py: StepSpec.qr_adb_engineering_menu. Пишется ПЕРВЫМ, до
# _FLAG_FILENAME: отдельный файл-триггер, который на этой платформе открывает
# инженерное меню магнитолы (внутри которого уже нужно вручную дойти до раздела с
# QR-кодом — этому не помочь запиской, только инструкцией на экране, см. stage_wizard.js).
# Обычный svlog.flag на прошивках Geely/VOLGA срабатывает сразу, без этого шага.
_PREP_FLAG_FILENAME = "svengmode.flag"


def _write_shared_flag(cars_dir: Path, filename: str, drive_letter: str) -> dict:
    """Общая часть write_flag/write_prep_flag ниже — копирует файл-триггер
    <filename> из cars/_shared в корень флешки."""
    src = cars_dir / "_shared" / filename
    if not src.is_file():
        return {
            "ok": False,
            "error": f"{filename} не найден в cars/_shared — обновите каталог (Настройки → "
                     "Проверить обновления) и попробуйте снова.",
        }
    try:
        shutil.copyfile(src, drive_root_path(drive_letter) / filename)
    except OSError as exc:
        return {"ok": False, "error": f"Не удалось записать на флешку {drive_letter}: {exc}"}
    return {"ok": True}


class QrAdbApi:
    def __init__(self, base_dir: Path, cars_dir: Path, platform: str = ""):
        self.base_dir = Path(base_dir)
        self.cars_dir = Path(cars_dir)
        self.platform = platform

    def write_prep_flag(self, drive_letter: str) -> dict:
        """Доп. шаг ПЕРЕД write_flag — только для qr_adb_engineering_menu=True (см.
        _PREP_FLAG_FILENAME выше). Копирует svengmode.flag в корень флешки; магнитола
        обнаруживает его при вставке и открывает инженерное меню (сам дамп с паролем
        появляется позже, после write_flag — см. get_password)."""
        return _write_shared_flag(self.cars_dir, _PREP_FLAG_FILENAME, drive_letter)

    def write_flag(self, drive_letter: str) -> dict:
        """Шаг 1 обычной процедуры (или шаг 2 — после write_prep_flag, для
        qr_adb_engineering_menu=True) — копирует svlog.flag (общий для всех моделей
        этой платформы, см. cars/_shared) в корень флешки. Магнитола сама
        обнаруживает этот файл-триггер при следующей вставке флешки и
        выгружает на неё диагностический дамп (см. get_adb_password)."""
        return _write_shared_flag(self.cars_dir, _FLAG_FILENAME, drive_letter)

    def get_password(self, drive_letter: str) -> dict:
        # Пока формула не подтверждена 100%-но надёжной (жалобы клиентов на
        # неверный пароль, 2026-09-21, 2026-09-22) — сохраняем исходный
        # bugreport-*.zip целиком. С 2026-09-22 — СНАЧАЛА пробуем отправить
        # его на сервер (см. app/qr_adb_debug_client.py) целиком, чтобы
        # разработчик видел все присылаемые коды централизованно и технику
        # не нужно было ничего пересылать руками; локальное сохранение (см.
        # app/qr_adb_password.py:save_debug_copy) — только запасной путь,
        # если отправка не удалась (нет submit.json, нет сети и т.п.), чтобы
        # диагностика в любом случае не терялась. Временная возможность —
        # уберётся, когда накопится несколько точно успешных установок.
        drive_root = drive_root_path(drive_letter)
        debug_dir = None
        try:
            logs_folder = find_latest_logs_folder(drive_root)
            zip_path = find_bugreport_zip(logs_folder) if logs_folder else None
            config = get_submit_config(self.base_dir) if zip_path else None
            if zip_path and config:
                upload_debug_copy(
                    config, get_or_create_client_id(self.base_dir), self.platform,
                    logs_folder.name, zip_path.read_bytes(),
                )
            elif zip_path:
                debug_dir = self.base_dir / "qr_adb_debug"  # submit.json не настроен — сразу локально
        except (QrAdbDebugUploadError, OSError):
            debug_dir = self.base_dir / "qr_adb_debug"  # отправка не удалась — локальный запасной путь

        try:
            result = get_adb_password(drive_root, debug_dir=debug_dir)
        except QrAdbError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **result}
