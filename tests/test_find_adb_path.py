"""app/adb_utils.py:find_adb_path — на реальной установленной macOS-копии
всегда возвращал голое "adb" (откат на PATH) вместо бандленного tools_mac/adb,
из-за неверного bundled_tools_root (см. tests/test_platform_paths.py и
память project_macos_finder_launch_adb_bug за полным разбором). Здесь —
сквозная проверка на реалистичной структуре .app в tmp_path: именно то, что
техник получал бы на живой машине."""
from __future__ import annotations
import sys
from pathlib import Path

from app.adb_utils import find_adb_path


def _make_fake_bundle(tmp_path: Path) -> Path:
    """<tmp>/MagicSQD.app/Contents/{MacOS/magic_sqd, Resources/tools_mac/adb}
    — та же структура, что и у настоящего собранного .app."""
    app = tmp_path / "MagicSQD.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (macos / "magic_sqd").write_text("#!/bin/sh\n")
    tools_mac = app / "Contents" / "Resources" / "tools_mac"
    tools_mac.mkdir(parents=True)
    (tools_mac / "adb").write_text("fake adb binary")
    return app


def test_macos_frozen_finds_bundled_adb_even_when_meipass_points_elsewhere(monkeypatch, tmp_path):
    """Регрессия на реальный баг: _MEIPASS указывает на несуществующий
    Frameworks/tools_mac (как оказалось в реальной сборке PyInstaller) —
    find_adb_path всё равно должен найти tools_mac/adb в Resources/."""
    app = _make_fake_bundle(tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "Contents" / "MacOS" / "magic_sqd"))
    monkeypatch.setattr(sys, "_MEIPASS", str(app / "Contents" / "Frameworks"), raising=False)

    result = find_adb_path(Path("/unrelated/base_dir"))

    assert result == str(app / "Contents" / "Resources" / "tools_mac" / "adb")


def test_macos_frozen_falls_back_to_path_when_bundle_truly_missing(monkeypatch, tmp_path):
    """Если tools_mac реально нет (повреждённая установка) — откат на
    голое "adb" по-прежнему должен работать как задокументированный запасной
    путь, а не падать."""
    app = tmp_path / "MagicSQD.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app / "Contents" / "MacOS" / "magic_sqd"))

    result = find_adb_path(Path("/unrelated/base_dir"))

    assert result == "adb"
