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
import shutil
import threading
import time

from ..events import event_bridge
from ... import update_tracker
from ...content_config import get_base_url
from ...content_sync import (ContentSyncError, fetch_manifest, filter_manifest, list_files_recursive,
                              list_shared_apk_catalog, prune_removed_models, sync_scripts,
                              sync_shared_apk_metadata)
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

    @staticmethod
    def _on_sync_progress(done: int, total: int, files_done: int | None = None,
                          files_total: int | None = None) -> None:
        # Тот же формат события, что уже рисует общий #main-progress бар
        # (см. app/web/api/install_api.py:_on_sync_progress/stage_wizard.js:
        # updateSyncProgress) — переиспользуем готовый механизм вместо
        # нового, чтобы скачивание моделей при самом первом запуске
        # программы не выглядело зависшим (раньше был только построчный лог).
        event_bridge.push({"kind": "sync_progress", "done": done, "total": total,
                           "files_done": files_done, "files_total": files_total})

    def startup_sync(self) -> dict:
        """Вызывается один раз из JS сразу после того, как главное окно
        готово (см. app.js) — до этого момента pywebview.api ещё недоступен.
        Возвращает {"changes": [{"kind": "added"|"updated", "label",
        "changelog"}, ...]} для сводки "Что нового" — пусто, если
        server.json не настроен, ничего не изменилось, или это вообще первый
        запуск программы (нет базовой линии для сравнения — см.
        update_tracker.compute_changes)."""
        self._remove_demo_car()
        self._remove_stale_merged_models()
        changes: list[dict] = []
        base_url = get_base_url(self.base_dir)
        if base_url:
            # Один манифест на весь запуск (см. content_sync.fetch_manifest/
            # server/backend.py: write_manifest) — раньше и cars/, и apk/
            # обходились отдельными рекурсивными сериями HTTP-запросов через
            # nginx autoindex (десятки-сотни на разросшемся дереве моделей),
            # теперь это один запрос, а cars-скрипты и каталог apk/ ниже
            # просто фильтруют уже скачанный список локально. None — сервер
            # ещё не обновлён (старый бэкенд без /content/manifest.json) или
            # сеть недоступна: всё ниже само откатывается на обход по HTTP,
            # как было раньше (см. content_sync.sync_tree: manifest=None).
            manifest = fetch_manifest(base_url)

            try:
                sync_scripts(self.base_dir, self.cars_dir, log=self._log, manifest=manifest,
                              on_progress=self._on_sync_progress)
                # sync_scripts выше только докачивает — сам никогда не
                # удаляет локальное (см. его докстринг), поэтому переименованные/
                # убранные на сервере модели отдельно убираем здесь (см.
                # prune_removed_models — сравнивает со снимком прошлого
                # манифеста, не трогает то, что технику ещё только предстоит
                # опубликовать).
                prune_removed_models(self.base_dir, self.cars_dir, manifest, log=self._log)
            except Exception as exc:  # noqa: BLE001 - сбой сети не должен ломать запуск
                self._log(f"Не удалось проверить обновления моделей на сервере: {exc}")
            else:
                models = flatten_models(scan_cars(self.cars_dir))
                changes, new_state = update_tracker.compute_changes(self.base_dir, models)
                update_tracker.save_seen(self.base_dir, new_state)
            finally:
                self._on_sync_progress(0, 0)  # скрыть бар, даже если качать было нечего/сбой сети

            try:
                if manifest is not None:
                    apk_items = filter_manifest(manifest, "apk")
                else:
                    apk_items = list_files_recursive(base_url, "apk")
            except ContentSyncError as exc:
                apk_items = None
                self._log(f"Не удалось получить список общей библиотеки приложений: {exc}")

            if apk_items is not None:
                catalog = list_shared_apk_catalog(self.base_dir, items=apk_items)
                if catalog:
                    self._scanner_api.set_remote_apk_catalog(catalog)

                try:
                    # Лёгкие *.json сайдкары (имя/описание) — чтобы ещё не
                    # скачанные APK в общей библиотеке показывали "красивое"
                    # имя, а не голое имя файла (см. scanner.scan_apks).
                    sync_shared_apk_metadata(self.base_dir, self.apk_dir, log=self._log, items=apk_items)
                except Exception as exc:  # noqa: BLE001 - сбой сети не должен ломать запуск
                    self._log(f"Не удалось получить метаданные общей библиотеки приложений: {exc}")

        self._start_heartbeat()
        return {"changes": changes}

    def _remove_demo_car(self) -> None:
        """cars/Demo/ (демо-модель для примера) убрана из продукта насовсем
        — путалась с настоящими машинами в списке у техников. Установки со
        старым инсталлятором (бандлившим её в dist/) всё ещё несут её на
        диске — сервер про неё ничего не знает и не пришлёт (её никогда не
        было в content/), поэтому обычный sync не тронет, только явное
        удаление здесь, один раз при каждом запуске, пока она не исчезнет."""
        demo_dir = self.cars_dir / "Demo"
        if demo_dir.exists():
            shutil.rmtree(demo_dir, ignore_errors=True)

    # Плоские модели (например Geely/Cityray (со значком Wi-Fi)), объединённые
    # в v0.4.0-alpha в группы с модификациями (Geely/Cityray/со значком Wi-Fi
    # (до 2026)) — sync_scripts только докачивает новое, никогда не удаляет
    # то, чего больше нет на сервере (см. content_sync.sync_tree), поэтому у
    # техника, уже видевшего старые плоские папки, остались бы ОБА варианта
    # разом (видимое задвоение машины в списке). Тот же приём, что и
    # _remove_demo_car — явное разовое удаление старых путей при каждом
    # запуске, пока они не исчезнут.
    _STALE_MERGED_DIRS = (
        ("Belgee", "X70 - Atlas Pro"), ("Belgee", "X70 после дек 2025"), ("Belgee", "X70 рест"),
        ("Changan", "CS75 Plus 1gen 3gen"), ("Changan", "CS75 Plus 4gen"),
        ("Chery", "Tiggo 7 Pro Max 2023"), ("Chery", "Tiggo 7 Pro Max 2024"),
        ("Exeed", "TXL 2020"), ("Exeed", "TXL 2024"),
        ("Exeed", "VX дорест"), ("Exeed", "VX рест 3 экрана"),
        ("GAC", "GS8 Carplay"), ("GAC", "GS8 II"),
        ("Geely", "Cityray (со значком Wi-Fi)"), ("Geely", "Cityray после 2026 (без Wi-Fi, Monji)"),
        ("Geely", "Monjaro 2026"), ("Geely", "Monjaro SE"),
        ("Haval", "F7 1рест"), ("Haval", "F7 New - F7x New"),
        ("Jetour", "Dashing 10 Android"),
        ("Omoda", "C5 2026"),
        ("Voyah", "Free NXP"),
    )
    # Тут имя старой плоской модели СОВПАДАЕТ с именем новой группы (папка
    # та же самая) — нельзя удалить папку целиком, там теперь ещё и
    # подпапки-модификации лежат (уже докачанные обычным sync). Убираем
    # только СВОИ файлы верхнего уровня старой плоской модели.
    _STALE_SELF_NAMED = (
        ("Geely", "Monjaro"), ("Jetour", "Dashing"), ("Omoda", "C5"), ("Voyah", "Free"),
    )

    def _remove_stale_merged_models(self) -> None:
        for brand, name in self._STALE_MERGED_DIRS:
            d = self.cars_dir / brand / name
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        for brand, name in self._STALE_SELF_NAMED:
            d = self.cars_dir / brand / name
            if not d.exists() or not (d / "stages.py").exists():
                continue
            for fname in ("install.py", "stages.py", "version.json", "_wizard_spec.json"):
                (d / fname).unlink(missing_ok=True)
            for dname in ("files", "usb_files", "__pycache__"):
                shutil.rmtree(d / dname, ignore_errors=True)

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
