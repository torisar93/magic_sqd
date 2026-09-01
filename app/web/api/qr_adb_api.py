"""Bridge для этапа "Пароль ADB по QR-коду" (app/web/frontend/js/screens/
stage_wizard.js: renderQrAdbStage) — см. app/qr_adb_password.py за самим
алгоритмом. Список дисков переиспользует usb_api.py (тот же
usb_utils.list_drives), отдельного списка тут нет."""
from __future__ import annotations
import shutil
from pathlib import Path

from ...qr_adb_password import QrAdbError, get_adb_password

_FLAG_FILENAME = "svlog.flag"


class QrAdbApi:
    def __init__(self, cars_dir: Path):
        self.cars_dir = Path(cars_dir)

    def write_flag(self, drive_letter: str) -> dict:
        """Шаг 1 процедуры — копирует svlog.flag (общий для всех моделей
        этой платформы, см. cars/_shared) в корень флешки. Магнитола сама
        обнаруживает этот файл-триггер при следующей вставке флешки и
        выгружает на неё диагностический дамп (см. get_adb_password)."""
        src = self.cars_dir / "_shared" / _FLAG_FILENAME
        if not src.is_file():
            return {
                "ok": False,
                "error": f"{_FLAG_FILENAME} не найден в cars/_shared — обновите каталог (Настройки → "
                         "Проверить обновления) и попробуйте снова.",
            }
        try:
            shutil.copyfile(src, Path(f"{drive_letter}\\{_FLAG_FILENAME}"))
        except OSError as exc:
            return {"ok": False, "error": f"Не удалось записать на флешку {drive_letter}: {exc}"}
        return {"ok": True}

    def get_password(self, drive_letter: str) -> dict:
        try:
            result = get_adb_password(Path(f"{drive_letter}\\"))
        except QrAdbError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **result}
