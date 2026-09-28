"""Выдача разрешений в окне установки — своя фаза кольца «Выдача разрешений» с ходом «Разрешение N из M».

Владелец (2026-09-28): скачивание и установка идут с анимацией, а выдача разрешений — без неё, «не понятно,
зависла программа или что вообще происходит». На ПК строка приложения закрывалась «Готово» ДО выдачи, и на
последнем приложении кольцо показывало «Все приложения установлены», пока ещё шли десятки команд pm grant/appops;
на Android кольцо крутилось под надписью «Установка приложения». Теперь: фаза grant (в том числе при выдаче
внутри localinstall/dex_shell), шаги считает cars/_shared/adb_permissions.py (серверный контент — у старых
программ ctx.permission_progress нет, выдача идёт как раньше), «Готово» — после разрешений."""
from __future__ import annotations
import importlib.util
import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.install_context as ic
from app.install_context import InstallCancelled, InstallContext

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "cars" / "_shared"
ANDROID = ROOT / "android/app/src/main/java/ru/magicsqd/mobile"

DUMPSYS = """Packages:
  Package [ru.yandex.music] (c0ffee):
    requested permissions:
      android.permission.INTERNET
      android.permission.RECORD_AUDIO
      android.permission.SYSTEM_ALERT_WINDOW
      android.permission.READ_PHONE_STATE
"""
# dumpsys + 3 pm grant (SYSTEM_ALERT_WINDOW — через appops) + 4 appops + --uid + 7 доп. appops
# + WRITE_SECURE_SETTINGS + Doze + спецвозможности + уведомления
STEPS = 1 + 3 + 4 + 1 + 7 + 4


