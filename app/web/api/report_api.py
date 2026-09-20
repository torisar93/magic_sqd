"""Обёртка report_client.py/submit_config.py для диалога "Сообщить о
проблеме" — портировано из app/report_dialog.py. Синхронный вызов (не через
event_bridge): отправка жалобы — разовое действие в несколько КБ, а не
долгий процесс с прогрессом, достаточно обычного ожидания промиса в JS."""
from ...ping_client import get_or_create_client_id
from ...report_client import ReportError, send_report
from ...submit_config import get_submit_config
from ...version import APP_VERSION

REASONS = [
    "Появился способ установки",
    "Инструкция больше не актуальна",
    "Появилась новая версия",
    "Не работает этап установки",
    "Ошибка в работе программы",
    "Не находит устройство или флешку",
    "Не скачивается или не обновляется",
    "Предложение или идея",
    "Другое",
]


class ReportApi:
    def __init__(self, base_dir):
        self.base_dir = base_dir

    def get_info(self) -> dict:
        return {"available": get_submit_config(self.base_dir) is not None, "reasons": REASONS}

    def send(self, brand: str, model: str, reason: str, description: str, platform: str = "") -> dict:
        config = get_submit_config(self.base_dir)
        if not config:
            return {"ok": False, "error": "submit.json не настроен — отправка недоступна."}
        try:
            send_report(brand, model, reason, description, config, app_version=APP_VERSION,
                        platform=platform, client_id=get_or_create_client_id(self.base_dir))
        except ReportError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "message": "Спасибо! Обращение отправлено."}
