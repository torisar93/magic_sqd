"""ПК: таймаут заливки и установки — по размеру файла (разбор 09.11.2026: VK Video 167 МБ по Wi-Fi не укладывался в
180 с на любом способе, №7962/7965/12745)."""
from __future__ import annotations

from types import SimpleNamespace

from app import install_context
from app.install_context import size_timeout


def test_small_files_keep_180s_and_big_ones_get_more(tmp_path):
    small = tmp_path / "small.apk"
    small.write_bytes(b"x" * 1024)
    big = tmp_path / "big.apk"
    with open(big, "wb") as f:
        f.truncate(167 * 1_048_576)
    assert size_timeout(small) == 180
    assert size_timeout(big) == 120 + 2 * 167
    assert size_timeout(tmp_path / "missing.apk") == 180


def test_install_and_push_use_it_unless_caller_sets_timeout(tmp_path):
    big = tmp_path / "big.apk"
    with open(big, "wb") as f:
        f.truncate(200 * 1_048_576)
    calls = []
    adb = SimpleNamespace(install=lambda path, **kw: calls.append(("install", kw["timeout"])) or SimpleNamespace(stdout="Success"),
                          push=lambda local, remote, timeout: calls.append(("push", timeout)))
    ctx = install_context.InstallContext.__new__(install_context.InstallContext)
    ctx._adb, ctx._prestaged, ctx.check_cancelled, ctx.log = adb, {}, lambda: None, lambda m: None
    ctx.install_apk(big)
    ctx.push(big, "/data/local/tmp/big.apk")
    ctx.push(big, "/system/app/x.apk", timeout=900)
    assert calls == [("install", 520), ("push", 520), ("push", 900)]
