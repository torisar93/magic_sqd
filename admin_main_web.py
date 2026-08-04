"""Точка входа админ-сборки (pywebview-интерфейс) — тот же app/web/, что и
main_web.py, только admin_mode=True (прячет adb-консоль, показывает кнопку
"Выгрузить на сервер...", см. app/web/api/admin_api.py) и отдельные имена
лог-файлов, чтобы установленная рядом обычная сборка не делила их с этой
(как раньше main.py/admin_main.py).

Запуск: python admin_main_web.py (собранный вариант — magic_sqd_admin.exe,
см. admin.spec)."""
from main_web import _run_with_crash_log

ADMIN_APP_TITLE = "Magic SQD — админ-панель контента"

if __name__ == "__main__":
    _run_with_crash_log(admin_mode=True, log_prefix="admin_", title=ADMIN_APP_TITLE)
