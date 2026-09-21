"""Автообновление на macOS выбирает .dmg по platform.machine() ("x86_64" или "arm64") — но это
архитектура ТЕКУЩЕГО процесса, а не железа: у x86_64-сборки, запущенной на Apple Silicon через Rosetta 2,
platform.machine() тоже вернёт "x86_64", неотличимо от настоящего Intel Mac. Реальный случай (2026-09-21,
MacBook Air M4): клиент однажды оказался на x86_64-копии и с тех пор самообновления НИКОГДА не предлагали
нативный arm64 — программа считала его Intel-Mac'ом навсегда. _mac_is_translated() (sysctl.proc_translated)
различает эти два случая — см. app/web/api/update_api.py: UpdateApi.__init__/_own_server_asset_key."""
from __future__ import annotations

import subprocess
import types

import pytest

from app.web.api import update_api as update_api_module
from app.web.api.update_api import UpdateApi, _mac_is_translated


def _fake_sysctl(output: str | None, raises: bool = False):
    def run(cmd, **kwargs):
        if raises:
            raise OSError("sysctl не найден")
        return types.SimpleNamespace(stdout=output)
    return run


def test_mac_is_translated_true_under_rosetta(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_sysctl("1\n"))
    assert _mac_is_translated() is True


def test_mac_is_translated_false_on_real_arch(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_sysctl("0\n"))
    assert _mac_is_translated() is False


def test_mac_is_translated_false_when_sysctl_missing(monkeypatch):
    # Настоящий Intel Mac без Rosetta вообще (или любая другая ОС) — sysctl.proc_translated
    # может не существовать; не должно ронять проверку обновлений.
    monkeypatch.setattr(subprocess, "run", _fake_sysctl(None, raises=True))
    assert _mac_is_translated() is False


def _make_mac_update_api(monkeypatch, tmp_path, machine: str, translated: bool):
    monkeypatch.setattr(update_api_module.sys, "platform", "darwin")
    monkeypatch.setattr(update_api_module.platform, "machine", lambda: machine)
    monkeypatch.setattr(update_api_module, "_mac_is_translated", lambda: translated)
    return UpdateApi(tmp_path)


def test_real_intel_mac_still_gets_x64_asset(monkeypatch, tmp_path):
    """Настоящий Intel Mac (x86_64, НЕ под Rosetta) — поведение не меняется."""
    api = _make_mac_update_api(monkeypatch, tmp_path, machine="x86_64", translated=False)
    assert api._mac_wants_x64 is True
    assert api._asset_re is update_api_module._ASSET_RE_MAC_X64
    assert api._own_server_asset_key() == "macos_x86_64"


def test_apple_silicon_under_rosetta_gets_native_arm64_asset(monkeypatch, tmp_path):
    """Реальный случай: x86_64-сборка, физически исполняется на Apple Silicon через Rosetta —
    ДОЛЖНА предложить нативный arm64, а не продолжать качать x86_64 по кругу."""
    api = _make_mac_update_api(monkeypatch, tmp_path, machine="x86_64", translated=True)
    assert api._mac_wants_x64 is False
    assert api._asset_re is update_api_module._ASSET_RE_MAC_ARM64
    assert api._own_server_asset_key() == "macos_arm64"


def test_native_arm64_process_unaffected(monkeypatch, tmp_path):
    """arm64-сборка, запущенная нативно — platform.machine() уже "arm64", _mac_is_translated
    сюда даже не должна попасть по-настоящему (проверяем, что arm64 остаётся arm64)."""
    api = _make_mac_update_api(monkeypatch, tmp_path, machine="arm64", translated=False)
    assert api._mac_wants_x64 is False
    assert api._asset_re is update_api_module._ASSET_RE_MAC_ARM64
    assert api._own_server_asset_key() == "macos_arm64"
