"""main_web.py:get_base_dir — на macOS, в собранном .app (sys.frozen), теперь
возвращает ~/Library/Application Support/MagicSQD/, а не папку рядом с exe
(Contents/MacOS/ — то есть ВНУТРИ подписанного бандла). Синхронизация cars/
apk туда после сборки ломала codesign-печать бандла (codesign --verify
--deep --strict: "a sealed resource is missing or invalid", подтверждено на
живой установленной копии) — Gatekeeper потом сильнее ограничивал
приложение при обычном запуске через Finder/Dock, из-за чего ADB переставал
видеть подключённое по USB устройство именно так (но не при запуске из
Терминала через `open` — реальный случай, воспроизведено и объяснено в
сессии 2026-09-22). На Windows и при запуске из исходников (sys.frozen
отсутствует) поведение НЕ меняется — это должны подтверждать регрессионные
тесты ниже."""
from __future__ import annotations
import sys
from pathlib import Path

import main_web


def test_macos_frozen_uses_application_support(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/Magic SQD.app/Contents/MacOS/magic_sqd")
    fake_home = Path("/Users/tester")
    monkeypatch.setattr(main_web.Path, "home", staticmethod(lambda: fake_home))

    result = main_web.get_base_dir()

    assert result == fake_home / "Library" / "Application Support" / "MagicSQD"


def test_macos_frozen_data_dir_is_outside_any_app_bundle(monkeypatch):
    """Прямая регрессия на сам баг — что бы ни возвращала get_base_dir на
    заморожённой macOS-сборке, в пути не должно быть ни ".app", ни
    "Contents" (иначе мы снова внутри подписанного бандла)."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/Magic SQD.app/Contents/MacOS/magic_sqd")
    monkeypatch.setattr(main_web.Path, "home", staticmethod(lambda: Path("/Users/tester")))

    result = main_web.get_base_dir()

    assert ".app" not in str(result)
    assert "Contents" not in result.parts


def test_windows_frozen_unchanged_next_to_exe(monkeypatch):
    """pathlib.Path берёт "вкус" (Posix/Windows) от РЕАЛЬНОЙ ОС теста, а не
    от monkeypatch'нутого sys.platform — поэтому не зашиваем сюда буквальный
    вид Windows-пути (на POSIX-раннере CI это было бы неверно интерпретировано
    ОДИНАКОВО что фактическим кодом, что тестом, и тест ложно-положительно
    прошёл бы, не проверяя ничего осмысленного). Вместо этого проверяем
    именно то, что реально должно быть неизменным: get_base_dir по-прежнему
    просто берёт родителя sys.executable, БЕЗ каких-либо macOS-специфичных
    перенаправлений — тем же способом Path, что и сам код."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    fake_exe = str(Path(main_web.__file__).resolve().parent / "magic_sqd.exe")
    monkeypatch.setattr(sys, "executable", fake_exe)

    result = main_web.get_base_dir()

    assert result == Path(fake_exe).resolve().parent
    assert result == Path(main_web.__file__).resolve().parent


def test_macos_not_frozen_unchanged_script_dir(monkeypatch):
    """Запуск из исходников (разработка/тесты) — поведение как раньше, даже
    на macOS: НЕ должно внезапно требовать Application Support на машине
    разработчика."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    result = main_web.get_base_dir()

    assert result == Path(main_web.__file__).resolve().parent


def test_windows_not_frozen_unchanged_script_dir(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", False, raising=False)

    result = main_web.get_base_dir()

    assert result == Path(main_web.__file__).resolve().parent
