"""Автообновление cars/ и каталога apk/ с сервера при запуске, плюс
периодический пульс для счётчика пользователей в админке — перенесено из
старого app/gui.py (_sync_scripts_from_server/_sync_shared_apks_catalog/
_start_heartbeat), которые не пережили переход на pywebview: main_web.py
никогда не вызывал ни sync_scripts, ни пульс, поэтому cars/ у техников
молча не обновлялся с самой миграции.

"Что нового" — сравнение version.json каждой модели (см. app/scanner.py:
ModelInfo.revision/changelog, app/car_generator.py: version.json,
записывается мастером "Добавить/Изменить машину" при каждом сохранении) с
тем, что техник уже видел (см. app/update_tracker.py)."""
import threading
import time

from ..events import event_bridge
from ... import update_tracker
from ...content_config import get_base_url
from ...content_sync import list_shared_apk_catalog, sync_scripts
from ...ping_client import PingError, get_or_create_client_id, send_ping
from ...scanner import flatten_models, scan_cars
from ...submit_config import get_submit_config

PING_INTERVAL_SECONDS = 3 * 60


class SyncApi:
    def __init__(self, base_dir, cars_dir, apk_dir, scanner_api):
        self.base_dir = base_dir
        self.cars_dir = cars_dir
        self.apk_dir = apk_dir
        self._scanner_api = scanner_api
        self._heartbeat_started = False

    @staticmethod
    def _log(message) -> None:
        event_bridge.push({"kind": "log", "text": str(message)})

    def startup_sync(self) -> dict:
        """Вызывается один раз из JS сразу после того, как главное окно
        готово (см. app.js) — до этого момента pywebview.api ещё недоступен.
        Возвращает {"changes": [{"kind": "added"|"updated", "label",
        "changelog"}, ...]} для сводки "Что нового" — пусто, если
        server.json не настроен, ничего не изменилось, или это вообще первый
        запуск программы (нет базовой линии для сравнения — см.
        update_tracker.compute_changes)."""
        changes: list[dict] = []
        if get_base_url(self.base_dir):
            try:
                sync_scripts(self.base_dir, self.cars_dir, log=self._log)
            except Exception as exc:  # noqa: BLE001 - сбой сети не должен ломать запуск
                self._log(f"Не удалось проверить обновления моделей на сервере: {exc}")
            else:
                models = flatten_models(scan_cars(self.cars_dir))
                changes, new_state = update_tracker.compute_changes(self.base_dir, models)
                update_tracker.save_seen(self.base_dir, new_state)

            try:
                catalog = list_shared_apk_catalog(self.base_dir)
            except Exception as exc:  # noqa: BLE001 - сбой сети не должен ломать запуск
                self._log(f"Не удалось получить список общей библиотеки приложений: {exc}")
            else:
                if catalog:
                    self._scanner_api.set_remote_apk_catalog(catalog)

        self._start_heartbeat()
        return {"changes": changes}

    def _start_heartbeat(self) -> None:
        if self._heartbeat_started:
            return
        self._heartbeat_started = True
        config = get_submit_config(self.base_dir)
        if not config:
            return
        client_id = get_or_create_client_id(self.base_dir)
        threading.Thread(target=self._heartbeat_loop, args=(client_id, config), daemon=True).start()

    @staticmethod
    def _heartbeat_loop(client_id, config) -> None:
        while True:
            try:
                send_ping(client_id, config)
            except PingError:
                pass  # счётчик пользователей необязателен — сбой сети тут не показываем
            time.sleep(PING_INTERVAL_SECONDS)
