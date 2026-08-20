"""Куда отправлять машину, собранную в мастере "Добавить машину...", на
проверку разработчику, и куда слать жалобы ("Сообщить о проблеме" в
главном окне, см. app/report_client.py) — настраивается вручную (см.
server/README.md).

Создайте файл submit.json рядом с main.py/.exe:
    {"submit_url": "https://ваш-домен/submit", "submit_key": "..."}

Если submit.json нет или заполнен не полностью — кнопки отправки/жалобы
просто не предлагаются, всё остальное в программе работает как обычно.
report_url/ping_url отдельно в файле не хранятся — тот же сервер, тот же
ключ, только путь "/submit" -> "/report"/"/ping" (см. nginx_magicsqd.conf:
все три — top-level location на одном хосте). Без submit.json программа
также не шлёт пульс (см. app/ping_client.py, gui.py) — счётчик пользователей
в админке в этом случае не увидит эту установку, это ожидаемо."""
from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SubmitConfig:
    submit_url: str
    submit_key: str

    @property
    def report_url(self) -> str:
        return self.submit_url.rsplit("/", 1)[0] + "/report"

    @property
    def ping_url(self) -> str:
        return self.submit_url.rsplit("/", 1)[0] + "/ping"

    @property
    def diagnostics_url(self) -> str:
        # DEBUG-сборка (см. main_web.py:_start_debug_uploader) — тот же
        # сервер/ключ, путь "/submit" -> "/diagnostics" (см.
        # server/backend.py: DIAGNOSTICS_DIR).
        return self.submit_url.rsplit("/", 1)[0] + "/diagnostics"


def get_submit_config(base_dir: Path) -> SubmitConfig | None:
    path = base_dir / "submit.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    url = data.get("submit_url")
    key = data.get("submit_key")
    if not url or not key:
        return None
    return SubmitConfig(submit_url=url, submit_key=key)
