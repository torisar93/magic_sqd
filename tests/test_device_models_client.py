"""Таблица «магнитола → модель» на стороне программ (подсказка «Похоже, это другая машина», 30.09): ПК —
app/device_models.py, Android — mobile_bridge.device_models_refresh + WebBridge «device_models»; решение и окно —
общий device_hint.js (tests/js/device_hint.test.js). Копия нужна заранее: при Wi-Fi ADB интернета обычно нет."""
from __future__ import annotations
import importlib
import io
import json
import re
import sys
from pathlib import Path

from app import device_models

ROOT = Path(__file__).resolve().parents[1]
TABLE = {"version": 1, "devices": {"x9h_a01g|x9 for arm64|x9h_a01g": [{"model": "Haval/Jolion/2026", "ok": 54, "phones": 13}]}}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _serve(monkeypatch, module, payload=None, error=None):
    # ПК ходит через content_sync.open_url (токен сборки) — urlopen получает объект Request; Android
    # (mobile_bridge) — голый urlopen со строкой. Берём адрес из того и другого.
    def urlopen(url, timeout=None):
        assert getattr(url, "full_url", url).endswith("/device_models.json")
        if error:
            raise error
        return _Resp(payload)
    target = getattr(module, "content_sync", module)
    monkeypatch.setattr(target.urllib.request, "urlopen", urlopen)


def test_desktop_keeps_a_copy_and_survives_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(device_models, "get_base_url", lambda base_dir: "https://magicsqd.ru/content")
    _serve(monkeypatch, device_models, json.dumps(TABLE).encode())
    assert device_models.refresh(tmp_path) is True and device_models.load(tmp_path) == TABLE
    _serve(monkeypatch, device_models, error=OSError("нет сети"))  # в сети магнитолы — копия остаётся
    assert device_models.refresh(tmp_path) is False and device_models.load(tmp_path) == TABLE
    _serve(monkeypatch, device_models, b"<html>502</html>")  # мусор вместо таблицы копию не портит
    assert device_models.refresh(tmp_path) is False and device_models.load(tmp_path) == TABLE


def test_desktop_without_copy_or_server():
    assert device_models.load(Path("/нет/такой/папки")) == {}


def test_android_bridge_downloads_the_same_table(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "android/app/src/main/python"))
    try:
        mobile_bridge = importlib.import_module("mobile_bridge")
    finally:
        sys.path.pop(0)
    cache = tmp_path / "device_models.json"
    _serve(monkeypatch, mobile_bridge, json.dumps(TABLE).encode())
    assert mobile_bridge.device_models_refresh("https://magicsqd.ru/content", str(cache)) is True
    assert json.loads(cache.read_text(encoding="utf-8")) == TABLE
    _serve(monkeypatch, mobile_bridge, b"{}")  # без devices — не таблица
    assert mobile_bridge.device_models_refresh("https://magicsqd.ru/content", str(cache)) is False
    assert json.loads(cache.read_text(encoding="utf-8")) == TABLE


def test_android_wiring():
    app_js = (ROOT / "android/app/src/main/assets/js/app.js").read_text(encoding="utf-8")
    assert "else if (banner) suggestModel(banner);" in app_js  # после успешного подключения (не в Wi-Fi-потоке установки)
    assert 'openModel(target.model, { keepConnections: true, banner });' in app_js
    assert re.search(r"if \(!options\.keepConnections\) \{\s+setAdbStatus\(false", app_js)  # та же магнитола — связь не рвём
    assert "if (options.banner) log(`ADB подключён: ${options.banner}`);" in app_js
    bridge = (ROOT / "android/app/src/main/java/ru/magicsqd/mobile/WebBridge.kt").read_text(encoding="utf-8")
    assert '"device_models" -> deviceModelsJson()' in bridge and "startDeviceModelsRefresh()" in bridge
    desktop = (ROOT / "app/web/api/sync_api.py").read_text(encoding="utf-8")
    assert "threading.Thread(target=device_models.refresh, args=(self.base_dir,), daemon=True).start()" in desktop
