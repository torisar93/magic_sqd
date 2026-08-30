"""Запуск одного ADB-этапа stages.py (функция run(ctx)) в фоновом потоке."""
from __future__ import annotations
import threading

from .content_sync import ensure_apks_downloaded, sync_model_subfolder
from .install_context import InstallContext, InstallCancelled


class InstallRunner:
    def __init__(self, adb_path, on_log, on_finished, base_dir=None, ask_input_fn=None,
                 on_sync_progress=None):
        """
        on_log(str) вызывается из фонового потока при каждой строке лога.
        on_finished(success: bool, message: str) вызывается по завершении.
        Оба callback должны сами позаботиться о потокобезопасности (см. gui.py).
        base_dir, если задан, используется, чтобы перед установкой тихо
        подтянуть СВОИ файлы конкретного этапа (own_dirs у start(), см.
        install_api.py:start_stage — не всю files/+usb_files модели разом,
        как было раньше: иначе запуск, например, telnet-этапа заодно тянул
        APK отдельного apps-этапа той же модели, до которого техник ещё не
        дошёл), а также докачать те из выбранных общих/стандартных APK
        (selected_apks), которых ещё нет локально (см.
        content_sync.ensure_apks_downloaded — дерево выбора уже показывает
        их благодаря манифесту при рендере, но сами файлы качаются только
        сейчас) — так APK по факту скачиваются "по требованию", в момент
        нажатия "Установить".
        ask_input_fn(prompt, title) -> str | None, если задан, пробрасывается
        в ctx.ask_input() для install.py/stages.py, которым нужно спросить у
        пользователя что-то во время установки (например, IPv6-адрес). Тоже
        вызывается из фонового потока и должен сам позаботиться о
        потокобезопасности (см. gui.py._ask_input_threaded).
        """
        self.adb_path = adb_path
        self.on_log = on_log
        self.on_finished = on_finished
        self.base_dir = base_dir
        self.ask_input_fn = ask_input_fn
        self.on_sync_progress = on_sync_progress or (lambda done, total: None)
        self._thread = None
        self._cancel_flag = threading.Event()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def cancel(self):
        self._cancel_flag.set()

    def start(self, model, device_serial, selected_apks, run_fn, own_dirs=None,
              preferred_install_method: str = ""):
        """Запускает run_fn(ctx) в фоновом потоке — run_fn это функция
        конкретного ADB-этапа из stages.py модели (см. stage_wizard.py,
        единственный вызывающий). own_dirs — локальные папки СВОИХ файлов
        именно этого этапа (см. install_api.py:_stage_own_dirs), которые
        нужно докачать перед запуском — пусто, если у этапа нет своих
        файлов (adb/uart/telnet без вложений, actions). preferred_install_method
        — см. install_api.py:start_stage/StepSpec.apps_install_method в
        car_generator.py (только для "apps"-этапов без своего run — пусто у
        всех остальных)."""
        if self.running:
            raise RuntimeError("Установка уже выполняется.")

        self._cancel_flag = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            args=(model, device_serial, selected_apks, run_fn, own_dirs or [], preferred_install_method),
            daemon=True,
        )
        self._thread.start()

    def _check_cancelled(self):
        if self._cancel_flag.is_set():
            raise InstallCancelled("Установка остановлена пользователем.")

    def _run(self, model, device_serial, selected_apks, run_fn, own_dirs, preferred_install_method=""):
        try:
            if self.base_dir:
                for local_dir in own_dirs:
                    sync_model_subfolder(self.base_dir, local_dir, log=self.on_log,
                                          check_cancelled=self._check_cancelled,
                                          on_progress=self.on_sync_progress)
                ensure_apks_downloaded(self.base_dir, self.base_dir / "apk", selected_apks,
                                        log=self.on_log, check_cancelled=self._check_cancelled,
                                        on_progress=self.on_sync_progress)
            ctx = InstallContext(
                adb_path=self.adb_path,
                device_serial=device_serial,
                model_dir=model.dir,
                selected_apks=selected_apks,
                log_fn=self.on_log,
                cancel_flag=self._cancel_flag,
                ask_input_fn=self.ask_input_fn,
                shared_dir=(self.base_dir / "cars" / "_shared") if self.base_dir else None,
                preferred_install_method=preferred_install_method,
            )
            run_fn(ctx)
        except InstallCancelled as exc:
            self.on_finished(False, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку скрипта
            # Полный traceback раньше шёл в видимый лог целиком — техника не
            # интересует трассировка Python, только понятная причина (см.
            # on_finished ниже); для отладки traceback всё равно попадает в
            # debug_logs/ в debug-сборке (см. main_web.py:_enable_debug_log_all).
            self.on_finished(False, f"Ошибка установки: {exc}")
            return
        finally:
            # (0, 0) скрывает прогресс-бар в логе после успеха, ошибки или
            # остановки — пользователь не остаётся с вечным индикатором.
            self.on_sync_progress(0, 0)
        self.on_finished(True, "Установка завершена успешно.")
