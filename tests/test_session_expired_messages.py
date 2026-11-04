"""Вход в аккаунт истёк (сервер на /auth/me отвечает 401 «нет активной сессии») — это не «нет связи с сервером».
04.11.2026: техник с подпиской на планшете (Jolion до 2026) жал «Обновить каталог APK» и видел «Нет связи с сервером —
каталог можно скачать, пока есть интернет.», хотя интернет был; помог перевход. Теперь обе платформы прямо говорят
«Вход в аккаунт истёк — войдите заново», а Android ещё и выходит из аккаунта сам, как ПК."""
from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
import urllib.error
from pathlib import Path

from app.web.api import offline_api
from app.web.api.auth_api import AuthApi
from app.web.api.offline_api import OfflineApi

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"
WEB_BRIDGE = ROOT / "android/app/src/main/java/ru/magicsqd/mobile/WebBridge.kt"


class _Auth:
    def __init__(self, result):
        self.result = result

    def refresh_subscriber(self):
        return self.result


def test_desktop_expired_sign_in_says_sign_in_again(app_base):
    expired = OfflineApi(app_base, None, _Auth({"ok": False, "expired": True})).apk_catalog_download()
    assert expired == {"ok": False, "locked": True, "error": offline_api.SESSION_EXPIRED_CATALOG}
    assert "войдите в аккаунт заново" in offline_api.SESSION_EXPIRED_CATALOG
    offline = OfflineApi(app_base, None, _Auth({"ok": False, "expired": False})).apk_catalog_download()
    assert offline["error"] == offline_api.NOT_SIGNED_IN


def test_desktop_refresh_subscriber_keeps_expired_flag(monkeypatch):
    api = AuthApi.__new__(AuthApi)
    monkeypatch.setattr(AuthApi, "refresh", lambda self: {"ok": False, "expired": True, "email": "x@y"})
    assert api.refresh_subscriber() == {"ok": False, "expired": True}
    monkeypatch.setattr(AuthApi, "refresh", lambda self: {"ok": False, "offline": True})
    assert api.refresh_subscriber() == {"ok": False, "expired": False}


def test_android_auth_bridge_reports_http_status(monkeypatch):
    monkeypatch.syspath_prepend(str(ANDROID_PY))
    spec = importlib.util.spec_from_file_location("auth_bridge_status", ANDROID_PY / "auth_bridge.py")
    auth_bridge = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "auth_bridge_status", auth_bridge)
    spec.loader.exec_module(auth_bridge)

    def refuse(request, timeout):
        body = io.BytesIO(json.dumps({"ok": False, "error": "нет активной сессии"}).encode())
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, body)

    monkeypatch.setattr(auth_bridge.urllib.request, "urlopen", refuse)
    assert json.loads(auth_bridge.me("https://x", "msqd_user=t")) == {"ok": False, "error": "нет активной сессии",
                                                                       "status": 401}


def _code() -> str:
    text = WEB_BRIDGE.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(line.split("//")[0] for line in text.splitlines())


def test_android_expired_sign_in_logs_out_and_says_so():
    code = _code()
    expired = re.search(r"private fun sessionExpired\(.*?\n    }\n", code, flags=re.S).group(0)
    assert 'me.optInt("status") != 401' in expired and "clearAuthSession()" in expired
    assert '"auth_logout_result"' in expired  # окно аккаунта сразу показывает «Войти»
    refusal = re.search(r"private fun subscriptionRefusal\(.*?\n    }\n", code, flags=re.S).group(0)
    assert refusal.index("sessionExpired(me)") < refusal.index('me == null || !me.optBoolean("ok")')
    assert "SESSION_EXPIRED_CATALOG) ?: run {" in code
    assert ".put(\"error\", SESSION_EXPIRED_OFFLINE)" in code
    assert "if (sessionExpired(obj)) return@Thread" in code  # и при запуске — проверка подписки
    # тексты одинаковые с ПК
    for name in ("SESSION_EXPIRED_CATALOG", "SESSION_EXPIRED_OFFLINE"):
        kotlin = re.search(name + r' =\s*"([^"]+)"', code).group(1)
        assert kotlin == getattr(offline_api, name)
