"""Обёртка runner.py/install_context.py/stage_runner.py для stage wizard.
Настоящий run(ctx) конкретного этапа НИКОГДА не сериализуется в JSON — наружу
уходят только описательные поля этапа (см. _stage_to_dict), запуск идёт по
stage_index через install_load_stages(model_key), а сам run_fn достаётся
заново из stages.py на стороне Python (см. start_stage). Прогресс/ask_input
идут через app/web/events.py — тот же приём, что раньше был queue.Queue +
self.after(100, ...) в stage_wizard.py, только транспорт — evaluate_js вместо
Tk-виджетов (см. план миграции)."""
from __future__ import annotations
import base64
import mimetypes
import re
import subprocess
import threading
import time
import webbrowser
from pathlib import Path

from ..events import event_bridge, input_broker
from ...adb_utils import (SERVER_LEVEL_COMMANDS, TOP_LEVEL_COMMANDS, Adb, get_default_gateway_ip,
                           list_devices, scan_for_adb_hosts)
from ...content_sync import (fetch_manifest, filter_manifest, get_base_url, sync_model_apk_metadata,
                             sync_model_subfolder, sync_shared_folder)
from ...runner import InstallRunner
from ...scanner import scan_apk_dir_with_remote
from ...stage_runner import (StageDefinitionError, UnknownStageTypeError, load_model_wifi, load_stages,
                              load_wifi_port, stage_instruction_html_path)
from .scanner_api import apk_to_dict

_MANIFEST_CACHE_TTL_SECONDS = 60

_IMG_SRC_RE = re.compile(r'(<img\b[^>]*\bsrc=")([^"]+)(")', re.IGNORECASE)


def _inline_relative_images(html_text: str, base_dir: Path) -> str:
    """instruction.html на диске ссылается на фото ОТНОСИТЕЛЬНЫМ путём
    ("images/foo.jpg", см. instruction_html.save_instruction — так вся
    папка модели остаётся переносимой сама по себе). Раньше это грузилось
    в tkinterweb (Tkhtml3) прямо с диска — здесь же мы отдаём HTML в JS и
    показываем его через `<iframe srcdoc>` на странице, обслуживаемой
    pywebview с http://127.0.0.1:.../ (см. main_web.py) — Chromium не
    грузит file:// ресурсы из не-file:// документа, поэтому относительный
    src просто не найдётся. Подменяем каждый такой src на data:-URI —
    единственный способ показать фото без отдельного HTTP-роута на cars/."""
    def _replace(match: re.Match) -> str:
        prefix, src, suffix = match.group(1), match.group(2), match.group(3)
        if src.startswith(("http://", "https://", "data:")):
            return match.group(0)
        path = (base_dir / src).resolve()
        try:
            data = path.read_bytes()
        except OSError:
            return match.group(0)
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return f'{prefix}data:{mime};base64,{base64.b64encode(data).decode("ascii")}{suffix}'

    return _IMG_SRC_RE.sub(_replace, html_text)


_VIDEO_HREF_RE = re.compile(r'(<a class="video-btn" href=")([^"]+)(")', re.IGNORECASE)


def _resolve_video_hrefs(html_text: str, base_dir: Path) -> str:
    """Кнопка "Смотреть видео" (см. instruction_html._render_block)
    ссылается на файл ОТНОСИТЕЛЬНЫМ путём (videos/имя.mp4, та же причина
    переносимости модели, что и у фото — см. instruction_html.
    save_instruction), но сама инструкция отдаётся в <iframe srcdoc> со
    страницы на http://127.0.0.1:.../ — относительный href резолвился бы
    туда же, а не на реальный файл на диске. В отличие от фото видео НЕ
    встраивается в документ — это просто внешняя ссылка (target="_blank",
    уходит через pywebview в системный браузер/webbrowser.open(), который
    прекрасно понимает file:// как обычный URL), поэтому вместо затратного
    base64 всего файла (до 150 МБ, см. instruction_html.MAX_VIDEO_BYTES)
    достаточно подменить относительный путь на абсолютный file:// URI."""
    def _replace(match: re.Match) -> str:
        prefix, href, suffix = match.group(1), match.group(2), match.group(3)
        if href.startswith(("http://", "https://", "file://")):
            return match.group(0)
        try:
            uri = (base_dir / href).resolve().as_uri()
        except ValueError:
            return match.group(0)
        return f"{prefix}{uri}{suffix}"

    return _VIDEO_HREF_RE.sub(_replace, html_text)


