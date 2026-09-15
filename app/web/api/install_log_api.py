"""Обёртка install_log_client.py/submit_config.py — вызывается из JS
(app.js/stage_wizard.js) по завершении/уходу из мастера установки, только
если лог не пустой (техник реально что-то делал). Синхронный вызов, тот же
приём, что и у ReportApi — маленький JSON, обычное ожидание промиса в JS,
best-effort (сетевая ошибка тут не должна ничего показывать технику)."""
from ...install_log_client import InstallLogError, send_install_log
from ...ping_client import get_or_create_client_id
from ...submit_config import get_submit_config


class InstallLogApi:
    def __init__(self, base_dir):
        self.base_dir = base_dir

    def send(self, platform: str, brand: str, model: str, modification: str,
              success: bool, log_text: str, email: str | None = None) -> dict:
        config = get_submit_config(self.base_dir)
        if not config:
            return {"ok": False, "error": "submit.json не настроен"}
        client_id = get_or_create_client_id(self.base_dir)
        try:
            send_install_log(platform, brand, model, modification, success, log_text,
                              client_id, config, email)
        except InstallLogError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}
