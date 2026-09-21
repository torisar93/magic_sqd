"""Тесты app/pending_install_logs.py — прочный локальный журнал сессии
установки (переживает обрыв сети и вылет процесса, см. докстринг модуля).
Никакой сети здесь: send_queue/send_one принимают поддельный InstallLogApi
(types.SimpleNamespace), как и в tests/test_prefetch_apks.py."""
from __future__ import annotations
import json
import types

from app import pending_install_logs as pil


def fake_api(ok: bool):
    calls = []

    def send(platform, brand, model, modification, success, log_text, email):
        calls.append({"platform": platform, "brand": brand, "model": model,
                      "modification": modification, "success": success,
                      "log_text": log_text, "email": email})
        return {"ok": ok}

    return types.SimpleNamespace(send=send), calls


def test_append_and_finalize_round_trip(tmp_path):
    token = pil.start_session(tmp_path, "Geely", "Atlas New", "Monji")
    pil.append_current(tmp_path, token, "первая строка", False)
    pil.append_current(tmp_path, token, "Установлено: X.apk", True)
    path = pil.finalize_to_queue(tmp_path, token, "windows", "Geely", "Atlas New", "Monji", True)
    assert path is not None and path.exists()
    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry["log_text"] == "первая строка\nУстановлено: X.apk\n"
    assert entry["success"] is True
    assert entry["brand"] == "Geely"
    # _current.* убраны — сессия запечатана
    assert not (tmp_path / "pending_install_logs" / "_current.log").exists()


def test_finalize_uses_given_log_text_not_disk(tmp_path):
    token = pil.start_session(tmp_path, "Geely", "Atlas New", "Monji")
    pil.append_current(tmp_path, token, "то, что на диске", True)
    path = pil.finalize_to_queue(tmp_path, token, "windows", "Geely", "Atlas New", "Monji", True,
                                  log_text="полный лог из JS")
    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry["log_text"] == "полный лог из JS"


def test_stale_token_append_is_noop(tmp_path):
    token = pil.start_session(tmp_path, "Geely", "Atlas New", "Monji")
    pil.append_current(tmp_path, "чужой-токен", "не должно попасть", True)
    current_log = tmp_path / "pending_install_logs" / "_current.log"
    assert current_log.read_text(encoding="utf-8") == ""
    # свежий токен по-прежнему работает
    pil.append_current(tmp_path, token, "должно попасть", True)
    assert "должно попасть" in current_log.read_text(encoding="utf-8")


def test_stale_token_finalize_does_not_touch_newer_session(tmp_path):
    old_token = pil.start_session(tmp_path, "Geely", "Preface", "Обычная")
    pil.append_current(tmp_path, old_token, "старая сессия", True)
    new_token = pil.start_session(tmp_path, "Geely", "Atlas New", "Monji")
    pil.append_current(tmp_path, new_token, "новая сессия", True)
    # Финализация с УСТАРЕВШИМ токеном (пришла с опозданием из-за гонки
    # потоков pywebview, см. докстринг модуля) не должна ни записать что-то
    # в очередь, ни стереть файлы новой сессии.
    path = pil.finalize_to_queue(tmp_path, old_token, "windows", "Geely", "Preface", "Обычная", False)
    assert path is None
    assert not pil.list_queue(tmp_path)
    current_log = tmp_path / "pending_install_logs" / "_current.log"
    assert "новая сессия" in current_log.read_text(encoding="utf-8")


def test_discard_current_removes_without_queueing(tmp_path):
    token = pil.start_session(tmp_path, "Geely", "Atlas New", "Monji")
    pil.append_current(tmp_path, token, "просто открыл модель", False)
    pil.discard_current(tmp_path)
    assert not (tmp_path / "pending_install_logs" / "_current.log").exists()
    assert not pil.list_queue(tmp_path)


