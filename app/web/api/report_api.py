"""Обёртка report_client.py/submit_config.py для диалога "Сообщить о
проблеме" — портировано из app/report_dialog.py. Синхронный вызов (не через
event_bridge): отправка жалобы — разовое действие в несколько КБ, а не
долгий процесс с прогрессом, достаточно обычного ожидания промиса в JS.

Почта для ответа (владелец, 2026-09-23: ответ на обращение приходит письмом): у вошедшего в
аккаунт — почта аккаунта (сервер берёт её из сессии), у остальных — поле в окне; вписанную
почту запоминаем для следующего обращения (report_contact.json рядом с данными программы)."""
import json

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


CONTACT_FILENAME = "report_contact.json"


class ReportApi:
    def __init__(self, base_dir):
        self.base_dir = base_dir

    def _saved_email(self) -> str:
        try:
            value = json.loads((self.base_dir / CONTACT_FILENAME).read_text(encoding="utf-8")).get("email")
        except (OSError, ValueError, AttributeError):
            return ""
        return value if isinstance(value, str) else ""

    def _save_email(self, email: str) -> None:
        try:
            (self.base_dir / CONTACT_FILENAME).write_text(json.dumps({"email": email}), encoding="utf-8")
        except OSError:
            pass  # не запомнили — в следующий раз впишут заново

    def get_info(self, account_email: str | None = None) -> dict:
        return {"available": get_submit_config(self.base_dir) is not None, "reasons": REASONS,
                "account_email": account_email or "", "saved_email": self._saved_email()}

    def send(self, brand: str, model: str, reason: str, description: str, platform: str = "",
             email: str = "", session_cookie: str = "", account_email: str = "") -> dict:
        config = get_submit_config(self.base_dir)
        if not config:
            return {"ok": False, "error": "submit.json не настроен — отправка недоступна."}
        email = "" if account_email else (email or "").strip()
        try:
            send_report(brand, model, reason, description, config, app_version=APP_VERSION,
                        platform=platform, client_id=get_or_create_client_id(self.base_dir),
                        email=email, session_cookie=session_cookie if account_email else "")
        except ReportError as exc:
            return {"ok": False, "error": str(exc)}
        if email:
            self._save_email(email)
        reply_to = account_email or email
        return {"ok": True, "message": f"Спасибо! Обращение отправлено. Если понадобится ответ, он придёт на почту {reply_to}."
                if reply_to else "Спасибо! Обращение отправлено."}
