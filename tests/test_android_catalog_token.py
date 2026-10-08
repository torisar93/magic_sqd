"""Закрытый каталог (включён 08.10.2026): все запросы Android к /content — через content_sync.open_url, с токеном
официальной сборки. Голый urlopen (таблица «магнитола → модель», список «Спасибо вам») и свой запрос Kotlin за
значками APK (WebBridge.serverApkIcon, manifest.json) шли без токена и получали 403."""
import importlib
import io
import json
import urllib.error

from conftest import ROOT

ANDROID_PY = ROOT / "android/app/src/main/python"
BASE = "https://magicsqd.ru/content"
ICONS = {"apk/Лаунчеры/a.apk": "icons/" + "a" * 64 + ".png"}
PAYLOADS = {
    "device_models.json": {"version": 1, "devices": {}},
    "supporters.json": {"people": [{"name": "Иван", "kind": "sub"}]},
    "manifest.json": {"files": {}, "apk_icons": ICONS},
}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Token:
    def header(self):
        return {"X-App-Token": "T"}


def _modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    return importlib.import_module("content_sync"), importlib.import_module("mobile_bridge")


def test_android_catalog_requests_carry_build_token(tmp_path, monkeypatch):
    content_sync, mobile_bridge = _modules(monkeypatch)
    seen = []

    def urlopen(request, timeout=None):
        seen.append(request)
        return _Resp(json.dumps(PAYLOADS[request.full_url.rsplit("/", 1)[1]], ensure_ascii=False).encode("utf-8"))

    monkeypatch.setattr(content_sync.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(content_sync, "_app_token", _Token())
    assert mobile_bridge.device_models_refresh(BASE, str(tmp_path / "device_models.json")) is True
    assert json.loads(mobile_bridge.supporters_fetch(BASE))["people"][0]["name"] == "Иван"
    assert json.loads(mobile_bridge.apk_icons(BASE)) == ICONS
    assert [r.full_url.rsplit("/", 1)[1] for r in seen] == ["device_models.json", "supporters.json", "manifest.json"]
    assert all(r.get_header("X-app-token") == "T" for r in seen)


def test_android_apk_icons_failure_is_empty_so_kotlin_retries(monkeypatch):
    content_sync, mobile_bridge = _modules(monkeypatch)

    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(content_sync.urllib.request, "urlopen", urlopen)
    assert mobile_bridge.apk_icons(BASE) == ""


def test_android_has_no_tokenless_catalog_requests():
    bridge = (ANDROID_PY / "mobile_bridge.py").read_text(encoding="utf-8")
    assert 'urlopen(f"{base_url}' not in bridge
    kotlin = (ROOT / "android/app/src/main/java/ru/magicsqd/mobile/WebBridge.kt").read_text(encoding="utf-8")
    assert 'callAttr("apk_icons", BASE_URL)' in kotlin
    assert 'URL("$BASE_URL/manifest.json")' not in kotlin
