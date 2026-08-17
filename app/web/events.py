"""Мост "фоновый Python-поток -> JS" и обратно, замена связки
queue.Queue + root.after(100, ...), которая раньше жила в stage_wizard.py/
gui.py. Единственное место, вызывающее webview.evaluate_js — потокобезопасность
этого вызова из ПРОИЗВОЛЬНОГО потока не гарантирована во всех бэкендах
pywebview, поэтому все события идут через один выделенный поток-насос, а не
напрямую из воркер-потоков install/usb-этапов (см. план миграции)."""
import json
import queue
import threading
import uuid

_window = None  # см. set_window() — задаётся один раз из main_web.py после create_window()


def set_window(window) -> None:
    global _window
    _window = window


class EventBridge:
    """Однонаправленный канал Python -> JS. push() можно звать из любого
    потока (только кладёт в queue.Queue, ничего не трогает в webview),
    один поток-насос (start_pump) вызывает evaluate_js по очереди."""

    def __init__(self):
        self._queue: "queue.Queue[dict]" = queue.Queue()
        self._pump_thread: threading.Thread | None = None
        self._stop = threading.Event()

    def push(self, event: dict) -> None:
        self._queue.put(event)

    def start_pump(self) -> None:
        if self._pump_thread is not None:
            return
        self._pump_thread = threading.Thread(target=self._pump_loop, daemon=True)
        self._pump_thread.start()

    def stop_pump(self) -> None:
        self._stop.set()

    def _pump_loop(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if _window is None:
                continue
            payload = json.dumps(event, ensure_ascii=False)
            try:
                _window.evaluate_js(f"window.__onBackendEvent({payload})")
            except Exception:
                # Окно могло уже закрыться (пользователь закрыл приложение
                # во время работы фонового этапа) — событие просто теряется,
                # это не повод падать в потоке-насосе.
                pass


event_bridge = EventBridge()


class InputBroker:
    """Блокирующий запрос ввода из фонового потока (см. install_context.py
    ctx.ask_input) — тот же паттерн, что раньше был на threading.Event между
    воркер-потоком и Tk-потоком, только ответ приходит не из опроса очереди
    на UI-потоке, а отдельным вызовом WebApi.install_answer_input из JS."""

    def __init__(self, bridge: EventBridge):
        self._bridge = bridge
        self._pending: dict[str, tuple[dict, threading.Event]] = {}

    def request(self, prompt: str, title: str = "", choices: list[str] | None = None,
                allow_manual: bool = True) -> str | None:
        """Вызывается на воркер-потоке install/usb-этапа. БЛОКИРУЕТ до ответа.
        choices — необязательный список готовых вариантов (см. ctx.ask_choice
        в install_context.py) — тот же event "ask_input", просто с полем
        choices, JS-сторона сама решает по нему, показать select или
        обычное текстовое поле (см. showAskInputDialog в stage_wizard.js)."""
        req_id = uuid.uuid4().hex
        holder: dict = {}
        ev = threading.Event()
        self._pending[req_id] = (holder, ev)
        event = {"kind": "ask_input", "id": req_id, "prompt": prompt, "title": title}
        if choices:
            event["choices"] = choices
            event["allow_manual"] = allow_manual
        self._bridge.push(event)
        ev.wait()
        holder, _ev = self._pending.pop(req_id)
        return holder.get("value")

    def answer(self, req_id: str, value: str | None) -> None:
        """Вызывается из WebApi.install_answer_input (поток pywebview,
        который обслуживает js_api-вызовы)."""
        pending = self._pending.get(req_id)
        if pending is None:
            return  # либо уже отвечено, либо запрос протух (окно переоткрыли)
        holder, ev = pending
        holder["value"] = value
        ev.set()


input_broker = InputBroker(event_bridge)
