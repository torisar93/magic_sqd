"""ПК: установка останавливается сразу и понятно, когда перебирать способы
установки бессмысленно (разбор логов 2026-09-23):
- #648: магнитола не подключена — все 8 способов подряд падали с «no
  devices/emulators found»; #557: магнитола отвалилась посреди установки
  («device '…' not found»);
- #570: APK не скачались (нет интернета) — каждый файл проходил все способы
  с «cannot stat …: No such file». Android в таком случае так же сразу
  останавливается («Файл не скачан»)."""
from __future__ import annotations
import threading

import pytest

from app.adb_utils import AdbError
from app.install_context import AppInstallFailed, InstallCancelled, InstallContext


def _ctx(tmp_path, names, log, create=True):
    apks = []
    for name in names:
        path = tmp_path / name
        if create:
            path.write_bytes(b"")
        apks.append(path)
    ctx = InstallContext(adb_path="fake-adb", device_serial="fake-device", model_dir=tmp_path,
                         selected_apks=apks, log_fn=log.append, cancel_flag=threading.Event(), shared_dir=None)
    ctx._after_app_installed = lambda apk, mock: None
    return ctx, apks


@pytest.mark.parametrize("error", [
    r"C:\adb.exe install -r C:\a.apk adb.exe: no devices/emulators found",
    "adb: error: failed to get feature set: device '0123456789ABCDEF' not found",
    "error: device offline",
])
def test_no_device_stops_after_first_method(tmp_path, error):
    log = []
    ctx, apks = _ctx(tmp_path, ["a.apk"], log)
    calls = []

    def fake_install(method, path, extra_args):
        calls.append(method)
        raise AdbError(error)

    ctx._install_with_method = fake_install

    with pytest.raises(InstallCancelled, match="не подключена или отключилась"):
        ctx.install_apk_auto(apks[0])
    assert len(calls) == 1  # раньше — все 8 способов подряд
    assert any("не сработало" in line for line in log)  # причина всё равно в логе


def test_unauthorized_asks_to_confirm_debugging(tmp_path):
    log = []
    ctx, apks = _ctx(tmp_path, ["a.apk"], log)
    ctx._install_with_method = lambda method, path, extra_args: (_ for _ in ()).throw(
        AdbError("adb: device unauthorized.\nThis adb server's $ADB_VENDOR_KEYS is not set"))

    with pytest.raises(InstallCancelled, match="Разрешить отладку"):
        ctx.install_apk_auto(apks[0])


def test_device_lost_after_method_is_locked_stops_the_whole_list(tmp_path):
    # Способ уже сработал на первом приложении, магнитола отвалилась на втором —
    # это не «пропустить одно приложение» (AppInstallFailed), а конец установки.
    log = []
    ctx, apks = _ctx(tmp_path, ["a.apk", "b.apk", "c.apk"], log)
    calls = []

    def fake_install(method, path, extra_args):
        calls.append(path.name)
        if path.name == "b.apk":
            raise AdbError("adb.exe: device 'fake-device' not found")

    ctx._install_with_method = fake_install

    with pytest.raises(InstallCancelled, match="не подключена или отключилась"):
        ctx.install_selected_apks()
    assert calls == ["a.apk", "b.apk"]  # c.apk уже не пробовали
    assert ctx.failed_apps == []


def test_ordinary_method_failure_still_tries_the_next_method(tmp_path):
    log = []
    ctx, apks = _ctx(tmp_path, ["a.apk"], log)
    calls = []

    def fake_install(method, path, extra_args):
        calls.append(method)
        if len(calls) < 3:
            raise AdbError("Failure [INSTALL_FAILED_INVALID_APK]")

    ctx._install_with_method = fake_install

    ctx.install_apk_auto(apks[0])
    assert len(calls) == 3 and ctx._install_method == calls[-1]


def test_not_downloaded_apps_stop_before_touching_the_device(tmp_path):
    log = []
    ctx, apks = _ctx(tmp_path, ["here.apk"], log)
    ctx.selected_apks += [tmp_path / "GLauncher.Link.1.1.apk", tmp_path / "Podpratel Pro.apk"]
    ctx._install_with_method = lambda *a: pytest.fail("до установки не должно дойти")

    with pytest.raises(InstallCancelled) as exc_info:
        ctx.install_selected_apks()
    text = str(exc_info.value)
    assert "Не скачаны приложения: GLauncher.Link.1.1.apk, Podpratel Pro.apk" in text
    assert "here.apk" not in text and "Проверьте интернет" in text


def test_single_app_failure_is_still_skipped_not_fatal(tmp_path):
    # Прежнее поведение (логи #390/#391) не сломано: обычный сбой одного приложения
    # после лока способа — пропуск, остальные ставятся.
    log = []
    ctx, apks = _ctx(tmp_path, ["a.apk", "b.apk", "c.apk"], log)
    ctx._install_with_method = lambda method, path, extra_args: (
        (_ for _ in ()).throw(AdbError("Success")) if path.name == "b.apk" else None)

    ctx.install_selected_apks()
    assert len(ctx.failed_apps) == 1 and "b.apk" in ctx.failed_apps[0]
    with pytest.raises(AppInstallFailed):
        ctx.install_apk_auto(apks[1])