def load_permissions():
    spec = importlib.util.spec_from_file_location("adb_permissions_grant_progress", SHARED / "adb_permissions.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fake_shell(commands):
    def shell(command, check=True, **kwargs):
        commands.append(command)
        stdout = DUMPSYS if command.startswith("dumpsys package ") else ""
        return SimpleNamespace(stdout=stdout, stderr="", returncode=0)
    return shell


def make_ctx(tmp_path, names, events, commands):
    paths = []
    for name in names:
        apk = tmp_path / name
        apk.write_bytes(b"not a real apk")
        paths.append(str(apk))
    ctx = InstallContext("adb", "SERIAL", tmp_path, paths, log_fn=lambda message: None,
                         cancel_flag=threading.Event(), on_apk_progress=lambda *event: events.append(event),
                         shared_dir=SHARED, device_confirmed=True)
    ctx.shell = fake_shell(commands)
    return ctx, paths


def test_grant_is_its_own_phase_and_row_is_done_only_after_it(tmp_path, monkeypatch):
    monkeypatch.setattr(ic, "read_package_name", lambda apk: "ru.yandex.music")
    events, commands = [], []
    ctx, (music,) = make_ctx(tmp_path, ["music.apk"], events, commands)
    ctx.install_apk_auto = lambda apk, extra_args=None: None

    ctx.install_selected_apks()

    assert events[0] == (music, 0, 1, "running", "install")
    assert events[1] == (music, 0, 1, "running", "grant")  # сразу, до dumpsys — кольцо уже «Выдача разрешений»
    steps = [event[5] for event in events[2:-1]]
    assert steps == [(n, STEPS) for n in range(1, STEPS + 1)]  # по шагу на команду, последний — M из M
    assert all(event[:5] == (music, 0, 1, "running", "grant") for event in events[2:-1])
    assert events[-1] == (music, 1, 1, "done", None)  # «Готово» — только после разрешений
    assert "pm grant ru.yandex.music android.permission.RECORD_AUDIO" in commands


def test_grant_inside_install_method_shows_the_same_phase(tmp_path, monkeypatch):
    """localinstall/dex_shell выдают разрешения сами, ещё внутри способа установки — та же фаза, и после
    установки второй раз не выдаётся."""
    monkeypatch.setattr(ic, "read_package_name", lambda apk: "ru.yandex.music")
    events, commands = [], []
    ctx, (music,) = make_ctx(tmp_path, ["music.apk"], events, commands)
    ctx.install_apk_auto = lambda apk, extra_args=None: ctx._grant_all_permissions_if_available("ru.yandex.music")

    ctx.install_selected_apks()

    phases = [event[4] for event in events]
    assert phases.count("install") == 1 and phases[-1] is None
    assert [event[5] for event in events if len(event) > 5][-1] == (STEPS, STEPS)
    assert commands.count("dumpsys package ru.yandex.music") == 1  # не второй раз после установки


def test_stop_during_grant_still_closes_the_row(tmp_path, monkeypatch):
    monkeypatch.setattr(ic, "read_package_name", lambda apk: "ru.yandex.music")
    events = []
    ctx, (music,) = make_ctx(tmp_path, ["music.apk"], events, [])
    ctx.install_apk_auto = lambda apk, extra_args=None: None

    def stop(apk, mock, installed_now=True):
        raise InstallCancelled("Установка остановлена пользователем.")

    ctx._after_app_installed = stop
    with pytest.raises(InstallCancelled):
        ctx.install_selected_apks()
    assert events[-1] == (music, 1, 1, "done", None)  # приложение уже стоит
    assert ctx._progress_item is None


def test_permissions_button_outside_the_queue_sends_no_progress(tmp_path):
    """«Выдать разрешения» в «Доп. действиях» — окна установки с кольцом нет, событий нет, выдача та же."""
    events, commands = [], []
    ctx, _ = make_ctx(tmp_path, [], events, commands)

    load_permissions().grant_all_permissions(ctx, "ru.yandex.music")

    assert events == []
    assert commands[0] == "dumpsys package ru.yandex.music"


def test_old_program_without_progress_hook_grants_as_before():
    """Серверный модуль у программ ≤ 1.0.46: у их ctx нет permission_progress — те же команды в том же порядке."""
    commands, log = [], []
    ctx = SimpleNamespace(shell=fake_shell(commands), log=log.append)

    load_permissions().grant_all_permissions(ctx, "ru.yandex.music")

    assert commands[:4] == ["dumpsys package ru.yandex.music",
                            "pm grant ru.yandex.music android.permission.INTERNET",
                            "pm grant ru.yandex.music android.permission.RECORD_AUDIO",
                            "pm grant ru.yandex.music android.permission.READ_PHONE_STATE"]
    assert "appops set --uid ru.yandex.music MANAGE_EXTERNAL_STORAGE allow" in commands
    assert log[-1] == "Все разрешения выданы (3)."


def test_bridge_event_carries_steps(monkeypatch):
    from app.web.api import install_api
    pushed = []
    monkeypatch.setattr(install_api.event_bridge, "push", pushed.append)

    install_api.InstallApi._push_apk_install(1, "/apk/music.apk", 0, 2, "running", "grant")
    install_api.InstallApi._push_apk_install(1, "/apk/music.apk", 0, 2, "running", "grant", (5, 20))

    assert pushed[0] == {"kind": "apk_progress", "stage_index": 1, "path": "/apk/music.apk", "completed": 0,
                         "total": 2, "state": "running", "phase": "grant", "determinate": False}
    assert pushed[1] == {**pushed[0], "determinate": True, "steps_done": 5, "steps_total": 20}


def test_android_grant_reports_the_same_steps():
    """Android: AdbPermissions.grantAllPermissions шлёт фазу grant через AdbInstallProgress (область есть и внутри
    localinstall/dex_shell, и вокруг выдачи после установки), шаги считаются как в серверном модуле."""
    permissions = (ANDROID / "usb/AdbPermissions.kt").read_text(encoding="utf-8")
    grant = permissions[permissions.index("fun grantAllPermissions("):permissions.index("private fun linkLostMessage")]
    assert "AdbInstallProgress.granting()" in grant
    assert ("val stepsTotal = 1 + toGrant.size + APPOPS_BY_PERMISSION.size +\n"
            "            APPOPS_BY_PERMISSION.values.count { it == MANAGE_EXTERNAL_STORAGE_OP } + EXTRA_APPOPS.size + 4"
            ) in grant
    # по вызову step() на каждое слагаемое формулы: dumpsys, pm grant, appops, --uid, доп. appops, WSS, Doze, 2 службы
    assert len(re.findall(r"^\s+step\(\)", grant, re.MULTILINE)) == 9

    progress = (ANDROID / "usb/ApkOperationProgress.kt").read_text(encoding="utf-8")
    assert 'ApkOperationProgress("grant", stepsDone = done, stepsTotal = total)' in progress
    engine = (ANDROID / "usb/InstallEngine.kt").read_text(encoding="utf-8")
    assert ("AdbInstallProgress.observe({ onDetail(path, index, apkPaths.size, it) }, { false }) {\n"
            "                            AdbPermissions.grantAllPermissions(pkg, log)") in engine
    bridge = (ANDROID / "WebBridge.kt").read_text(encoding="utf-8")
    assert 'detail.stepsTotal?.let { event.put("steps_total", it) }' in bridge
