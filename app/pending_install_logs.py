"""Прочный локальный журнал сессии установки — переживает и обрыв сети (лог
уходит на сервер при СЛЕДУЮЩЕМ запуске программы, см. send_queue), и вылет
процесса (то, что уже дописано в _current.log на диск, не теряется, см.
recover_stale_current). Использует app/web/api/install_log_api.py:InstallLogApi
для самой отправки — никакого нового сетевого кода здесь нет, это только
файловая бухгалтерия.

Точки входа из остального кода:
- app/web/api/install_api.py: load_stages() — start_session() на каждое
  открытие модели.
- app/web/frontend/js/screens/stage_wizard.js: log() — append_current() на
  каждую строку лога, которую видит техник (тем же путём идут и JS-only
  строки, и переданные из Python через событие install_log — см. докстринг
  append_current).
- app/web/bridge.py: install_log_send()/flush_abandoned_install_log() —
  finalize_to_queue() перед попыткой отправки; WebApi.__init__ — фоновым
  потоком recover_stale_current() + send_queue() при каждом старте программы.

Важно про pywebview (см. .venv/lib/python3.11/site-packages/webview/util.py:
js_bridge_call) — КАЖДЫЙ вызов window.pywebview.api.X(...) из JS диспетчерится
в СВОЙ отдельный поток без всякой сериализации между разными вызовами: Python
не гарантированно увидит их в том порядке, в котором их сделал JS (реальный
пример — app/web/frontend/js/screens/stage_wizard.js:open(), где flush старой
сессии, лог заголовка версии и запуск новой сессии уходят в три независимых
потока). Поэтому каждая сессия помечена случайным токеном (start_session
возвращает его, JS передаёт обратно на каждый append_current/finalize_to_queue
этой же сессии) — несовпадение токена значит "эта операция предназначалась уже
не той сессии, что сейчас активна" и тихо ничего не делает, вместо того чтобы
испортить файлы более новой (или более старой, ещё не отправленной) сессии."""
from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from pathlib import Path

_LOCK = threading.Lock()

_PENDING_DIR_NAME = "pending_install_logs"
_CURRENT_LOG_NAME = "_current.log"
_CURRENT_META_NAME = "_current.meta.json"
_CURRENT_ACTIVITY_NAME = "_current.activity"
_QUEUE_DIR_NAME = "queue"

STALE_MARKER = "=== Предыдущий запуск программы не завершился штатно ==="


def _pending_dir(base_dir) -> Path:
    return Path(base_dir) / _PENDING_DIR_NAME


def _current_log_path(base_dir) -> Path:
    return _pending_dir(base_dir) / _CURRENT_LOG_NAME


def _current_meta_path(base_dir) -> Path:
    return _pending_dir(base_dir) / _CURRENT_META_NAME


def _current_activity_path(base_dir) -> Path:
    return _pending_dir(base_dir) / _CURRENT_ACTIVITY_NAME


def _queue_dir(base_dir) -> Path:
    return _pending_dir(base_dir) / _QUEUE_DIR_NAME


def _clear_current(base_dir) -> None:
    for path in (_current_log_path(base_dir), _current_meta_path(base_dir), _current_activity_path(base_dir)):
        path.unlink(missing_ok=True)


