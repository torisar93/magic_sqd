"""Окно «Установка приложений» (progress08.js) на ПК получает ход установки
КАЖДОГО выбранного APK — ту же последовательность событий apk_progress, что
шлёт Android (InstallEngine.installApksWithProgress → WebBridge.kt:
pushApkProgress): «устанавливается» (готово = index, фаза install) → «готово»
(index + 1) или «ошибка».

Реальный случай (владелец, macOS, v1.0.32, 2026-09-23): десктоп слал только
скачивание, и после него окно так и оставалось на «Скачивание приложений», а
в итоге писало «Готово 0 из 3» со статусами строк от скачивания."""
from __future__ import annotations
import threading

import pytest

from app.install_context import AppInstallFailed, InstallCancelled, InstallContext


def make_ctx(tmp_path, names, events, outcomes):
    paths = []
    for name in names:
        apk = tmp_path / name
        apk.write_bytes(b"not a real apk")
        paths.append(str(apk))
    ctx = InstallContext("adb", "SERIAL", tmp_path, paths, log_fn=lambda message: None,
                         cancel_flag=threading.Event(), on_apk_progress=lambda *event: events.append(event))

    def install(apk, extra_args=None):
        outcome = outcomes.get(apk.name)
        if outcome is not None:
            raise outcome

    ctx.install_apk_auto = install
    ctx._after_app_installed = lambda apk, give_mock_location: None
    return ctx, paths


def test_each_apk_reports_running_then_done_or_error(tmp_path):
    events = []
    ctx, (gstore, weather, radio) = make_ctx(
        tmp_path, ["gstore.apk", "weather.apk", "radio.apk"], events,
        {"weather.apk": AppInstallFailed("weather.apk: INSTALL_FAILED_OLDER_SDK")})

    ctx.install_selected_apks()

    assert events == [
        (gstore, 0, 3, "running", "install"), (gstore, 1, 3, "done", None),
        (weather, 1, 3, "running", "install"), (weather, 1, 3, "error", None),
        (radio, 2, 3, "running", "install"), (radio, 3, 3, "done", None),
    ]
    assert ctx.failed_apps == ["weather.apk: INSTALL_FAILED_OLDER_SDK"]


def test_stopped_apk_is_marked_error_and_the_rest_never_start(tmp_path):
    events = []
    ctx, (first, second, third) = make_ctx(
        tmp_path, ["a.apk", "b.apk", "c.apk"], events,
        {"b.apk": InstallCancelled("Установка остановлена пользователем.")})

    with pytest.raises(InstallCancelled):
        ctx.install_selected_apks()

    assert events == [
        (first, 0, 3, "running", "install"), (first, 1, 3, "done", None),
        (second, 1, 3, "running", "install"), (second, 1, 3, "error", None),
    ]


def test_bridge_event_has_the_android_fields(monkeypatch):
    from app.web.api import install_api
    pushed = []
    monkeypatch.setattr(install_api.event_bridge, "push", pushed.append)

    install_api.InstallApi._push_apk_install(2, "/apk/a.apk", 0, 3, "running", "install")
    install_api.InstallApi._push_apk_install(2, "/apk/a.apk", 1, 3, "done", None)
    install_api.InstallApi._push_apk_install(None, "/apk/a.apk", 1, 3, "done", None)  # этап неизвестен — молчим

    assert pushed == [
        {"kind": "apk_progress", "stage_index": 2, "path": "/apk/a.apk", "completed": 0, "total": 3,
         "state": "running", "phase": "install", "determinate": False},
        {"kind": "apk_progress", "stage_index": 2, "path": "/apk/a.apk", "completed": 1, "total": 3,
         "state": "done"},
    ]
