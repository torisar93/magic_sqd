"""ПК: отпечаток магнитолы (ro.product.name/model/device) для журнала сессии — тем же текстом, что пишет Android. По нему
сервер учится узнавать модель по магнитоле (подсказка «похоже, это другая машина», 30.09). JS-сторона —
tests/js/desktop_device_fingerprint.test.js."""
from __future__ import annotations
import types

from app.web.api import install_api
from app.web.api.install_api import InstallApi


def _api(monkeypatch, stdout=None, error=None):
    calls = []

    class FakeAdb:
        def __init__(self, adb_path, serial):
            calls.append(serial)

        def shell(self, command, check=True, timeout=120):
            calls.append(command)
            if error:
                raise error
            return types.SimpleNamespace(stdout=stdout, stderr="")

    monkeypatch.setattr(install_api, "Adb", FakeAdb)
    return InstallApi("adb", None, types.SimpleNamespace(get_model=lambda key: None)), calls


def test_fingerprint_reads_three_props(monkeypatch):
    api, calls = _api(monkeypatch, "geely_fx11_j1\r\nFX11_J1\r\nmsmnile_gvmq\r\n")
    assert api.device_fingerprint("ABC123") == {"name": "geely_fx11_j1", "model": "FX11_J1", "device": "msmnile_gvmq"}
    assert calls == ["ABC123", "getprop ro.product.name; getprop ro.product.model; getprop ro.product.device"]


def test_model_with_spaces_is_kept_as_android_writes_it(monkeypatch):
    api, _ = _api(monkeypatch, "x9h_a01g\nx9 for arm64\nx9h_a01g\n")
    assert api.device_fingerprint("s")["model"] == "x9 for arm64"  # как в баннере Android, не «x9_for_arm64» из devices -l


def test_no_answer_means_no_fingerprint(monkeypatch):
    assert _api(monkeypatch, "")[0].device_fingerprint("s") == {}
    assert _api(monkeypatch, error=RuntimeError("device offline"))[0].device_fingerprint("s") == {}