def test_recover_stale_current_with_activity_queues_as_crash(tmp_path):
    token = pil.start_session(tmp_path, "Haval", "Jolion", "2026")
    pil.append_current(tmp_path, token, "Установка APK: FloatingDock.apk", True)
    # _current.* остался НЕ запечатанным — как после вылета процесса.
    pil.recover_stale_current(tmp_path, "macos")
    queue = pil.list_queue(tmp_path)
    assert len(queue) == 1
    entry = json.loads(queue[0].read_text(encoding="utf-8"))
    assert entry["success"] is False
    assert entry["log_text"].startswith(pil.STALE_MARKER)
    assert "Установка APK: FloatingDock.apk" in entry["log_text"]
    assert not (tmp_path / "pending_install_logs" / "_current.log").exists()


def test_recover_stale_current_without_activity_discards(tmp_path):
    pil.start_session(tmp_path, "Geely", "Atlas New", "Monji")
    # Только открыли модель, реальной активности не было (append_current с
    # has_activity=False ниже, как при просмотре инструкции).
    token_path = tmp_path / "pending_install_logs" / "_current.meta.json"
    assert token_path.exists()
    pil.recover_stale_current(tmp_path, "windows")
    assert not pil.list_queue(tmp_path)
    assert not token_path.exists()


def test_recover_stale_current_with_no_session_at_all(tmp_path):
    # Программа никогда не открывала модель в прошлом запуске — просто не
    # должно падать.
    pil.recover_stale_current(tmp_path, "windows")
    assert not pil.list_queue(tmp_path)


def test_send_one_deletes_on_success(tmp_path):
    token = pil.start_session(tmp_path, "Geely", "Atlas New", "Monji")
    pil.append_current(tmp_path, token, "строка", True)
    path = pil.finalize_to_queue(tmp_path, token, "windows", "Geely", "Atlas New", "Monji", True)
    api, calls = fake_api(ok=True)
    pil.send_one(tmp_path, path, api, "tech@example.com")
    assert not path.exists()
    assert calls[0]["brand"] == "Geely" and calls[0]["email"] == "tech@example.com"


def test_send_one_keeps_file_on_failure(tmp_path):
    token = pil.start_session(tmp_path, "Geely", "Atlas New", "Monji")
    pil.append_current(tmp_path, token, "строка", True)
    path = pil.finalize_to_queue(tmp_path, token, "windows", "Geely", "Atlas New", "Monji", True)
    api, _ = fake_api(ok=False)
    pil.send_one(tmp_path, path, api, None)
    assert path.exists()  # остаётся для следующей попытки


def test_send_one_with_none_path_is_noop(tmp_path):
    api, calls = fake_api(ok=True)
    pil.send_one(tmp_path, None, api, None)
    assert calls == []


def test_send_queue_drains_multiple_entries_independently(tmp_path):
    for i in range(3):
        token = pil.start_session(tmp_path, "Geely", f"Model{i}", "")
        pil.append_current(tmp_path, token, f"строка {i}", True)
        pil.finalize_to_queue(tmp_path, token, "windows", "Geely", f"Model{i}", "", True)
    assert len(pil.list_queue(tmp_path)) == 3
    api, calls = fake_api(ok=True)
    pil.send_queue(tmp_path, api, None)
    assert not pil.list_queue(tmp_path)
    assert len(calls) == 3


def test_send_queue_skips_corrupt_entry_without_crashing(tmp_path):
    token = pil.start_session(tmp_path, "Geely", "Atlas New", "Monji")
    pil.append_current(tmp_path, token, "строка", True)
    pil.finalize_to_queue(tmp_path, token, "windows", "Geely", "Atlas New", "Monji", True)
    # Битый файл рядом (не должен возникать благодаря .part+rename, но
    # send_queue не должен падать, если такое всё же случится).
    queue_dir = tmp_path / "pending_install_logs" / "queue"
    (queue_dir / "corrupt.json").write_text("{не json", encoding="utf-8")
    api, calls = fake_api(ok=True)
    logged = []
    pil.send_queue(tmp_path, api, None, log=logged.append)
    assert len(calls) == 1  # валидная запись всё равно отправлена
    assert (queue_dir / "corrupt.json").exists()  # битая осталась, но не уронила проход
    assert any("corrupt.json" in message for message in logged)


def test_list_queue_empty_when_no_queue_dir(tmp_path):
    assert pil.list_queue(tmp_path) == []
