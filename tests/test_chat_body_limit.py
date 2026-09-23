"""ИИ-чат: тело запроса к серверу не превышает лимит даже за долгую сессию.

Лог #675 (Android 1.0.33): «Слишком большой запрос к чату (401 КБ, максимум 256 КБ)» — клиент
резал каждый вывод команды (6000 симв.) и строку лога (500), но 16 реплик + 40 строк лога
по-русски, да ещё кириллица как \\uXXXX (6 байт на букву), в сумме выходили за лимит, и чат
отвечал ошибкой на каждое сообщение. Проверяем обе копии: ПК (app/chat_client.py) и Android
(android/app/src/main/python/chat_bridge.py)."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path

import pytest

from app import chat_client

ANDROID_CHAT = Path(__file__).resolve().parents[1] / "android/app/src/main/python/chat_bridge.py"
SERVER_LIMIT = 262144  # server/backend.py: CHAT_MAX_BODY_BYTES


def _android_module():
    spec = importlib.util.spec_from_file_location("android_chat_bridge", ANDROID_CHAT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=["desktop", "android"])
def build(request):
    module = chat_client if request.param == "desktop" else _android_module()
    return module.build_chat_body


def _long_session():
    history = []
    for i in range(8):
        history.append({"role": "user", "content": f"Вопрос {i}: почему не ставится приложение?"})
        history.append({"role": "tool_result", "command": f"dumpsys package {i}",
                        "output": "Пакет установлен с ошибкой. " * 215, "ok": False})  # ~6000 симв. кириллицы
    history.append({"role": "user", "content": "Последний вопрос техника"})
    recent_log = ["Этап завершился с ошибкой: не удалось установить приложение " * 8 for _ in range(40)]
    return history, recent_log


def test_long_session_fits_and_keeps_the_last_turn(build):
    history, recent_log = _long_session()
    raw = json.dumps({"history": history, "recent_log": recent_log}).encode("utf-8")
    assert len(raw) > SERVER_LIMIT  # как в логе #675 — старый способ не влезал

    body = build(history, recent_log, {"client_id": "", "provider": None})
    assert len(body) <= 200 * 1024
    payload = json.loads(body.decode("utf-8"))
    assert payload["history"][-1] == {"role": "user", "content": "Последний вопрос техника"}
    assert payload["history"] == history[-len(payload["history"]):]  # выпали только самые старые
    assert payload["recent_log"] == recent_log  # лог не тронут, пока хватает места за счёт истории
    assert payload["client_id"] == "" and payload["provider"] is None
    assert "Последний".encode("utf-8") in body  # кириллица в UTF-8, не \uXXXX


def test_small_request_is_sent_as_is(build):
    history = [{"role": "user", "content": "Привет"}, {"role": "assistant", "content": "Здравствуйте"}]
    body = build(history, ["строка лога"], {"client_id": "abc", "provider": "qwen"})
    assert json.loads(body) == {"history": history, "recent_log": ["строка лога"],
                                "client_id": "abc", "provider": "qwen"}


def test_log_lines_go_after_history_and_huge_last_turn_is_clipped(build):
    history = [{"role": "user", "content": "Я" * 300000}]
    recent_log = ["строка"] * 60
    body = build(history, recent_log, {"client_id": "", "provider": None})
    payload = json.loads(body)
    assert len(body) <= 200 * 1024
    content = payload["history"][0]["content"]
    assert content.startswith("ЯЯЯ") and content.endswith("ЯЯЯ") and "[обрезано" in content


def test_broken_surrogate_from_js_does_not_crash(build):
    # clipChatText режет строку по UTF-16 — может остаться половинка эмодзи
    history = [{"role": "user", "content": "ок \ud83d"}]
    body = build(history, [], {"client_id": "", "provider": None})
    assert json.loads(body)["history"][0]["content"].startswith("ок ")


def test_desktop_send_uses_the_limited_body(monkeypatch):
    sent = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"ok": True, "type": "text", "content": "ответ"}).encode("utf-8")

    def fake_urlopen(request, timeout):
        sent["body"] = request.data
        return _Response()

    monkeypatch.setattr(chat_client.urllib.request, "urlopen", fake_urlopen)
    config = type("Config", (), {"submit_key": "k", "chat_url": "https://example.invalid/chat"})()
    history, recent_log = _long_session()
    reply = chat_client.send_chat_turn(history, recent_log, config)
    assert reply["content"] == "ответ"
    assert len(sent["body"]) <= 200 * 1024


def test_android_send_uses_the_limited_body(monkeypatch):
    module = _android_module()
    sent = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"ok": True, "type": "text", "content": "ответ"}).encode("utf-8")

    def fake_urlopen(request, timeout):
        sent["body"] = request.data
        return _Response()

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)
    history, recent_log = _long_session()
    reply = json.loads(module.send_chat_turn(json.dumps(history), json.dumps(recent_log),
                                             "https://example.invalid/chat", "k"))
    assert reply["content"] == "ответ"
    assert len(sent["body"]) <= 200 * 1024
