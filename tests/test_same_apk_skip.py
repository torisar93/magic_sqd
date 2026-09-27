"""Уже стоящий на магнитоле ТОТ ЖЕ файл не ставится заново (владелец, 2026-09-27). После «Файл не скачан» или
обрыва связи этап повторяли, и все уже поставленные приложения ставились снова (лог #1347: WiFi Manager и Back
Button — по три раза). Сравниваем SHA-256 файла с base.apk на магнитоле, а не versionCode: у модов номер версии
обычно как у оригинала. Здесь — ПК (app/install_context.py); Android — tests/test_android_replug_and_scan.py."""
from __future__ import annotations
import hashlib
import threading
from types import SimpleNamespace

import pytest

from app import install_context
from app.install_context import InstallContext


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    apk = tmp_path / "WiFi+Manager.apk"
    apk.write_bytes(b"apk-bytes-v4.3.0")
    monkeypatch.setattr(install_context, "read_package_name", lambda path: "org.kman.WifiManager")
    log = []
    context = InstallContext(adb_path="fake-adb", device_serial="fake-device", model_dir=tmp_path,
                             selected_apks=[apk], log_fn=log.append, cancel_flag=threading.Event(), shared_dir=None)
    context.require_device = lambda: None
    installs, after = [], []
    context.install_apk_auto = lambda path, extra_args=None: installs.append(path.name)
    context._after_app_installed = lambda path, mock, installed_now=True: after.append((path.name, installed_now))
    context.test = SimpleNamespace(apk=apk, log=log, installs=installs, after=after)
    return context


def _device(ctx, responses):
    commands = []

    def shell(command, check=True, timeout=120):
        commands.append(command)
        return SimpleNamespace(stdout=responses.get(command, ""), stderr="", returncode=0)

    ctx.shell = shell
    return commands


def test_same_file_on_head_unit_is_not_installed_again(ctx):
    digest = hashlib.sha256(ctx.test.apk.read_bytes()).hexdigest()
    _device(ctx, {"pm path org.kman.WifiManager": "package:/data/app/~~a==/org.kman.WifiManager-b==/base.apk\n",
                  "sha256sum /data/app/~~a==/org.kman.WifiManager-b==/base.apk":
                      f"{digest}  /data/app/~~a==/org.kman.WifiManager-b==/base.apk\n"})
    ctx.install_selected_apks()
    assert ctx.test.installs == []
    assert ctx.test.after == [("WiFi+Manager.apk", False)]  # разрешения — да, в «откат в сток» — нет
    assert "«WiFi+Manager.apk»: на магнитоле уже стоит этот же файл — установку пропускаю." in ctx.test.log


@pytest.mark.parametrize("responses", [
    # другая сборка с тем же пакетом (мод вместо оригинала) — ставим
    {"pm path org.kman.WifiManager": "package:/data/app/x/base.apk\n", "sha256sum /data/app/x/base.apk": "0" * 64 + "  x\n"},
    # приложения на магнитоле нет
    {},
    # приложение из нескольких файлов — не сравнить
    {"pm path org.kman.WifiManager": "package:/data/app/x/base.apk\npackage:/data/app/x/split_config.arm64_v8a.apk\n"},
    # на старой прошивке нет sha256sum
    {"pm path org.kman.WifiManager": "package:/data/app/x/base.apk\n", "sha256sum /data/app/x/base.apk": ""},
])
def test_anything_unclear_installs_as_before(ctx, responses):
    _device(ctx, responses)
    ctx.install_selected_apks()
    assert ctx.test.installs == ["WiFi+Manager.apk"]
    assert ctx.test.after == [("WiFi+Manager.apk", True)]
