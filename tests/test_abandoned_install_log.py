"""pending_install_logs.seal_abandoned_session — что уходит на сервер, когда
программу закрыли посреди сессии, которую JS не успела отправить сама (см.
app/web/bridge.py: seal_abandoned_install_log — тонкая обёртка над ней, +
send_one в flush_abandoned_install_log). Сам bridge.py здесь не импортируется:
он тянет pywebview (app/web/api/car_editor_api.py), которого нет в
requirements-dev.txt.

Реальные случаи (install_logs, 2026-09): #471 (Windows, QR ADB — строка
логировалась только из JS) и #589 (Windows, отказ по дубликату пакета до
первой бэкендовой строки) — оба закрыты штатно, но бэкендовый буфер был
пуст, flush молча выходил, а следующий запуск слал их как вылет."""
from __future__ import annotations
import json
import types

from app import pending_install_logs as pil


def fake_install_log_api():
    calls = []

    def send(platform, brand, model, modification, success, log_text, email):
        calls.append({"platform": platform, "brand": brand, "model": model, "success": success,
                      "log_text": log_text, "email": email})
        return {"ok": True}

    return types.SimpleNamespace(send=send), calls


def flush(base_dir, platform, pending):
    """То же, что WebApi.flush_abandoned_install_log: запечатать + одна попытка отправки."""
    api, calls = fake_install_log_api()
    path = pil.seal_abandoned_session(base_dir, platform, pending)
    pil.send_one(base_dir, path, api, None)
    return calls


def test_js_only_activity_is_sent_at_close_not_left_for_crash_recovery(tmp_path):
    token = pil.start_session(tmp_path, "Haval", "Jolion", "2026")
    pil.append_current(tmp_path, token, "Magic SQD v1.0.30 (x64) · client=", False)
    pil.append_current(tmp_path, token, "QR ADB: файл svlog.flag записан на E:.", True)

    calls = flush(tmp_path, "windows", pending=None)  # бэкенд ничего не логировал

    assert len(calls) == 1
    assert "QR ADB: файл svlog.flag записан" in calls[0]["log_text"]
    assert "Magic SQD v1.0.30" in calls[0]["log_text"]  # и строка версии из JS не теряется
    assert pil.STALE_MARKER not in calls[0]["log_text"]
    assert calls[0]["success"] is False
    # До фикса здесь оставался _current.* → следующий запуск слал «вылет».
    pil.recover_stale_current(tmp_path, "windows")
    assert not pil.list_queue(tmp_path)


def test_backend_lines_missing_on_disk_are_appended(tmp_path):
    token = pil.start_session(tmp_path, "Haval", "M6", "до 04.2026")
    pil.append_current(tmp_path, token, "строка с диска", True)
    pending = {"brand": "Haval", "model": "M6", "modification": "до 04.2026",
               "log_text": "Установка APK: EdgeScreenS9Pro.apk", "token": token}

    calls = flush(tmp_path, "macos", pending)

    assert [c["log_text"] for c in calls] == ["строка с диска\nУстановка APK: EdgeScreenS9Pro.apk"]
    assert not (tmp_path / "pending_install_logs" / "_current.log").exists()


def test_backend_buffer_with_stale_token_falls_back_to_disk_journal(tmp_path):
    token = pil.start_session(tmp_path, "Geely", "Preface", "Обычная")
    pil.append_current(tmp_path, token, "Этап завершился с ошибкой: ...", True)
    pending = {"brand": "Geely", "model": "Preface", "modification": "Обычная",
               "log_text": "бэкенд", "token": "устаревший-токен"}

    calls = flush(tmp_path, "windows", pending)

    assert len(calls) == 1
    assert "Этап завершился с ошибкой" in calls[0]["log_text"]
    assert not (tmp_path / "pending_install_logs" / "_current.log").exists()


def test_seal_without_send_leaves_entry_for_next_launch(tmp_path):
    # macOS, Cmd+Q: хук NSApplicationWillTerminate только запечатывает (без
    # сети — главный поток Cocoa), отправит send_queue при следующем запуске.
    token = pil.start_session(tmp_path, "Haval", "Jolion", "2026")
    pil.append_current(tmp_path, token, "Установлено: WiFi+Manager.apk", True)

    path = pil.seal_abandoned_session(tmp_path, "macos", None)

    entry = json.loads(path.read_text(encoding="utf-8"))
    assert entry["platform"] == "macos" and pil.STALE_MARKER not in entry["log_text"]
    pil.recover_stale_current(tmp_path, "macos")  # следующий запуск
    assert pil.list_queue(tmp_path) == [path]  # ровно одна запись, без «вылета»


def test_second_seal_does_not_duplicate(tmp_path):
    token = pil.start_session(tmp_path, "Haval", "Jolion", "2026")
    pil.append_current(tmp_path, token, "Установлено: WiFi+Manager.apk", True)
    pending = {"brand": "Haval", "model": "Jolion", "modification": "2026",
               "log_text": "Установлено: WiFi+Manager.apk", "token": token}

    first = pil.seal_abandoned_session(tmp_path, "macos", pending)
    second = pil.seal_abandoned_session(tmp_path, "macos", pending)

    assert first is not None and second is None
    assert pil.list_queue(tmp_path) == [first]


