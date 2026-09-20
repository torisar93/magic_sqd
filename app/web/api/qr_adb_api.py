"""Bridge для этапа "Пароль ADB по QR-коду" (app/web/frontend/js/screens/
stage_wizard.js: renderQrAdbStage) — см. app/qr_adb_password.py за самим
алгоритмом. Список дисков переиспользует usb_api.py (тот же
usb_utils.list_drives), отдельного списка тут нет."""
from __future__ import annotations
import shutil
from pathlib import Path

from ...qr_adb_password import QrAdbError, get_adb_password

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
        shutil.copyfile(src, Path(f"{drive_letter}\\{filename}"))
    except OSError as exc:
        return {"ok": False, "error": f"Не удалось записать на флешку {drive_letter}: {exc}"}
    return {"ok": True}


class QrAdbApi:
    def __init__(self, cars_dir: Path):
        self.cars_dir = Path(cars_dir)

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
        try:
            result = get_adb_password(Path(f"{drive_letter}\\"))
        except QrAdbError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **result}
