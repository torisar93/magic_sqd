"""Тесты исправлений по разбору логов 24.11.2026 (к 1.1.27):
- apksigner timeout 180 с + client.json (№19185, №19201);
- повтор с --user current при «geely install must have --user» (№17928);
- блокировка UsbFlashSession.connectBlocking + автоотписка ресивера (№18168, №18961, №18595);
- защита от запоминания pm_install вместо jdwp_whitelist после JDWP-патча (№17850)."""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import apk_signer, client_config
from app.adb_utils import AdbError
from app.install_context import InstallContext

ROOT = Path(__file__).resolve().parents[1]
KOTLIN = ROOT / "android/app/src/main/java/ru/magicsqd/mobile/usb"


def _fake_signer_tree(tmp_path: Path):
    java = tmp_path / ("tools/jre_minimal/bin/java.exe" if apk_signer.sys.platform == "win32" else "tools_mac/jre_minimal/bin/java")
    jar = tmp_path / ("tools/apksigner.jar" if apk_signer.sys.platform == "win32" else "tools_mac/apksigner.jar")
    java.parent.mkdir(parents=True, exist_ok=True)
    jar.parent.mkdir(parents=True, exist_ok=True)
    java.write_bytes(b"")
    jar.write_bytes(b"")
    cert = tmp_path / "cert"
    cert.mkdir()
    (cert / "private.pk8").write_bytes(b"k")
    (cert / "certificate.crt").write_bytes(b"c")
    apk = tmp_path / "demo.apk"
    apk.write_bytes(b"apk")
    return apk, cert, tmp_path / "out.apk"


def test_apksigner_uses_180s_default_and_client_config(tmp_path, monkeypatch):
    apk, cert, out = _fake_signer_tree(tmp_path)
    monkeypatch.setattr(apk_signer, "bundled_tools_root", lambda base: base)
    seen = []

    def fake_run(cmd, **kw):
        seen.append(kw["timeout"])
        out.write_bytes(b"signed")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    cfg = tmp_path / "client_config.json"
    client_config.configure(cfg, "1.1.27", "pc")
    apk_signer.resign_apk(tmp_path, apk, cert, out)
    assert seen[-1] == 180

    cfg.write_text(json.dumps({"schema": 1, "settings": {"apksigner_timeout_sec": 240}}), encoding="utf-8")
    out.unlink()
    apk_signer.resign_apk(tmp_path, apk, cert, out)
    assert seen[-1] == 240


def test_apksigner_timeout_raises_friendly_apk_sign_error(tmp_path, monkeypatch):
    apk, cert, out = _fake_signer_tree(tmp_path)
    monkeypatch.setattr(apk_signer, "bundled_tools_root", lambda base: base)

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kw["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(apk_signer.ApkSignError, match="не успел переподписать demo.apk за 90 с"):
        apk_signer.resign_apk(tmp_path, apk, cert, out, timeout=90)


def _ctx_with_fake_adb(tmp_path: Path, install_fn=None, shell_fn=None):
    logs = []
    adb = SimpleNamespace(
        adb_path="adb",
        install=install_fn or (lambda *a, **k: SimpleNamespace(returncode=0, stdout="Success\n", stderr="")),
        shell=shell_fn or (lambda *a, **k: SimpleNamespace(returncode=0, stdout="Success\n", stderr="")),
        push=lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
        run=lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    ctx = InstallContext("adb", "serial1", tmp_path, [], logs.append, threading.Event())
    ctx._adb = adb
    return ctx, logs


def test_install_apk_retries_with_user_current_on_geely_error(tmp_path):
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"1234")
    calls = []

    def fake_install(path, reinstall=True, extra_args=None, timeout=None):
        calls.append(extra_args)
        if not extra_args:
            raise AdbError("java.lang.IllegalArgumentException: geely install must have --user, for example --user current")
        return SimpleNamespace(returncode=0, stdout="Success\n", stderr="")

    ctx, logs = _ctx_with_fake_adb(tmp_path, install_fn=fake_install)
    ctx.install_apk(apk)
    assert calls == [None, ["--user", "current"]]
    assert any("--user current" in line for line in logs)


def test_install_apk_pm_and_stream_and_spoofed_retry_with_user_current(tmp_path):
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"1234")
    cmds = []

    def fake_shell(command, **kw):
        cmds.append(command)
        if "--user current" not in command:
            return SimpleNamespace(returncode=1, stdout="", stderr="geely install must have --user")
        return SimpleNamespace(returncode=0, stdout="Success\n", stderr="")

    ctx, _ = _ctx_with_fake_adb(tmp_path, shell_fn=fake_shell)
    ctx.install_apk_pm(apk)
    ctx.install_apk_stream(apk)
    ctx.install_apk_pm_spoofed(apk)
    assert any(c.startswith("pm install --user current -r ") for c in cmds)
    assert any("pm install --user current -S " in c for c in cmds)
    assert any(c.startswith("pm install --user current -i com.android.packageinstaller ") for c in cmds)


def test_android_kotlin_1124_invariants():
    flash = (KOTLIN / "UsbFlashSession.kt").read_text(encoding="utf-8")
    assert "synchronized(connectLock)" in flash
    assert "connectGeneration" in flash
    assert "usbManager?.hasPermission(target.usbDevice) != true" in flash

    spike = (KOTLIN / "UsbFlashSpike.kt").read_text(encoding="utf-8")
    assert "settled.compareAndSet(false, true)" in spike
    assert "65_000L" in spike

    engine = (KOTLIN / "InstallEngine.kt").read_text(encoding="utf-8")
    assert "jdwpPatchedCurrentApk" in engine
    assert "viaJdwpSideEffect" in engine

    adb_inst = (KOTLIN / "AdbInstall.kt").read_text(encoding="utf-8")
    assert "internal fun needsUserFlag(" in adb_inst
    assert adb_inst.count("pm install --user current") >= 3