class InstallApi:
    def __init__(self, adb_path: str, base_dir, scanner_api):
        self.adb_path = adb_path
        self.base_dir = base_dir
        self._scanner_api = scanner_api
        self._runner = InstallRunner(
            adb_path, self._on_log, self._on_finished,
            base_dir=base_dir, ask_input_fn=input_broker.request,
            on_sync_progress=self._on_sync_progress,
        )
        self._pending_stage_index: int | None = None
        self._manifest_cache: dict | None = None
        self._manifest_cache_time: float = 0.0

    # ------------------------------------------------------------------
    def _get_manifest(self) -> dict | None:
        """Кэширует content/manifest.json на несколько секунд — иначе он
        (весь каталог cars/+apk/, не только текущая модель) перекачивался бы
        заново при каждом рендере каждого apps-этапа за один проход мастера
        (load_stages -> потом несколько вызовов standard_apks на том же
        экране). TTL короткий — не для оффлайн-работы, только чтобы не
        дублировать сетевые запросы в пределах одной "сессии" открытой
        модели."""
        url = get_base_url(self.base_dir)
        if not url:
            return None
        now = time.monotonic()
        if self._manifest_cache is None or now - self._manifest_cache_time > _MANIFEST_CACHE_TTL_SECONDS:
            self._manifest_cache = fetch_manifest(url)
            self._manifest_cache_time = now
        return self._manifest_cache

    def load_stages(self, model_key: str) -> dict:
        model = self._scanner_api.get_model(model_key)
        if model is None:
            return {"error": "unknown model key"}
        if not model.stages_script:
            return {"stages": []}
        try:
            stages = load_stages(model)
        except UnknownStageTypeError:
            # Отдельно от StageDefinitionError ниже — это не поломанная
            # модель, а модель для более новой версии программы (см.
            # UnknownStageTypeError.__doc__). needs_update — фронтенд
            # показывает по нему отдельный callout с кнопкой "Проверить
            # обновления" вместо технического текста ошибки.
            return {
                "error": "Эта модель использует более новую версию программы, чем установлена у вас. "
                         "Обновите программу, чтобы установка стала доступна.",
                "needs_update": True,
            }
        except StageDefinitionError as exc:
            return {"error": f"Ошибка в stages.py: {exc}"}
        except Exception as exc:  # noqa: BLE001 - показать пользователю любую ошибку загрузки скрипта
            return {"error": f"Не удалось загрузить stages.py: {exc}"}

        # Только файлы, нужные ПРЯМО СЕЙЧАС для показа списка этапов —
        # instruction (текст/картинки видны сразу, до "Установки") — своя
        # маленькая подпапка files/instruction_N, а не вся files/+usb_files
        # модели разом (как было раньше, см. sync_model_files ниже в
        # runner.py/usb_api.py — та по-прежнему тянет модель целиком, но
        # только прямо перед запуском конкретного этапа). "apps" — см.
        # standard_apks() (список строится по манифесту, без докачки);
        # "usb"/"adb"/"exe" — свои файлы качаются по клику на соответствующем
        # этапе, не здесь.
        manifest = self._get_manifest()
        try:
            for stage in stages:
                if stage["type"] == "instruction" and stage.get("instruction"):
                    instr_dir = (model.dir / stage["instruction"]).parent
                    try:
                        sync_model_subfolder(self.base_dir, instr_dir, log=self._on_log,
                                              manifest=manifest, on_progress=self._on_sync_progress)
                    except Exception as exc:  # noqa: BLE001 - сбой сети не должен мешать открыть уже скачанное
                        self._on_log(f"Не удалось проверить обновления инструкции: {exc}")
        finally:
            self._on_sync_progress(0, 0)  # скрыть бар, даже если качать было нечего

        model_wifi = load_model_wifi(model)
        return {
            "stages": [self._stage_to_dict(model, i, s, manifest, model_wifi) for i, s in enumerate(stages)],
            # Порт Wi-Fi ADB для apps-этапов с apps_connection="wifi"/"ask"
            # (см. stage_wizard.js: buildTransportBar) — читается один раз
            # здесь, а не на каждый рендер этапа.
            "wifi_port": load_wifi_port(model),
        }

    def _stage_to_dict(self, model, index: int, stage: dict, manifest: dict | None = None,
                        model_wifi: bool = False) -> dict:
        html_path = stage_instruction_html_path(model, stage)
        exe_path = stage.get("exe_path")
        exe_exists = None
        if exe_path:
            # Локально уже есть — ИЛИ значится в манифесте сервера (ленивая
            # докачка, см. run_exe — сам файл при этом не качаем, только
            # проверяем, что он в принципе где-то есть, чтобы не показывать
            # ложное "файл не найден" тому, что просто ещё не скачано).
            exe_exists = Path(exe_path).exists()
            if not exe_exists and manifest is not None:
                try:
                    remote = "cars/" + Path(exe_path).resolve().relative_to(self.base_dir / "cars").as_posix()
                except ValueError:
                    remote = None
                if remote:
                    exe_exists = remote in manifest
        video_path = stage.get("video_path")
        video_exists = None
        if video_path:
            # Та же ленивая докачка, что и exe_path выше — сам .mp4 (до
            # 150 МБ) качается по факту клика "Смотреть видео" (open_video),
            # а не заранее при открытии мастера.
            video_exists = Path(video_path).exists()
            if not video_exists and manifest is not None:
                try:
                    remote = "cars/" + Path(video_path).resolve().relative_to(self.base_dir / "cars").as_posix()
                except ValueError:
                    remote = None
                if remote:
                    video_exists = remote in manifest
        return {
            "index": index,
            "type": stage["type"],
            "title": stage["title"],
            "description": stage.get("description"),
            "instruction_html": (_resolve_video_hrefs(
                                      _inline_relative_images(html_path.read_text(encoding="utf-8"), html_path.parent),
                                      html_path.parent)
                                  if html_path else None),
            # Граф исполнения (см. car_generator.py: StepSpec.next/
            # next_options) — id этого этапа и, для обычных типов, id
            # следующего; "check" вместо этого несёт next_options (id на
            # каждый вариант из check_options, тот же индекс). id по
            # умолчанию — позиция в списке, для моделей, ещё не
            # пересохранённых после перехода на граф.
            "id": stage.get("id", index),
            "next": stage.get("next"),
            "next_options": stage.get("next_options"),
            "variant_names": stage.get("variant_names"),
            "standard_label": stage.get("standard_label", "Стандартные приложения"),
            "usb_copy_selected_apks": stage.get("usb_copy_selected_apks", False),
            # Явный apps_connection побеждает; если его нет вовсе (этап
            # сохранён до появления этого поля), берём "wifi" для
            # wifi-only моделей вместо слепого "wired" — иначе apps-этап
            # без проводного ADB вообще пытался бы подключиться по USB
            # (реальный баг: Geely Cityray "со значком Wi-Fi"). Только для
            # type == "apps" — поле не имеет смысла для остальных типов.
            "apps_connection": ((stage.get("apps_connection") or ("wifi" if model_wifi else "wired"))
                                 if stage["type"] == "apps" else stage.get("apps_connection", "wired")),
            # Свой порт ИМЕННО этого apps-этапа (см. car_generator.py:
            # StepSpec.apps_wifi_port) — независим от общего wifi_port
            # модели (тот для adb/actions-этапов, см. "wifi_port" в
            # load_stages() ниже). None — заранее не известен, техник
            # вписывает сам на этапе (см. stage_wizard.js: buildTransportBar).
            "apps_wifi_port": stage.get("apps_wifi_port"),
            # ADB-команды (actions) — свой независимый выбор способа
            # подключения/порта, тот же смысл, что apps_connection/
            # apps_wifi_port выше (см. car_generator.py: StepSpec.
            # actions_connection/actions_wifi_port).
            "actions_connection": stage.get("actions_connection", "wired"),
            "actions_wifi_port": stage.get("actions_wifi_port"),
            # UART — порт Wi-Fi ADB чисто справочно (см. StepSpec.
            # uart_wifi_port) — не используется рантаймом.
            "uart_wifi_port": stage.get("uart_wifi_port"),
            "check_options": stage.get("check_options"),
            "exe_path": exe_path,
            "exe_name": Path(exe_path).name if exe_path else None,
            "exe_exists": exe_exists,
            "video_path": video_path,
            "video_label": stage.get("video_label") or "Смотреть видео",
            "video_exists": video_exists,
            "actions": [{"label": a.get("label", ""), "kind": a.get("kind", "command")}
                        for a in (stage.get("actions") or [])],
        }

    # ------------------------------------------------------------------
    _EMPTY_STANDARD_APKS = {"required": [], "optional": []}

    def standard_apks(self, model_key: str, stage_index: int, variant: str | None) -> dict:
        """Соответствует _standard_apks() в старом stage_wizard.py — APK
        "Стандартных приложений" этапа, с поправкой на выбранный вариант
        (Full/Lite/...) для этапов с standard_dir_base+variant_names.
        Разделены на обязательные (files/pack*/required — устанавливаются
        всегда, без чекбокса) и необязательные (.../optional — техник
        выбирает сам) — см. app/car_generator.py: StepSpec.standard_apks/
        standard_apks_optional за тем, как эти подпапки формируются.

        Список строится по манифесту сервера (remote_only, см.
        scanner.scan_apk_dir_with_remote), БЕЗ докачки самих APK — реальные
        файлы подтягиваются позже, прямо перед установкой этого этапа (см.
        runner.py: InstallRunner._run -> sync_model_files), а не просто от
        открытия модели/этой страницы мастера."""
        model = self._scanner_api.get_model(model_key)
        if model is None:
            return dict(self._EMPTY_STANDARD_APKS)
        stages = load_stages(model)
        stage = stages[stage_index]
        standard_dir = stage.get("standard_dir")
        if standard_dir is None and stage.get("standard_dir_base"):
            variant_names = stage.get("variant_names") or []
            variant = variant or (variant_names[0] if variant_names else None)
            if not variant:
                return dict(self._EMPTY_STANDARD_APKS)
            standard_dir = Path(stage["standard_dir_base"]) / variant
        if not standard_dir:
            return dict(self._EMPTY_STANDARD_APKS)
        standard_dir = Path(standard_dir)

        manifest = self._get_manifest()

        def remote_items(folder: Path) -> list[dict]:
            if manifest is None:
                return []
            try:
                remote_subpath = "cars/" + folder.relative_to(self.base_dir / "cars").as_posix()
            except ValueError:
                return []
            return filter_manifest(manifest, remote_subpath)

        required_dir, optional_dir = standard_dir / "required", standard_dir / "optional"
        # APK у пакетов модели скачиваются лениво — только перед установкой.
        # Их JSON-сайдкары маленькие, но содержат отображаемое название и
        # описание, поэтому подтягиваем их уже для списка выбора. Без этого
        # у свежей установки вместо «Monjaro Panel» показывалось имя файла.
        sync_model_apk_metadata(self.base_dir, [required_dir, optional_dir],
                                log=self._on_log, manifest=manifest,
                                on_progress=self._on_sync_progress)
        self._on_sync_progress(0, 0)
        return {
            "required": [apk_to_dict(apk) for apk in
                         scan_apk_dir_with_remote(required_dir, remote_items(required_dir))],
            "optional": [apk_to_dict(apk) for apk in
                         scan_apk_dir_with_remote(optional_dir, remote_items(optional_dir))],
        }

    # ------------------------------------------------------------------
    def list_devices(self) -> list[dict]:
        return list_devices(self.adb_path)

    # -- мини-консоль ADB под логом главного окна (была в tkinter-версии до
    # перехода на pywebview, см. app/gui.py:_send_console_command в истории
    # git) — свободная "adb shell <команда>" на выбранное устройство, не
    # привязанная к конкретному этапу мастера установки. -------------------
    def console_send(self, device: str | None, command: str) -> dict:
        command = command.strip()
        if not command:
            return {"ok": False}
        # Перед вводом уже стоит статичная подпись "adb" (см. index.html:
        # adb-console-label) — сама команда вводится БЕЗ этого слова
        # ("install foo.apk", "devices", "shell pm list packages"). По
        # привычке к настоящему терминалу его всё равно иногда дописывают
        # ("adb install foo.apk") — раньше это ломало разбор: первым словом
        # оказывалось "adb", в TOP_LEVEL_COMMANDS такого нет, и всё
        # уходило в шелл устройства как есть (устройство такой команды не
        # знает). Срезаем один лишний "adb" в начале, если он есть.
        if command.lower() == "adb":
            command = ""
        elif command[:4].lower() == "adb ":
            command = command[4:].lstrip()
        if not command:
            return {"ok": False}
        threading.Thread(target=self._console_worker, args=(device, command), daemon=True).start()
        return {"ok": True}

    @staticmethod
    def _console_log(message) -> None:
        event_bridge.push({"kind": "log", "text": str(message)})

    def _console_worker(self, device: str | None, command: str) -> None:
        first_word = command.split(maxsplit=1)[0].lower() if command else ""
        try:
            if first_word in TOP_LEVEL_COMMANDS:
                # "connect"/"pair"/"devices" и т.п. — команды самого adb, не
                # шелла устройства (см. adb_utils.TOP_LEVEL_COMMANDS). Для
                # серверных команд не привязываемся к выбранному устройству —
                # при "connect" оно ещё и не может быть известно заранее.
                target = None if first_word in SERVER_LEVEL_COMMANDS else device
                adb = Adb(self.adb_path, target, log=self._console_log)
                result = adb.run(*command.split(), check=False)
            else:
                adb = Adb(self.adb_path, device, log=self._console_log)
                result = adb.shell(command, check=False)
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку
            self._console_log(f"Ошибка: {exc}")
            return
        # Adb.run/shell больше не логируют вывод сами (см. комментарий в
        # adb_utils.py:run — раньше это спамило лог на каждый вызов внутри
        # автоматических скриптов вроде grant_all_permissions). Но для этой
        # свободной консоли, где человек сам печатает команду и ждёт ответ,
        # результат обязательно нужен — иначе успешный вызов выглядит так,
        # будто вообще ничего не произошло.
        self._log_console_result(command, result)

    def _log_console_result(self, command: str, result) -> None:
        output = (result.stdout or "").strip()
        error = (result.stderr or "").strip()
        # "am start" и некоторые другие shell-команды на многих версиях
        # Android возвращают returncode=0 ДАЖЕ ПРИ ОШИБКЕ (реальный случай:
        # "Error type 3 / Error: Activity class {...} does not exist." —
        # returncode всё равно 0) — код возврата тут ненадёжен, поэтому
        # известные фразы ошибок ищем прямо в тексте, а не только через него.
        translated = self._translate_console_error(f"{output}\n{error}")
        if translated:
            self._console_log(translated)
            if output:
                self._console_log(output)
            if error:
                self._console_log(error)
            return
        pretty = self._format_console_output(command, output) if result.returncode == 0 and output else None
        if pretty is not None:
            self._console_log(pretty)
        elif output:
            self._console_log(output)
        if error:
            self._console_log(error)
        if not output and not error:
            self._console_log("(готово)" if result.returncode == 0 else f"(код возврата: {result.returncode})")

    # Самые частые ошибки adb/Android, с которыми реально сталкиваются
    # техники — сырой "Error type 3"/"INSTALL_FAILED_..." ничего не говорит
    # человеку, не знакомому с внутренностями Android, и на практике это
    # приводило к тому, что люди просто продолжали вводить команды наугад,
    # не понимая, что вообще пошло не так. Не претендует на перевод ЛЮБОЙ
    # возможной ошибки — только тех, что встречаются постоянно; для
    # остального выводится исходный текст (см. вызывающий код) — уже хотя бы
    # покрашен как ошибка (см. log_format.js: classifyLogLevel, ищет "error"/
    # "exception" и т.п. и по-английски тоже).
    _CONSOLE_ERROR_TRANSLATIONS = [
        ("does not exist", "Ошибка: указанный компонент (activity/сервис) не найден — проверьте, что "
                            "приложение установлено, и что имя пакета и класса набраны верно (регистр букв важен)."),
        ("permission denial", "Ошибка: отказано в доступе — не хватает разрешения на это действие."),
        ("unknown package", "Ошибка: указанный пакет не найден — проверьте имя точно "
                             '(посмотреть можно через "shell pm list packages").'),
        ("install_failed_already_exists", "Ошибка: пакет с таким именем уже установлен — используйте "
                                           '"install -r", чтобы переустановить поверх.'),
        ("install_failed_duplicate_package", "Ошибка: пакет с таким именем уже установлен — используйте "
                                              '"install -r", чтобы переустановить поверх.'),
        ("install_failed_insufficient_storage", "Ошибка: на устройстве не хватает места для установки."),
        ("install_failed_version_downgrade", "Ошибка: нельзя установить более старую версию поверх уже установленной."),
        ("install_failed_update_incompatible", "Ошибка: установленная версия несовместима с этим APK (другая "
                                                "подпись) — сначала удалите текущее приложение (uninstall)."),
        ("install_failed_invalid_apk", "Ошибка: файл повреждён или не является корректным APK."),
        ("install_parse_failed_not_apk", "Ошибка: выбранный файл не является APK."),
        ("install_parse_failed_no_certificates", "Ошибка: APK не подписан — установка невозможна."),
        ("install_parse_failed_inconsistent_certificates", "Ошибка: приложение подписано другим ключом, чем уже "
                                                             "установленная версия — сначала удалите текущую (uninstall)."),
        ("install_failed_no_matching_abis", "Ошибка: это приложение не поддерживает архитектуру процессора этого устройства."),
        ("install_failed_older_sdk", "Ошибка: это приложение требует более новую версию Android, чем стоит на устройстве."),
        ("install_failed_missing_shared_library", "Ошибка: приложению не хватает системной библиотеки, которой нет на этом устройстве."),
        ("install_failed_test_only", "Ошибка: этот APK помечен как тестовый (test-only) — установите с флагом "
                                      '"install -t", если это ожидаемо.'),
        ("no devices/emulators found", "Ошибка: нет подключённых устройств — проверьте кабель или Wi-Fi ADB-подключение."),
        ("device offline", "Ошибка: устройство не отвечает (offline) — переподключите провод или Wi-Fi ADB заново."),
        ("device unauthorized", "Ошибка: устройство не авторизовано — примите запрос отладки по USB на экране устройства."),
        ("more than one device", "Ошибка: подключено несколько устройств — выберите конкретное в списке слева от поля ввода."),
        ("device not found", "Ошибка: указанное устройство не найдено — оно могло отключиться."),
        ("no such file or directory", "Ошибка: файл или папка не найдены по указанному пути."),
        ("failed to stat", "Ошибка: указанный путь к файлу не найден."),
        ("protocol fault", "Ошибка: сбой связи с устройством — попробуйте переподключить его."),
        ("not running as root", "Ошибка: нет root-доступа на этом устройстве — выполните сначала команду \"root\"."),
        ("remount failed", "Ошибка: не удалось перемонтировать /system на запись — на этом устройстве обычно "
                            "нужен root (сначала выполните \"root\")."),
        ("adb server is out of date", "Ошибка: adb-сервер устарел — выполните \"kill-server\", затем повторите команду."),
        ("cannot connect to daemon", "Ошибка: не удаётся подключиться к adb-серверу — выполните \"start-server\"."),
    ]

    # "Failure [ПРИЧИНА]" — общий формат ответа многих pm-команд (uninstall/
    # disable-user и т.п.) при отказе; сама ПРИЧИНА — отдельный код, часть из
    # них переведена здесь, остальные хотя бы показываются в понятной рамке
    # вместо голого "Failure [...]".
    _PM_FAILURE_REASONS = {
        "delete_failed_internal_error": "внутренняя ошибка системы",
        "delete_failed_device_policy_manager": "запрещено политикой устройства (MDM/корпоративное управление)",
        "delete_failed_user_restricted": "запрещено ограничениями текущего пользователя",
        "delete_failed_owner_blocked": "заблокировано владельцем устройства",
        "delete_failed_abort": "операция прервана системой",
    }

    @classmethod
    def _translate_console_error(cls, text: str) -> str | None:
        lowered = text.lower()
        for needle, translation in cls._CONSOLE_ERROR_TRANSLATIONS:
            if needle in lowered:
                return translation
        match = re.search(r"failure\s*\[([\w_]+)]", text, re.IGNORECASE)
        if match:
            code = match.group(1).lower()
            reason = cls._PM_FAILURE_REASONS.get(code, f"код {match.group(1)}")
            return f"Ошибка: команда отклонена системой ({reason})."
        return None

    @staticmethod
    def _effective_shell_command(command: str) -> str | None:
        """Реальная команда, которая выполнится ВНУТРИ шелла устройства —
        либо явно после "shell " (см. TOP_LEVEL_COMMANDS в adb_utils.py — сама
        "shell" тоже там, отдельно от произвольного текста после неё), либо
        весь ввод целиком, если это не команда самого adb (ветка по
        умолчанию в _console_worker — implicit "adb shell <ввод>"). Для
        настоящих adb-команд верхнего уровня (devices/install/push и т.п.,
        не "shell") возвращает None — там красиво форматировать нечего в
        этом смысле, у них своё форматирование ниже (см. _format_console_output)."""
        words = command.split()
        if not words:
            return None
        first = words[0].lower()
        if first == "shell":
            return " ".join(words[1:])
        if first in TOP_LEVEL_COMMANDS:
            return None
        return command

    @staticmethod
    def _format_console_output(command: str, output: str) -> str | None:
        """Красивое форматирование под самые частые команды диагностики
        головного устройства — сырой протокольный вывод adb (табуляции,
        "package:" в начале каждой строки и т.п.) читать неудобно. Команда,
        для которой формата ещё нет, возвращает None — тогда используется
        вывод как есть (см. вызывающий код)."""
        # "Success" (реже "true") — общий ответ УСПЕХА у многих pm-команд
        # (install/uninstall/pm clear/pm grant/pm revoke/pm enable/
        # pm disable-user и т.д.) — не привязан к конкретной команде, поэтому
        # проверяется универсально, до разбора того, что это была за команда.
        stripped_lower = output.strip().lower()
        if stripped_lower == "success":
            return "Выполнено успешно."

        words = command.split()
        first = words[0].lower() if words else ""
        if first == "devices":
            return InstallApi._format_devices(output)
        if first == "push":
            return InstallApi._format_push_pull(output, "Скопировано на устройство")
        if first == "pull":
            return InstallApi._format_push_pull(output, "Скопировано с устройства")

        shell_command = InstallApi._effective_shell_command(command)
        if shell_command is not None:
            shell_words = shell_command.split()
            if shell_words[:2] == ["pm", "list"] and shell_words[2:3] == ["packages"]:
                return InstallApi._format_packages(output)
            if shell_words[:2] == ["am", "start"]:
                return InstallApi._format_am_start(output)
            if shell_words[:2] == ["pm", "path"]:
                return InstallApi._format_pm_path(output)
            if shell_words[:2] == ["dumpsys", "battery"]:
                return InstallApi._format_dumpsys_battery(output)
            if shell_words[:2] == ["wm", "size"]:
                return InstallApi._format_wm_size(output)
            if shell_words[:2] == ["wm", "density"]:
                return InstallApi._format_wm_density(output)
        return None

    @staticmethod
    def _format_push_pull(output: str, verb: str) -> str | None:
        # "1 file pushed, 0 skipped. 12.3 MB/s (1048576 bytes in 0.081s)"
        match = re.search(
            r"(\d+)\s+files?\s+\w+,\s*(\d+)\s+skipped\.\s*([\d.]+\s*\S+/s)\s*\((\d+)\s*bytes\s*in\s*([\d.]+)s\)",
            output, re.IGNORECASE)
        if not match:
            return None
        count, skipped, speed, byte_count, seconds = match.groups()
        size = InstallApi._human_size(int(byte_count))
        parts = [f"{verb}: {count} файл(ов), {size} за {seconds} сек ({speed})"]
        if int(skipped):
            parts.append(f"пропущено: {skipped}")
        return ", ".join(parts) + "."

    @staticmethod
    def _human_size(byte_count: int) -> str:
        size = float(byte_count)
        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if size < 1024 or unit == "ГБ":
                return f"{size:.1f} {unit}" if unit != "Б" else f"{int(size)} {unit}"
            size /= 1024
        return f"{byte_count} Б"

    @staticmethod
    def _format_pm_path(output: str) -> str | None:
        line = output.strip().splitlines()[0] if output.strip() else ""
        if not line.startswith("package:"):
            return None
        return f"Путь к APK: {line[len('package:'):]}"

    _BATTERY_STATUS = {"1": "неизвестно", "2": "заряжается", "3": "разряжается", "4": "не заряжается", "5": "заряжена полностью"}
    _BATTERY_HEALTH = {
        "1": "неизвестно", "2": "в порядке", "3": "перегрев", "4": "неисправна",
        "5": "перенапряжение", "6": "сбой", "7": "переохлаждение",
    }

    @staticmethod
    def _format_dumpsys_battery(output: str) -> str | None:
        fields = dict(re.findall(r"^\s*([\w ]+?):\s*(\S+)\s*$", output, re.MULTILINE))
        if "level" not in fields:
            return None
        level = fields.get("level", "?")
        status = InstallApi._BATTERY_STATUS.get(fields.get("status", ""), fields.get("status", "?"))
        health = InstallApi._BATTERY_HEALTH.get(fields.get("health", ""), fields.get("health", "?"))
        parts = [f"Заряд: {level}%", f"статус: {status}", f"состояние: {health}"]
        if "temperature" in fields:
            try:
                parts.append(f"температура: {int(fields['temperature']) / 10:.1f}°C")
            except ValueError:
                pass
        if "voltage" in fields:
            parts.append(f"напряжение: {fields['voltage']} мВ")
        return " · ".join(parts)

    @staticmethod
    def _format_wm_size(output: str) -> str | None:
        physical = re.search(r"physical size:\s*(\S+)", output, re.IGNORECASE)
        override = re.search(r"override size:\s*(\S+)", output, re.IGNORECASE)
        if not physical and not override:
            return None
        parts = []
        if physical:
            parts.append(f"физическое разрешение: {physical.group(1)}")
        if override:
            parts.append(f"установленное разрешение: {override.group(1)}")
        text = " · ".join(parts) + "."
        return text[:1].upper() + text[1:]

    @staticmethod
    def _format_wm_density(output: str) -> str | None:
        physical = re.search(r"physical density:\s*(\S+)", output, re.IGNORECASE)
        override = re.search(r"override density:\s*(\S+)", output, re.IGNORECASE)
        if not physical and not override:
            return None
        parts = []
        if physical:
            parts.append(f"физическая плотность: {physical.group(1)} DPI")
        if override:
            parts.append(f"установленная плотность: {override.group(1)} DPI")
        text = " · ".join(parts) + "."
        return text[:1].upper() + text[1:]

    @staticmethod
    def _format_am_start(output: str) -> str | None:
        # "am start" при успехе печатает "Starting: Intent { act=... }" или
        # "{ cmp=пакет/activity }" — сама по себе фраза ни о чём не говорит
        # человеку, не знакомому с внутренностями Android (см. также
        # _translate_console_error — сюда попадают только успешные случаи,
        # ошибки "does not exist"/"Permission Denial" перехватываются раньше).
        if not output.lower().startswith("starting: intent"):
            return None
        match = re.search(r"(?:act|cmp)=(\S+)", output)
        return f"Запущено: {match.group(1)}" if match else "Запущено."

    @staticmethod
    def _format_devices(output: str) -> str:
        # "adb devices" (с -l или без) — первая строка всегда служебная
        # ("List of devices attached"), дальше "<серийник>\t<состояние>...".
        lines = [ln for ln in output.splitlines() if ln.strip() and "list of devices" not in ln.lower()]
        if not lines:
            return "Подключённых устройств не найдено."
        state_labels = {
            "device": "подключено",
            "offline": "не отвечает (offline)",
            "unauthorized": "не авторизовано — примите запрос на экране устройства",
            "no permissions": "нет доступа (permissions) — переустановите драйвер/проверьте udev-правила",
        }
        rows = []
        for line in lines:
            parts = line.split(maxsplit=1)
            serial = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
            state_word = rest.split(maxsplit=1)[0] if rest else "unknown"
            label = f"{serial} — {state_labels.get(state_word, state_word)}"
            # "devices -l" добавляет после состояния "product:... model:...
            # device:... transport_id:..." — раньше эта часть просто
            # отбрасывалась (нужна была только сама по себе state_word).
            model_match = re.search(r"model:(\S+)", rest)
            if model_match:
                label += f" ({model_match.group(1).replace('_', ' ')})"
            rows.append(f"  • {label}")
        return f"Устройств подключено: {len(rows)}\n" + "\n".join(rows)

    @staticmethod
    def _format_packages(output: str) -> str:
        # "pm list packages" (в т.ч. с -3/-s/-f и фильтром по имени) —
        # каждая строка "package:<имя>", или "package:<путь к apk>=<имя>"
        # с флагом -f.
        names = []
        for line in output.splitlines():
            line = line.strip()
            if not line.startswith("package:"):
                continue
            value = line[len("package:"):]
            if "=" in value:
                value = value.rsplit("=", 1)[1]
            names.append(value)
        if not names:
            return "Пакеты не найдены."
        names.sort()
        return f"Установлено пакетов: {len(names)}\n" + "\n".join(f"  • {name}" for name in names)

    # -- кнопка «Подключить Wi-Fi» под логом главного окна — то же самое, что
    # печатать "connect <ip>:<port>" в консоли выше, но без необходимости
    # знать IP магнитолы вручную (см. get_default_gateway_ip) — до этого
    # единственным способом подключиться по Wi-Fi ADB вне готовой модели с
    # галочкой "Wi-Fi" в мастере был именно ручной ввод команды, что
    # оказалось не всем очевидно (устройство ведь ещё НЕ появилось в
    # списке — выбирать пока нечего, сначала нужно подключиться).
    # Синхронная (не в фоновом потоке, в отличие от console_send) — JS-сторона
    # (см. app.js: connectAdbWifi) ждёт результат, чтобы решить, предлагать
    # ли технику ввести IP вручную: автоопределение по шлюзу работает,
    # только если ноутбук подключён именно к собственной точке доступа
    # магнитолы — не всегда так (например, магнитола в общей сети, или
    # локальный тест через 127.0.0.1). -------------------------------------
    def wifi_connect(self, port: int, ip: str | None = None) -> dict:
        ip = (ip or "").strip() or None
        auto = ip is None
        if not ip:
            ip = get_default_gateway_ip()
            if not ip:
                return {"ok": False, "auto": True, "ip": None,
                        "error": "Не удалось определить IP магнитолы автоматически "
                                 "(нет активного Wi-Fi-подключения с шлюзом)."}
        self._console_log(f"Подключаюсь по Wi-Fi ADB: {ip}:{port}")
        adb = Adb(self.adb_path, None, log=self._console_log)
        try:
            result = adb.run("connect", f"{ip}:{port}", check=False)
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку
            return {"ok": False, "auto": auto, "ip": ip, "error": str(exc)}
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        ok = "connected to" in output.lower() or "already connected" in output.lower()
        return {"ok": ok, "auto": auto, "ip": ip, "message": output}

    # Скан локальной подсети на открытый port — для случая, когда шлюз (см.
    # wifi_connect выше) не помог: магнитола сама подключилась к сети/точке
    # доступа ноутбука, поэтому IP не совпадает с гейтвеем. Синхронный, как и
    # wifi_connect — JS-сторона (см. app.js:connectAdbWifi) ждёт результат,
    # чтобы сразу показать список найденного техникy.
    def scan_wifi(self, port: int) -> list[str]:
        self._console_log(f"Сканирую локальную сеть на открытый порт {port}...")
        found = scan_for_adb_hosts(port)
        if found:
            self._console_log(f"Найдены устройства с открытым портом {port}: {', '.join(found)}")
        else:
            self._console_log(f"Не нашёл в сети устройств с открытым портом {port}.")
        return found

    def start_stage(self, model_key: str, stage_index: int, device_serial: str | None,
                     selected_apk_paths: list[str]) -> dict:
        if self._runner.running:
            return {"ok": False, "error": "Установка уже выполняется."}
        model = self._scanner_api.get_model(model_key)
        if model is None:
            return {"ok": False, "error": "unknown model key"}
        stages = load_stages(model)
        stage = stages[stage_index]
        # "apps" — единственный тип этапа без обязательного run(ctx) в
        # stages.py (см. stage_runner.py: STAGE_TYPES с run обязателен только
        # для usb/adb/uart/telnet): раньше он был чисто выбором галочек, а
        # установку выполнял отдельный следующий "adb"-этап. Теперь кнопка
        # "Начать установку" есть прямо на этапе apps (см. stage_wizard.js:
        # renderAppsStage) — если модель не задала свой run для него, ставим
        # отмеченные приложения тем же способом, что и обычный adb-этап
        # (ctx.install_selected_apks, см. install_context.py).
        run_fn = stage.get("run")
        if run_fn is None and stage["type"] == "apps":
            run_fn = lambda ctx: ctx.install_selected_apks()  # noqa: E731
        if run_fn is None:
            return {"ok": False, "error": f"Для этапа «{stage['title']}» не задан run(ctx)."}
        self._pending_stage_index = stage_index
        event_bridge.push({"kind": "install_log", "text": f"=== Этап {stage_index + 1}: {stage['title']} ==="})
        # usb_shared_folder — раньше был возможен только на "usb"-этапах (см.
        # usb_api.py), но StepSpec/stages.py его ни к какому типу не
        # привязывает, это просто ключ в словаре. Реальный случай: ADB-этап,
        # ссылающийся на тот же тяжёлый payload cars/_shared/<имя>/ (freetuga
        # и т.п.), что раньше писался только на флешку — синхронизируем его
        # так же, точечно перед запуском, а не при каждом старте программы.
        if stage.get("usb_shared_folder"):
            try:
                sync_shared_folder(self.base_dir, stage["usb_shared_folder"], log=self._on_log)
            except Exception as exc:  # noqa: BLE001 - не должно ронять запуск этапа
                self._on_log(f"Не удалось синхронизировать {stage['usb_shared_folder']}: {exc}")
        try:
            self._runner.start(model, device_serial, selected_apk_paths, run_fn=run_fn,
                                own_dirs=self._stage_own_dirs(model, stage, stage_index=stage_index),
                                preferred_install_method=stage.get("apps_install_method", ""))
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    @staticmethod
    def _stage_own_dirs(model, stage: dict, action_index: int | None = None,
                        stage_index: int | None = None) -> list[Path]:
        """Локальные папки СВОИХ файлов конкретного этапа — синхронизируются
        прямо перед запуском (см. runner.py: InstallRunner._run), вместо
        того чтобы (как раньше) тянуть всю files/+usb_files модели разом при
        запуске ЛЮБОГО её этапа (запуск telnet-этапа заодно качал APK
        отдельного apps-этапа той же модели). "apps" сюда не входит — её
        выбранные файлы докачивает ensure_apks_downloaded(...) по факту
        отмеченных галочек, точнее, чем вся папка required/optional разом.
        "usb" через этот путь не идёт вовсе (свой вызов в usb_api.py)."""
        if stage.get("type") == "actions":
            # Файлы действия (например Gboard) генератор кладёт в
            # files/actions_<номер этапа>_<номер действия>. Раньше здесь
            # учитывался только type=adb, поэтому клик запускал команду до
            # того, как её APK успевал появиться на диске.
            if action_index is None:
                return []
            if stage_index is None:
                return []
            return [model.dir / "files" / f"actions_{stage_index + 1}_{action_index + 1}"]
        if stage.get("type") != "adb":
            return []
        # adb_files (StepSpec в car_generator.py) не сохраняется как ключ в
        # скомпилированном STAGES-словаре — используется только при
        # генерации, поэтому точную подпапку (files/adb_N) здесь узнать
        # нельзя. Берём весь files/ модели (БЕЗ usb_files/ — прошивки для
        # флешки сюда не относятся).
        return [model.dir / "files"]

    def cancel_stage(self) -> dict:
        if self._runner.running:
            self._runner.cancel()
            event_bridge.push({"kind": "install_log", "text": "Останавливаю этап..."})
        return {"ok": True}

    # -- "actions"-этап: в отличие от start_stage выше, у самого этапа нет
    # единого run — техник может нажать любую из кнопок (см. StepSpec.actions
    # в car_generator.py) в любом порядке и по несколько раз, поэтому запуск
    # идёт по (stage_index, action_index), а не только по stage_index. Тот же
    # InstallRunner (один запущенный run_fn одновременно), тот же поток
    # событий install_log/install_finished — JS-сторона (см. stage_wizard.js:
    # renderActionsStage) отличает "этап выполняется" от "действие
    # выполняется" только тем, что не переходит на следующий этап по success.
    def run_action(self, model_key: str, stage_index: int, action_index: int,
                    device_serial: str | None, selected_apk_paths: list[str]) -> dict:
        if self._runner.running:
            return {"ok": False, "error": "Установка уже выполняется."}
        model = self._scanner_api.get_model(model_key)
        if model is None:
            return {"ok": False, "error": "unknown model key"}
        stages = load_stages(model)
        stage = stages[stage_index]
        actions = stage.get("actions") or []
        if not (0 <= action_index < len(actions)):
            return {"ok": False, "error": "unknown action index"}
        action = actions[action_index]
        self._pending_stage_index = stage_index
        event_bridge.push({"kind": "install_log", "text": f"--- {action.get('label') or 'Действие'} ---"})
        try:
            self._runner.start(model, device_serial, selected_apk_paths, run_fn=action["run"],
                                own_dirs=self._stage_own_dirs(model, stage, action_index, stage_index))
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def answer_input(self, req_id: str, value: str | None) -> dict:
        input_broker.answer(req_id, value)
        return {"ok": True}

    # ------------------------------------------------------------------
    def run_exe(self, exe_path: str) -> dict:
        path = Path(exe_path)
        if not path.exists():
            # Ленивая докачка — этот .exe мог ещё не попасть на диск (см.
            # load_stages: exe_exists теперь только проверяет манифест,
            # реальный файл качается именно здесь, по факту клика "Запустить").
            try:
                sync_model_subfolder(self.base_dir, path.parent, log=self._on_log,
                                      on_progress=self._on_sync_progress)
            except Exception as exc:  # noqa: BLE001 - показываем понятную ошибку ниже
                self._on_log(f"Не удалось скачать {path.name}: {exc}")
            finally:
                self._on_sync_progress(0, 0)
        if not path.exists():
            return {"ok": False, "error": f"Не удалось скачать {path.name} с сервера."}
        try:
            subprocess.Popen([str(path)], cwd=str(path.parent))
        except OSError as exc:
            return {"ok": False, "error": f"Не удалось запустить {path.name}: {exc}"}
        return {"ok": True}

    # ------------------------------------------------------------------
    def open_video(self, video_path: str) -> dict:
        """Кнопка "Смотреть видео" в нав-баре мастера (см. StepSpec.video_file
        — не путать с video-блоком внутри instruction.html, у него своя
        ссылка прямо в HTML, см. _resolve_video_hrefs). Та же ленивая
        докачка, что и run_exe, открывается тем же способом, что и
        video-блок — через системный обработчик file://, а не встраиванием
        в pywebview (файл может весить до 150 МБ, см. instruction_html.
        MAX_VIDEO_BYTES)."""
        path = Path(video_path)
        if not path.exists():
            try:
                sync_model_subfolder(self.base_dir, path.parent, log=self._on_log,
                                      on_progress=self._on_sync_progress)
            except Exception as exc:  # noqa: BLE001 - показываем понятную ошибку ниже
                self._on_log(f"Не удалось скачать {path.name}: {exc}")
            finally:
                self._on_sync_progress(0, 0)
        if not path.exists():
            return {"ok": False, "error": f"Не удалось скачать {path.name} с сервера."}
        webbrowser.open(path.resolve().as_uri())
        return {"ok": True}

    # ------------------------------------------------------------------
    # Колбэки InstallRunner — вызываются из фонового потока установки,
    # только толкают событие в очередь (см. app/web/events.py), ничего не
    # трогают в webview напрямую.
    def _on_log(self, message: str) -> None:
        event_bridge.push({"kind": "install_log", "text": message})

    def _on_sync_progress(self, done: int, total: int, files_done: int | None = None,
                          files_total: int | None = None) -> None:
        # (0, 0) — сигнал "скрыть бар" (см. load_stages ниже) — sync_tree
        # сам никогда так не вызывает on_progress (при total=0 скачивать
        # нечего, on_progress вообще не вызывается), так что с реальным
        # прогрессом не перепутается.
        event_bridge.push({"kind": "sync_progress", "done": done, "total": total,
                           "files_done": files_done, "files_total": files_total})

    def _on_finished(self, success: bool, message: str) -> None:
        event_bridge.push({
            "kind": "install_finished",
            "success": success,
            "message": message,
            "stage_index": self._pending_stage_index,
        })
