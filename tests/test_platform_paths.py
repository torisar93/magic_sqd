"""app/platform_paths.py:bundled_tools_root — реальный найденный баг
(2026-09-22): на актуальной версии PyInstaller (6.x, onedir + BUNDLE) на
macOS sys._MEIPASS указывает на Contents/Frameworks/, а НЕ на
Contents/Resources/, куда build-release.yml/scripts/build_intel_dmg.sh
реально кладут tools_mac/ (отдельным шагом ПОСЛЕ сборки PyInstaller —
никогда не попадает в список того, что PyInstaller сам симлинкует из
Frameworks/ в Resources/, в отличие от app/, certifi/ и т.п., см. докстринг
функции). find_adb_path (app/adb_utils.py) поэтому ВСЕГДА тихо откатывался
на голое "adb" из PATH — на технике без Android SDK/другой копии adb в
PATH такого пути попросту нет: программа никогда не находила ни одного
устройства по USB, на любой сборке. Подтверждено отладочным логом на живой
установленной копии — см. память project_macos_finder_launch_adb_bug."""
from __future__ import annotations
import sys
from pathlib import Path

from app.platform_paths import bundled_tools_root


def test_macos_frozen_ignores_meipass_uses_executable_relative_resources(monkeypatch):
    """Прямая регрессия на сам баг: даже если sys._MEIPASS указывает на
    Frameworks/ (как оказалось в реальности) — результат должен быть
    Resources/, вычисленный от sys.executable, а не от _MEIPASS."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/Magic SQD.app/Contents/MacOS/magic_sqd")
    monkeypatch.setattr(sys, "_MEIPASS", "/Applications/Magic SQD.app/Contents/Frameworks", raising=False)

    result = bundled_tools_root(Path("/ignored/base_dir"))

    assert result == Path("/Applications/Magic SQD.app/Contents/Resources")


def test_macos_frozen_result_does_not_depend_on_base_dir_argument(monkeypatch):
    """base_dir теперь (после отдельной правки get_base_dir) может быть
    вообще не связан с расположением .app (~/Library/Application Support/…)
    — bundled_tools_root не должен от него зависеть на macOS-фриз."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/Magic SQD.app/Contents/MacOS/magic_sqd")

    result = bundled_tools_root(Path("/Users/someone/Library/Application Support/MagicSQD"))

    assert result == Path("/Applications/Magic SQD.app/Contents/Resources")


def test_windows_unaffected_still_uses_base_dir(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    fake_base = Path("/some/base/dir")

    result = bundled_tools_root(fake_base)

    assert result == fake_base


def test_not_frozen_source_run_unaffected_even_on_macos(monkeypatch):
    """Запуск из исходников (разработка/тесты) — sys.frozen нет вообще,
    должно остаться base_dir, как и раньше, независимо от платформы."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    fake_base = Path("/repo/checkout")

    result = bundled_tools_root(fake_base)

    assert result == fake_base
