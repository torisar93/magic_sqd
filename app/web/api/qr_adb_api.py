"""Bridge для диалога "Пароль ADB по флешке (QR-код)" — см.
app/qr_adb_password.py за самим алгоритмом. Список дисков переиспользует
usb_api.py (тот же usb_utils.list_drives), отдельного списка тут нет."""
from __future__ import annotations
from pathlib import Path

from ...qr_adb_password import QrAdbError, get_adb_password


class QrAdbApi:
    def get_password(self, drive_letter: str) -> dict:
        try:
            result = get_adb_password(Path(f"{drive_letter}\\"))
        except QrAdbError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **result}