def test_nothing_to_send_without_session(tmp_path):
    assert flush(tmp_path, "windows", pending=None) == []


def test_full_disk_journal_preferred_over_backend_buffer(tmp_path):
    # Разбор логов 2026-09-26: при закрытии окна уходил бэкендовый буфер — без строки версии и без
    # строк интерфейса (81 лог с ПК, например №1144, №1163). Журнал той же сессии на диске полнее.
    token = pil.start_session(tmp_path, "Haval", "Jolion", "2026")
    for line, activity in [("Magic SQD v1.0.42 (x64) · client=", False),
                           ("Флешка: записано — svlog.flag.", True),
                           ("Установка APK (JDWP-патч белого списка, Desay x9h): WiFi.apk", True),
                           ("Установка завершена успешно.", True)]:
        pil.append_current(tmp_path, token, line, activity)
    pending = {"brand": "Haval", "model": "Jolion", "modification": "2026", "token": token,
               "log_text": "Установка APK (JDWP-патч белого списка, Desay x9h): WiFi.apk\nУстановка завершена успешно."}

    calls = flush(tmp_path, "windows", pending)

    assert [c["log_text"] for c in calls] == [
        "Magic SQD v1.0.42 (x64) · client=\nФлешка: записано — svlog.flag.\n"
        "Установка APK (JDWP-патч белого списка, Desay x9h): WiFi.apk\nУстановка завершена успешно."]
    assert not (tmp_path / "pending_install_logs" / "_current.log").exists()


def test_session_sealed_before_adb_kill_server_on_close():
    # kill-server держит закрытие до 15 с; если программу добьют в это время (выключение Windows, повторный
    # запуск), незапечатанная сессия уходила следующим запуском как вылет (Windows 1.0.41: №1040, №1115, №1230).
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for name in ("main_web.py",):
        source = (root / name).read_text(encoding="utf-8")
        finally_block = source[source.index('_log_step("webview.start() returned (normal close)")'):]
        assert finally_block.index("api.seal_abandoned_install_log()") < finally_block.index("kill_server(api.adb_path)"), name


def test_journal_preferred_even_without_early_download_lines(tmp_path):
    # Докачка при открытии модели (_on_log_passive) идёт раньше, чем интерфейс узнаёт токен сессии, —
    # в журнале на диске её нет. Сверяем только строки действий, иначе журнал не выбирался бы никогда.
    token = pil.start_session(tmp_path, "Haval", "Jolion", "2026")
    pil.append_current(tmp_path, token, "Magic SQD v1.0.42 (x64) · client=", False)
    pil.append_current(tmp_path, token, "Установлено: WiFi.apk", True)
    pending = {"brand": "Haval", "model": "Jolion", "modification": "2026", "token": token,
               "log_text": "Скачано файлов (cars/Haval/Jolion/2026/files/instruction_1): 3.\nУстановлено: WiFi.apk",
               "active_text": "Установлено: WiFi.apk"}

    calls = flush(tmp_path, "windows", pending)

    assert [c["log_text"] for c in calls] == ["Magic SQD v1.0.42 (x64) · client=\nУстановлено: WiFi.apk"]


def test_actions_missing_on_disk_are_appended_to_journal(tmp_path):
    # Окно закрыли сразу после установки — последнее событие не дошло до интерфейса (лог #1327, 1.0.43):
    # журнал с версией программы остаётся, недостающее действие дописывается в конец.
    token = pil.start_session(tmp_path, "Haval", "Jolion", "2026")
    pil.append_current(tmp_path, token, "Magic SQD v1.0.42 (x64) · client=", False)
    pil.append_current(tmp_path, token, "Установлено: WiFi.apk", True)
    pending = {"brand": "Haval", "model": "Jolion", "modification": "2026", "token": token,
               "log_text": "Установлено: WiFi.apk\nУстановлено: Dock.apk",
               "active_text": "Установлено: WiFi.apk\nУстановлено: Dock.apk"}

    calls = flush(tmp_path, "windows", pending)

    assert [c["log_text"] for c in calls] == [
        "Magic SQD v1.0.42 (x64) · client=\nУстановлено: WiFi.apk\nУстановлено: Dock.apk"]


def test_install_api_marks_download_lines_as_not_actions(tmp_path, monkeypatch):
    from app.web.api import install_api as ia
    monkeypatch.setattr(ia.event_bridge, "push", lambda event: None)
    api = ia.InstallApi("adb", tmp_path, types.SimpleNamespace(get_model=lambda key: None))
    api._session_meta = {"brand": "Haval", "model": "Jolion", "modification": "2026"}
    api._on_log_passive("Скачано файлов (cars/Haval/Jolion/2026/files/instruction_1): 3.")
    api._on_log("Установлено: WiFi.apk")

    pending = api.pending_session_log()

    assert pending["log_text"] == "Скачано файлов (cars/Haval/Jolion/2026/files/instruction_1): 3.\nУстановлено: WiFi.apk"
    assert pending["active_text"] == "Установлено: WiFi.apk"
