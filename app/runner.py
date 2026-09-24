"""Запуск одного ADB-этапа stages.py (функция run(ctx)) в фоновом потоке."""
from __future__ import annotations
import threading

from .adb_utils import Adb
from .content_sync import ensure_apks_downloaded, sync_model_subfolder
from .install_context import InstallContext, InstallCancelled, check_device, device_unavailable_message


class InstallRunner:
    def __init__(self, adb_path, on_log, on_finished, base_dir=None, ask_input_fn=None,
                 on_sync_progress=None, on_apk_download_progress=None, on_apk_install_progress=None):
        """
        on_log(str) вызывается из фонового потока при каждой строке лога.
        on_finished(success: bool, message: str[, partial=True]) вызывается по завершении; partial —
        этап пройден, но часть приложений пропущена (окно этапа покажет «Установлено не всё»).
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
        on_apk_download_progress(путь, скачано, всего) — байты текущего
        докачиваемого APK (см. content_sync.ensure_apks_downloaded:
        on_file_progress) — для кольца окна установки.
        on_apk_install_progress(путь, готово, всего, состояние, фаза) — установка
        каждого выбранного APK (см. InstallContext.install_selected_apks) — для
        очереди того же окна.
        """
        self.adb_path = adb_path
        self.on_log = on_log
        self.on_finished = on_finished
        self.base_dir = base_dir
        self.ask_input_fn = ask_input_fn
        # *args: content_sync зовёт on_progress и с 4 аргументами (файлы
        # done/total) — заглушка на 2 роняла любой запуск без своего обработчика.
        self.on_sync_progress = on_sync_progress or (lambda done, total, *args: None)
        self.on_apk_download_progress = on_apk_download_progress or (lambda path, done, total: None)
        self.on_apk_install_progress = on_apk_install_progress
        self._thread = None
        self._cancel_flag = threading.Event()

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    def cancel(self):
        self._cancel_flag.set()

    def start(self, model, device_serial, selected_apks, run_fn, own_dirs=None,
              preferred_install_method: str = "", skip_sync: bool = False, require_device: bool = False):
        """Запускает run_fn(ctx) в фоновом потоке — run_fn это функция
        конкретного ADB-этапа из stages.py модели (см. stage_wizard.py,
        единственный вызывающий). own_dirs — локальные папки СВОИХ файлов
        именно этого этапа (см. install_api.py:_stage_own_dirs), которые
        нужно докачать перед запуском — пусто, если у этапа нет своих
        файлов (adb/uart/telnet без вложений, actions). preferred_install_method
        — см. install_api.py:start_stage/StepSpec.apps_install_method в
        car_generator.py (только для "apps"-этапов без своего run — пусто у
        всех остальных). skip_sync — всё нужное уже скачано отдельно (см. install_api.py:prefetch_apks —
        Wi-Fi ADB: компьютер к этому моменту уже в сети магнитолы БЕЗ интернета, повторная сверка с
        сервером только ждала бы таймаут). require_device — этапу нужна уже подключённая магнитола
        (см. install_api.py: _needs_device): без неё этап сразу заканчивается окном «Магнитола не
        подключена», ничего не скачивая и не выполняя."""
        if self.running:
            raise RuntimeError("Установка уже выполняется.")

        self._cancel_flag = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            args=(model, device_serial, selected_apks, run_fn, own_dirs or [], preferred_install_method, skip_sync,
                  require_device),
            daemon=True,
        )
        self._thread.start()

    def _check_cancelled(self):
        if self._cancel_flag.is_set():
            raise InstallCancelled("Установка остановлена пользователем.")

    def _run(self, model, device_serial, selected_apks, run_fn, own_dirs, preferred_install_method="",
             skip_sync=False, require_device=False):
        ctx = None
        try:
            if require_device:
                missing = check_device(Adb(self.adb_path, device_serial))
                if missing:
                    raise InstallCancelled(missing)  # текст уйдёт в лог через install_finished
            if self.base_dir and not skip_sync:
                for local_dir in own_dirs:
                    sync_model_subfolder(self.base_dir, local_dir, log=self.on_log,
                                          check_cancelled=self._check_cancelled,
                                          on_progress=self.on_sync_progress)
                ensure_apks_downloaded(self.base_dir, self.base_dir / "apk", selected_apks,
                                        log=self.on_log, check_cancelled=self._check_cancelled,
                                        on_progress=self.on_sync_progress,
                                        on_file_progress=self.on_apk_download_progress)
                self.sync_resign_cert(model)
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
                on_apk_progress=self.on_apk_install_progress,
                device_confirmed=require_device,
            )
            run_fn(ctx)
        except InstallCancelled as exc:
            self.on_finished(False, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку скрипта
            # Магнитола пропала посреди команд этапа (ctx.shell/ctx.push… с проверкой результата) —
            # понятная фраза, по которой программа покажет окно «что сделать», а не сырая команда adb.
            gone = device_unavailable_message(str(exc), during=bool(ctx and ctx._device_confirmed))
            if gone:
                self.on_finished(False, gone)
                return
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
        # ctx.failed_apps — apk, пропущенные install_selected_apks через
        # AppInstallFailed (способ установки залочен, но не сработал именно на
        # этом файле, см. install_context.py) — этап в целом НЕ упал, но
        # итоговое сообщение должно честно сказать, что встало не всё, а не
        # просто "успешно": иначе технику узнать об пропуске неоткуда, кроме
        # как долистать полный лог до нужной строки.
        failed_apps = getattr(ctx, "failed_apps", None)
        if failed_apps:
            self.on_finished(True, "Установка завершена, но не всё встало — пропущено: "
                                    + "; ".join(failed_apps) + ". Остальные приложения установлены.", partial=True)
        else:
            self.on_finished(True, "Установка завершена успешно.")

    def sync_resign_cert(self, model, check_cancelled=None) -> None:
        """Сертификат переподписи модели (files/resign_cert/{private.pk8,
        certificate.crt}, см. apk_signer.py) подтягиваем перед ЛЮБЫМ запуском
        установки. Раньше его не качало ничто, кроме ручной кнопки «Скачать»
        на всю модель: этап «apps» докачивает только отмеченные APK, а сам
        сертификат — не APK. В итоге InstallContext._maybe_resign молча
        пропускал переподпись, и магнитолы Changan отвечали «-118 ... is not
        auth» на неподписанный APK (логи #361/#362/#365). Два крошечных
        файла; у моделей без сертификата на сервере такой папки нет и
        вызов ничего не качает. Сбой сети здесь не должен срывать установку —
        просто предупреждаем в лог (без сертификата переподпись пропустится,
        а _maybe_resign скажет об этом отдельной строкой)."""
        try:
            sync_model_subfolder(self.base_dir, model.dir / "files" / "resign_cert",
                                 log=self.on_log, check_cancelled=check_cancelled or self._check_cancelled)
        except InstallCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - сеть/манифест не должны ронять установку
            self.on_log(f"Не удалось проверить сертификат переподписи модели на сервере: {exc}")
