"""Понятное окно вместо трассировки, когда программа не запускается из-за повреждённой установки. Скриншот техника
(Honor на Windows 11, 30.09): после автообновления — «Unhandled exception in script … Cannot find win-arm64» и
трассировка; техник удалил программу и поставил начисто, потеряв модели и настройки, хотя хватило бы запустить
установщик поверх. Отдельный модуль без pywebview — чтобы его проверяли тесты (tests/test_startup_errors.py)."""
from __future__ import annotations

REINSTALL_HINT = ("Скачайте установщик с сайта magicsqd.ru и запустите его — удалять программу не нужно: "
                  "модели, приложения и настройки сохранятся.")


def broken_install_message(exc: BaseException) -> str | None:
    """Текст для техника, если запуск упал на недостающем файле самой программы; иначе None (прежнее окно).
    FileNotFoundError — pywebview не нашёл свои файлы (webview/util.py: interop_dll_path, «Cannot find win-arm64»);
    ImportError — нет модуля программы; «You must have pythonnet installed» — pywebview не загрузил pythonnet."""
    broken = isinstance(exc, (FileNotFoundError, ImportError)) or (
        type(exc).__name__ == "WebViewException" and "pythonnet" in str(exc))
    if not broken:
        return None
    return ("Файлы программы повреждены — скорее всего, прервалось обновление.\n\n"
            f"{REINSTALL_HINT}\n\nПодробности: {exc}")