def _read_current_meta(base_dir) -> dict | None:
    try:
        return json.loads(_current_meta_path(base_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def start_session(base_dir, brand: str, model: str, modification: str) -> str:
    """Новая сессия — свежий _current.* (прошлый, если был, просто убирается:
    если в нём была реальная активность, он уже должен был уйти в очередь
    через finalize_to_queue до этого момента — см. app.js/stage_wizard.js,
    которые флашат старую сессию ДО открытия новой модели; если нет —
    отбрасывать его и не нужно было). Возвращает токен для последующих вызовов
    этой же сессии (см. докстринг модуля)."""
    base_dir = Path(base_dir)
    token = secrets.token_hex(8)
    with _LOCK:
        _pending_dir(base_dir).mkdir(parents=True, exist_ok=True)
        _clear_current(base_dir)
        _current_meta_path(base_dir).write_text(
            json.dumps({"token": token, "brand": brand, "model": model, "modification": modification},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        _current_log_path(base_dir).write_text("", encoding="utf-8")
    return token


def append_current(base_dir, token: str, line: str, has_activity: bool) -> None:
    """Дозаписывает одну строку лога сессии — свежим open/close на каждый
    вызов, без держания файла открытым: гарантирует, что строка долетела до
    ОС раньше, чем вернётся управление вызывающему (защищает от вылета именно
    процесса Python — не от отключения питания, но ради этого модуль и
    существует)."""
    base_dir = Path(base_dir)
    with _LOCK:
        meta = _read_current_meta(base_dir)
        if not meta or meta.get("token") != token:
            return  # не та сессия — см. докстринг модуля про гонку потоков pywebview
        try:
            with open(_current_log_path(base_dir), "a", encoding="utf-8") as f:
                f.write(str(line) + "\n")
            if has_activity:
                _current_activity_path(base_dir).touch(exist_ok=True)
        except OSError:
            pass


def discard_current(base_dir) -> None:
    """Сессия без реальной активности — как и сегодня, слать нечего, просто
    убираем _current.* без создания записи в очереди. Не вызывается из
    остального кода на каждую "тихую" сессию отдельно — тот же эффект даёт
    следующий start_session() (в рамках одного запуска) или recover_stale_current()
    (после перезапуска, см. проверку activity-маркера там); эта функция —
    явный, самостоятельный путь для того же самого, если он понадобится."""
    with _LOCK:
        _clear_current(Path(base_dir))


def _write_queue_entry(base_dir, platform: str, brand: str, model: str, modification: str,
                        success: bool, log_text: str) -> Path:
    queue_dir = _queue_dir(base_dir)
    queue_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "platform": platform, "brand": brand, "model": model, "modification": modification,
        "success": bool(success), "log_text": log_text,
    }
    name = f"{time.time():.6f}-{uuid.uuid4().hex}.json"
    # Через .part + атомное переименование (тот же приём, что content_sync.py:
    # download_file/_replace_with_retry) — недописанный queue-файл никогда не
    # виден send_queue/list_queue, даже если процесс упадёт ровно посреди записи.
    tmp_path = queue_dir / (name + ".part")
    final_path = queue_dir / name
    tmp_path.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    tmp_path.replace(final_path)
    return final_path


def finalize_to_queue(base_dir, token: str, platform: str, brand: str, model: str, modification: str,
                       success: bool, log_text: str | None = None) -> Path | None:
    """Запечатывает текущую сессию в queue/<файл>.json и очищает _current.* —
    вызывается ПЕРЕД каждой попыткой отправки (успешной или нет), поэтому
    сбой сети/процесса после этого момента ничего не теряет, только
    откладывает до следующего запуска (см. send_queue). Возвращает путь к
    записанному файлу (None, если токен не совпал и ничего не записано) —
    вызывающий может сразу попробовать отправить именно его (см. send_one),
    не дожидаясь следующего запуска.

    log_text=None — аварийный путь (закрытие окна без явной отправки из JS,
    см. app/web/bridge.py:flush_abandoned_install_log): читаем то, что успело
    накопиться в _current.log. log_text задан (обычный путь, JS уже прислал
    полный собственный sessionLog) — используем его как есть, он полнее (там
    есть и JS-только строки — версия, "Все этапы установки выполнены" и т.п.,
    которые эта построчная дозапись тоже видит через install_log_append, но
    доверяем именно тому, что реально ушло бы техническим путём отправки)."""
    base_dir = Path(base_dir)
    with _LOCK:
        meta = _read_current_meta(base_dir)
        if not meta or meta.get("token") != token:
            return None  # чужая/более новая сессия — её файлы не трогаем
        if log_text is None:
            try:
                log_text = _current_log_path(base_dir).read_text(encoding="utf-8")
            except OSError:
                log_text = ""
        path = _write_queue_entry(base_dir, platform, brand, model, modification, success, log_text)
        _clear_current(base_dir)
        return path


def recover_stale_current(base_dir, platform: str) -> None:
    """Вызывается ОДИН раз при старте программы, до открытия любой модели —
    если _current.* пережил прошлый запуск, значит та сессия не дошла до
    штатного finalize_to_queue (вылет процесса или принудительное закрытие
    раньше, чем закрылось окно). С маркером активности — считаем её достойной
    внимания (была реальная попытка установки), кладём в очередь с
    success=False и текстовым маркером в начале лога; без маркера — была
    просто открыта модель или просмотрена инструкция, слать нечего, как и
    сегодня."""
    base_dir = Path(base_dir)
    with _LOCK:
        meta = _read_current_meta(base_dir)
        if not meta:
            _clear_current(base_dir)
            return
        has_activity = _current_activity_path(base_dir).exists()
        if not has_activity:
            _clear_current(base_dir)
            return
        try:
            log_text = _current_log_path(base_dir).read_text(encoding="utf-8")
        except OSError:
            log_text = ""
        log_text = f"{STALE_MARKER}\n{log_text}"
        _write_queue_entry(base_dir, platform, meta.get("brand", ""), meta.get("model", ""),
                            meta.get("modification", ""), False, log_text)
        _clear_current(base_dir)


def list_queue(base_dir) -> list[Path]:
    queue_dir = _queue_dir(Path(base_dir))
    if not queue_dir.is_dir():
        return []
    return sorted(p for p in queue_dir.iterdir() if p.is_file() and p.suffix == ".json")


def send_one(base_dir, path: Path | None, install_log_api, email: str | None,
             log=lambda message: None) -> None:
    """Пытается отправить ОДНУ конкретную запись очереди (см.
    finalize_to_queue — вызывается сразу после неё для немедленной попытки,
    не дожидаясь следующего запуска) через уже существующий
    InstallLogApi.send (см. app/web/api/install_log_api.py) — никакого
    нового сетевого кода. Удаляет файл при успехе, оставляет при сбое
    (следующий запуск повторит его вместе с остальной очередью, см.
    send_queue). path=None (finalize_to_queue не записал файл — токен не
    совпал) — тихо ничего не делает."""
    if path is None:
        return
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log(f"Не удалось разобрать отложенный лог {path.name} — пропускаю.")
        return
    try:
        result = install_log_api.send(
            entry.get("platform", ""), entry.get("brand", ""), entry.get("model", ""),
            entry.get("modification", ""), bool(entry.get("success")),
            entry.get("log_text", ""), email,
        )
    except Exception as exc:  # noqa: BLE001 - отправка отложенного лога не должна ронять программу
        log(f"Не удалось отправить отложенный лог {path.name}: {exc}")
        return
    if isinstance(result, dict) and result.get("ok"):
        path.unlink(missing_ok=True)
    else:
        log(f"Отложенный лог {path.name} снова не отправлен — попробуем при следующем запуске.")


def send_queue(base_dir, install_log_api, email: str | None, log=lambda message: None) -> None:
    """Проходит по ВСЕЙ очереди (см. send_one за отправкой одной записи) —
    вызывается фоновым потоком при старте программы (см.
    app/web/bridge.py:WebApi.__init__) и после каждого нормального
    завершения сессии (см. install_log_send — заодно опустошает и то, что
    накопилось раньше). Битая/нечитаемая запись (не должна возникать
    благодаря записи через .part+rename в _write_queue_entry, но не
    полагаемся на это) безопасно пропускается, не роняет весь проход."""
    for path in list_queue(base_dir):
        send_one(base_dir, path, install_log_api, email, log)
