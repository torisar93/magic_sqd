"""Объект ctx, передаваемый в install.py каждой модели."""
from __future__ import annotations
import hashlib
import re
import shlex
import sys
import time
from pathlib import Path

from .adb_utils import Adb, AdbError
from .apk_package import read_package_name
from .scanner import read_apk_mock_location


class InstallCancelled(RuntimeError):
    """Пользователь нажал "Стоп"."""


# Подписи для лога/сообщения об ошибке — по индексу в install_apk_auto ниже.
_INSTALL_METHOD_LABELS = ("adb install", "adb push + pm install", "adb push + pm install -S (поток)",
                          "app_process + localinstall.apk (Chery DesaySV)",
                          "adb push + pm install -i (подмена установщика, Geely OneOS/NewEra)",
                          "app_process + dex-хелпер (PackageInstaller.Session, Geely OneOS)",
                          "adb install -g -t -d --install-reason 64 (Haval, «revived» ГУ)",
                          "JDWP-патч белого списка + pm install (Desay x9h — Haval Jolion 2026)")
# Те же способы, но короткими устойчивыми ключами — хранятся в
# StepSpec.apps_install_method/_wizard_spec.json/stages.py (см.
# car_generator.py) как явная подсказка "начни перебор с этого способа",
# когда автор модели уже знает, какой из них рабочий на этой магнитоле
# (не жёсткая привязка — если он всё-таки не сработает, install_apk_auto
# просто пойдёт дальше по остальным способам в обычном порядке).
INSTALL_METHOD_KEYS = ("adb_install", "pm_install", "pm_install_stream", "localinstall", "pm_install_spoofed",
                        "dex_shell_install", "adb_install_haval_revived", "jdwp_whitelist")

# Платформа Chery DesaySV (Jaecoo/Exeed/Chery/Tenet — общий поставщик ГУ)
# блокирует обычный "pm install" на уровне прошивки; единственный найденный
# рабочий способ — маленький helper APK, который запускается через
# app_process от имени shell и ставит целевой APK через Android API
# PackageInstaller.Session, тот же приём, что и в открытом проекте
# https://github.com/EvilBorsch/chery-adb-app-install (см. instuction.md в
# репозитории за разбором механизма). Как ПОСЛЕДНИЙ из способов
# install_apk_auto — на других платформах команда app_process просто не
# найдёт нужный класс и упадёт с ошибкой, не будет попыток install_selected_apks
# останавливаться на этом способе или что-то ломать.
_LOCALINSTALL_HELPER_NAME = "chery_localinstall.apk"
_LOCALINSTALL_REMOTE_APK = "/data/local/tmp/desaysv-install-target.apk"
_LOCALINSTALL_REMOTE_HELPER = "/data/local/tmp/desaysv-localinstall.apk"

# Тот же приём (app_process + helper через PackageInstaller.Session), что и
# localinstall выше, но для платформы Geely OneOS (Atlas/CityRay/Preface) —
# helper взят БУКВАЛЬНО из стороннего бесплатного установщика (пользователь
# подтвердил, что использовать его можно), не переписан с нуля: захвачен и
# разобран через devtools/fake_adb_device.py (перехвачена точная команда
# запуска на трёх разных моделях — один и тот же .dex, один и тот же набор
# флагов, см. cars/_shared/dex_shell_helper.dex). Класс "MonjiShellInstaller"
# ниже — это РЕАЛЬНОЕ имя входной точки, скомпилированное внутри самого
# .dex (как есть, не наше), а не название, которое мы выбираем сами — без
# него app_process не найдёт нужный класс. --flags 0x116 — это ровно
# INSTALL_REPLACE_EXISTING(0x2) | INSTALL_ALLOW_TEST(0x4) |
# INSTALL_INTERNAL(0x10) | INSTALL_GRANT_RUNTIME_PERMISSIONS(0x100), как и
# в оригинале, а не подобрано на глаз. В отличие от localinstall (Chery),
# помещаем оба файла НЕ по фиксированному общему пути, а рядом с самим APK.
_DEX_SHELL_HELPER_NAME = "dex_shell_helper.dex"
_DEX_SHELL_REMOTE_HELPER = "/data/local/tmp/dex_shell_helper.dex"
_DEX_SHELL_ENTRY_CLASS = "MonjiShellInstaller"  # см. пояснение выше — имя из самого .dex
_DEX_SHELL_INSTALL_FLAGS = 0x116

# Некоторые новые магнитолы Haval (прошивка "headunit revived", моделей пока
# нет в программе — способ добавлен заранее, чтобы можно было на него
# сослаться в apps_install_method, когда модели появятся) отклоняют обычный
# "adb install"/"adb install -r", но ставят APK с этим набором флагов —
# подтверждено пользователем вручную: `adb install -g -r -t -d
# --install-reason 64 "headunit revived.apk"`. -g — выдать все runtime-
# разрешения из манифеста сразу, -t — разрешить тестовые пакеты, -d —
# разрешить установку версии старше уже стоящей (downgrade), --install-reason
# 64 — код причины установки, с которым эта прошивка соглашается (обычная
# установка без него отклоняется). -r (переустановка) добавляется отдельно
# самим install_apk (reinstall=True по умолчанию), здесь его дублировать не
# нужно.
_HAVAL_REVIVED_EXTRA_ARGS = ("-g", "-t", "-d", "--install-reason", "64")


class VersionDowngradeError(AdbError):
    """PackageManager отклонил установку с INSTALL_FAILED_VERSION_DOWNGRADE —
    на устройстве уже стоит версия с таким же или более высоким versionCode.
    Отдельный тип (не голый AdbError) специально для install_apk_auto: реальный
    случай (2026-09, Geely Cityray/Monji) — техник ставил APK постарее той,
    что уже на магнитоле, install_apk_auto честно перебрал ВСЕ остальные
    способы установки один за другим (все с тем же результатом или с CLSE —
    сервис на этой платформе просто не поддерживается), и только в самом
    конце показал длинный список из 5 разных ошибок вместо одной понятной
    фразы "удалите старую версию". Смысла продолжать перебор нет — причина
    отказа не в СПОСОБЕ установки, а в самой версии APK, и на любом другом
    способе (если бы он вообще заработал на этой платформе) PackageManager
    отказал бы точно так же."""


class SignatureMismatchError(VersionDowngradeError):
    """INSTALL_FAILED_UPDATE_INCOMPATIBLE — на устройстве уже стоит приложение с
    ТЕМ ЖЕ именем пакета, но подписанное другим ключом (реальный случай, Geely
    Monji: GLauncher.Link и 3screen — оба com.maxinf.car). Как и с понижением
    версии, причина не в способе установки — перебор остальных бесполезен.
    Наследует VersionDowngradeError, чтобы уже существующие места, останавливающие
    перебор, сработали и здесь; тексты для техника различаются."""


class AppInstallFailed(RuntimeError):
    """Одно конкретное приложение не встало УЖЕ ПОСЛЕ того, как способ установки
    подтверждён рабочим на этой магнитоле (self._install_method залочен в
    install_apk_auto). До v1.0.23 такой сбой ничем не отличался от любой другой
    ошибки и улетал необработанным до самого runner.py — "Ошибка установки: ..."
    рушила ВЕСЬ этап целиком, стирая уже успешно поставленные приложения (реальный
    случай, 2026-09-20, Geely Atlas New/Monji, логи #390/#391: один и тот же
    gnss-client-v2.10.1.apk не подтверждал успех диффом списка пакетов, хотя сам
    dex-хелпер печатал "Success" — и это молча обрывало установку ещё ~10 уже
    готовых приложений следом в списке). install_selected_apks ловит это
    исключение и продолжает со следующим файлом — единичный сбой ОДНОГО apk не
    должен стоить техникам всей остальной, уже сделанной работы."""


_VERSION_DOWNGRADE_MARKER = "INSTALL_FAILED_VERSION_DOWNGRADE"
_SIGNATURE_MISMATCH_MARKER = "INSTALL_FAILED_UPDATE_INCOMPATIBLE"


def _raise_if_version_downgrade(text: str) -> None:
    upper = text.upper()
    if _VERSION_DOWNGRADE_MARKER in upper:
        raise VersionDowngradeError(text)
    if _SIGNATURE_MISMATCH_MARKER in upper:
        raise SignatureMismatchError(text)


def _check_pm_install_result(result) -> None:
    """pm install через adb shell (в отличие от "adb install" целиком) не
    всегда даёт надёжный код возврата — старые прошивки/adb используют shell
    протокол, который вообще не пробрасывает код завершения удалённой
    команды. Реальный результат смотрим в тексте, как выводит сама pm
    ("Success"/"Failure [...]") — тот же приём, что и везде в проекте, где
    нужно проверить именно вывод, а не полагаться на код возврата adb shell."""
    text = ((result.stdout or "") + (result.stderr or "")).strip()
    if "success" not in text.lower() or "failure" in text.lower():
        _raise_if_version_downgrade(text)
        raise AdbError(text or "pm install не подтвердил успех (пустой вывод)")


# Ошибки adb, после которых перебирать остальные способы установки бессмысленно:
# магнитолы нет (не подключена, отвалилась посреди установки) или она не разрешила
# отладку. Раньше перебор шёл до конца — 8 способов подряд с одной и той же
# «no devices/emulators found» / «device '…' not found» (install_logs #648, #557).
_DEVICE_UNAUTHORIZED_RE = re.compile(r"\bdevice unauthorized\b", re.IGNORECASE)
_DEVICE_GONE_RE = re.compile(r"no devices/emulators found|device '[^']*' not found|device not found|device offline",
                             re.IGNORECASE)


def _device_unavailable_message(text: str) -> str | None:
    if _DEVICE_UNAUTHORIZED_RE.search(text):
        return ("Магнитола не разрешила отладку по USB — подтвердите запрос «Разрешить отладку» на её экране "
                "и запустите установку заново.")
    if _DEVICE_GONE_RE.search(text):
        return ("Магнитола не подключена или отключилась во время установки — проверьте кабель (или Wi-Fi-"
                "подключение) и запустите установку заново.")
    return None


def _short_reason(exc, limit: int = 300) -> str:
    """Причина отказа одной строкой для лога: без переводов строк, не длиннее limit.
    Сообщение AdbError начинается с длинной команды (полный путь к adb, файлы),
    а сама причина — в конце, поэтому при обрезке оставляем ХВОСТ."""
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


class InstallContext:
    def __init__(self, adb_path, device_serial, model_dir: Path, selected_apks,
                 log_fn, cancel_flag, ask_input_fn=None, shared_dir: Path | None = None,
                 preferred_install_method: str = "", on_apk_progress=None):
        # on_apk_progress(путь, готово, всего, "running"/"done"/"error", фаза|None) — ход установки
        # каждого выбранного APK для очереди окна установки (см. install_selected_apks).
        self._on_apk_progress = on_apk_progress or (lambda path, completed, total, state, phase: None)
        self.model_dir = Path(model_dir)
        self.files_dir = self.model_dir / "files"
        # cars/_shared/ — общие для МНОГИХ моделей файлы (не только Python-
        # модули вроде wifi_adb.py, которые уже подключаются через sys.path
        # в сгенерированном stages.py, но и произвольный payload — скрипты,
        # прошивки и т.п., см. StepSpec.usb_shared_folder в car_generator.py).
        # Синхронизируется на клиент автоматически как часть cars/ (см.
        # content_sync.sync_scripts), одной копией на всех — не дублируется
        # в каждую модель. None, если вызывающий не передал base_dir
        # (не должно происходить в реальной установке, только в тестах).
        self.shared_dir = Path(shared_dir) if shared_dir is not None else None
        self.selected_apks = [Path(p) for p in selected_apks]
        self.device = device_serial
        self._log_fn = log_fn
        self._cancel_flag = cancel_flag
        self._ask_input_fn = ask_input_fn
        self._adb = Adb(adb_path, device_serial, log=self._log_fn)
        # Способ установки APK, определённый install_apk_auto на первом же
        # файле (индекс в _INSTALL_METHOD_LABELS) — держится в рамках ОДНОГО
        # запуска install.py (один InstallContext на запуск, см. runner.py),
        # чтобы не перебирать все три способа заново на каждом следующем APK.
        self._install_method: int | None = None
        # Строка «переподпись не используется» пишется один раз за запуск
        # (см. _maybe_resign), а не на каждый APK.
        self._resign_skip_logged = False
        # Подсказка "начни перебор с этого способа" (см. StepSpec.
        # apps_install_method в car_generator.py) — только меняет ПОРЯДОК
        # попыток в install_apk_auto, не пропускает остальные способы, если
        # автор модели ошибся. "" или незнакомый ключ — обычный порядок.
        self._preferred_method: int | None = (
            INSTALL_METHOD_KEYS.index(preferred_install_method)
            if preferred_install_method in INSTALL_METHOD_KEYS else None
        )
        # Пакеты, которым уже выдали разрешения в этом запуске — чтобы
        # инлайн-выдача в localinstall/dex_shell и общая после установки
        # (_after_app_installed) не сработали дважды на одном приложении.
        self._granted_packages: set[str] = set()
        # Имя пакета, которое localinstall/dex_shell определили сравнением
        # списков пакетов до/после — запасной вариант, если из самого APK
        # его прочитать не удалось (см. _after_app_installed).
        self._last_diff_package: str | None = None
        # Приложения, пропущенные install_selected_apks из-за AppInstallFailed
        # (способ уже залочен, но сработал не на всех apk) — runner.py читает
        # этот список ПОСЛЕ run_fn(ctx), чтобы итоговое сообщение этапа честно
        # упомянуло пропуски, а не просто отрапортовало "успешно", раз стадия
        # в целом не упала (см. install_selected_apks/AppInstallFailed).
        self.failed_apps: list[str] = []

    # --- служебное -------------------------------------------------
    def log(self, message):
        self._log_fn(str(message))

    def check_cancelled(self):
        if self._cancel_flag.is_set():
            raise InstallCancelled("Установка остановлена пользователем.")

    def ask_input(self, prompt, title="Ввод данных"):
        """Запрашивает у пользователя строку (например, IPv6-адрес магнитолы)
        через диалоговое окно. Вызывается из фонового потока установки и
        блокирует его до ответа пользователя — сам диалог показывается на
        главном потоке через ask_input_fn (см. gui.py/stage_wizard.py).
        Бросает InstallCancelled, если пользователь нажал "Отмена" или
        оставил поле пустым."""
        self.check_cancelled()
        if not self._ask_input_fn:
            raise RuntimeError("В этом режиме запуска ask_input недоступен")
        value = self._ask_input_fn(prompt, title)
        self.check_cancelled()
        if not value:
            raise InstallCancelled("Ввод отменён пользователем.")
        return value

    def ask_choice(self, prompt, choices, title="Выбор", allow_manual=True):
        """Как ask_input, но предлагает выбрать из готового списка вариантов
        (например IP-адреса, найденные сканом сети, см. cars/_shared/
        wifi_adb.py/telnet_adb.py) — рядом всегда есть пункт "Ввести
        вручную...", если allow_manual=True (по умолчанию), на случай, если
        нужного варианта в списке нет или скан ничего не нашёл. Тот же
        ask_input_fn обслуживает оба диалога — JS-сторона сама решает,
        показывать select или текстовое поле, по наличию choices в event."""
        self.check_cancelled()
        if not self._ask_input_fn:
            raise RuntimeError("В этом режиме запуска ask_choice недоступен")
        value = self._ask_input_fn(prompt, title, choices=choices, allow_manual=allow_manual)
        self.check_cancelled()
        if not value:
            raise InstallCancelled("Выбор отменён пользователем.")
        return value

    def sleep(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.check_cancelled()
            time.sleep(min(0.3, max(0, end - time.time())))

    # --- файлы модели ------------------------------------------------
    def file(self, relative_path):
        """Путь к файлу внутри files/ данной модели."""
        return self.files_dir / relative_path

    # --- adb-обёртки ---------------------------------------------------
    def adb(self, *args, **kwargs):
        self.check_cancelled()
        return self._adb.run(*args, **kwargs)

    def shell(self, command, **kwargs):
        self.check_cancelled()
        return self._adb.shell(command, **kwargs)

    def shell_log(self, command, **kwargs):
        """Как shell(), но явно пишет в лог и саму команду, и её вывод
        (stdout+stderr). Обычный shell() из "#"-команд мини-DSL (см.
        car_generator.py:_render_command_body) вызывается с check=False и
        результат нигде не оседает — осознанный выбор против "стены текста"
        на длинных цепочках рутинных команд (см. adb_utils.py: Adb.run).
        Для диагностических кнопок ("actions"-этап, техник просто нажимает
        и потом отправляет лог целиком через "Сообщить о проблеме") нужен
        именно сырой вывод — маркер "#log <команда>" в мини-DSL рендерит
        вызов именно этого метода, а не shell()."""
        self.log(f"$ {command}")
        kwargs.setdefault("check", False)
        result = self.shell(command, **kwargs)
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        self.log(output if output else "(пусто)")
        return result

    def install_apk(self, path, reinstall=True, extra_args=None, timeout=180):
        self.check_cancelled()
        self.log(f"Установка APK: {Path(path).name}")
        try:
            return self._adb.install(path, reinstall=reinstall, extra_args=extra_args, timeout=timeout)
        except AdbError as exc:
            _raise_if_version_downgrade(str(exc))
            raise

    def _duplicate_package_conflict(self) -> str | None:
        """Выбраны РАЗНЫЕ файлы с одним именем пакета (например GLauncher.Link и
        3screen — оба com.maxinf.car): они заменяют друг друга, второй не встанет
        из-за другой подписи, а техник остаётся с половиной списка. Ловим ДО
        установки. Копии с одинаковым содержимым (тот же APK из пакета модели и
        из общей библиотеки) — не конфликт."""
        by_package: dict[str, list[Path]] = {}
        for apk in dict.fromkeys(Path(a) for a in self.selected_apks):
            package = read_package_name(apk)
            if package:
                by_package.setdefault(package, []).append(apk)
        for package, files in by_package.items():
            if len(files) < 2:
                continue
            try:
                digests = set()
                for f in files:
                    h = hashlib.sha256()
                    with open(f, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1 << 20), b""):
                            h.update(chunk)
                    digests.add(h.digest())
            except OSError:
                continue
            if len(digests) > 1:
                names = ", ".join(f"«{f.name}»" for f in files)
                return (
                    f"Выбраны приложения с одним и тем же именем пакета ({package}): {names}. Они заменяют друг "
                    "друга и не могут стоять вместе (второе не установится из-за другой подписи). "
                    "Оставьте что-то одно и запустите этап заново."
                )
        return None

    def install_selected_apks(self, extra_args=None):
        # Не скачалось (нет интернета) — сразу и понятно, ДО магнитолы: раньше каждый такой
        # файл проходил все 8 способов установки с «cannot stat …: No such file» (install_logs
        # #570). Android в этом случае так же останавливается («Файл не скачан»).
        missing = [apk.name for apk in dict.fromkeys(self.selected_apks) if not apk.is_file()]
        if missing:
            raise InstallCancelled(
                "Не скачаны приложения: " + ", ".join(missing) + " — без них установка невозможна. "
                "Проверьте интернет (компьютер не должен быть подключён к Wi-Fi магнитолы без интернета) "
                "и запустите установку заново.")
        conflict = self._duplicate_package_conflict()
        if conflict:
            raise InstallCancelled(conflict)
        mock_target = self._mock_location_target()
        # Очередь окна установки (progress08.js) — та же последовательность, что шлёт Android
        # (InstallEngine.installApksWithProgress): «устанавливается» (готово = index) → «готово»
        # (index + 1) или «ошибка». Раньше десктоп слал только скачивание, и после него окно так и
        # оставалось на «Скачивание приложений» с «Готово 0 из N» (жалоба владельца, 2026-09-23).
        total = len(self.selected_apks)
        for index, apk in enumerate(self.selected_apks):
            self._last_diff_package = None
            self._on_apk_progress(str(apk), index, total, "running", "install")
            try:
                self.install_apk_auto(apk, extra_args=extra_args)
            except AppInstallFailed as exc:
                # Способ установки уже подтверждён рабочим на этой магнитоле —
                # сбой именно этого apk не должен стоить техникам остальных,
                # уже успешно установленных приложений (см. AppInstallFailed).
                self.failed_apps.append(str(exc))
                self._on_apk_progress(str(apk), index, total, "error", None)
                continue
            except BaseException:
                # Стоп, ни один способ не подошёл и т.п. — этап заканчивается, строка не должна
                # остаться на «Установка…».
                self._on_apk_progress(str(apk), index, total, "error", None)
                raise
            # Приложение уже стоит; разрешения ниже установку не срывают (см. _after_app_installed).
            self._on_apk_progress(str(apk), index + 1, total, "done", None)
            self._after_app_installed(apk, apk == mock_target)
        if self.failed_apps:
            self.log("Не установлено (пропущено, остальные приложения из списка "
                      "установлены): " + "; ".join(self.failed_apps))

    def _mock_location_target(self) -> Path | None:
        """Приложение, которому после установки нужно выдать фиктивное
        местоположение: ровно ОДНО из выбранных с пометкой админа
        ("mock_location": true в <файл>.json, только раздел GPS). Если таких
        несколько — не выдаём никому: какое из них техник хочет использовать,
        неизвестно, а фиктивное местоположение действует на устройстве
        только для одного приложения."""
        flagged = [apk for apk in self.selected_apks if read_apk_mock_location(apk)]
        if len(flagged) > 1:
            self.log("Выбрано несколько GPS-приложений с автовыдачей фиктивного местоположения "
                     "— автоматически оно не выдаётся, выберите приложение вручную на этапе "
                     "«Доп. действия».")
            return None
        return flagged[0] if flagged else None

    def _after_app_installed(self, apk: Path, give_mock_location: bool) -> None:
        """После КАЖДОГО успешно установленного приложения (любым способом
        и по любому подключению — сюда приходят все семь способов через
        install_apk_auto) выдаёт ему все разрешения, а помеченному GPS-
        приложению — ещё и фиктивное местоположение. Ошибка выдачи не должна
        срывать установку: приложение уже стоит, разрешения можно выдать
        вручную на этапе «Доп. действия» — поэтому любой сбой (кроме
        «Стоп» от техника) только пишется в лог."""
        package = read_package_name(apk) or self._last_diff_package
        if not package:
            self.log(f"Не удалось определить имя пакета «{apk.name}» — разрешения автоматически не выданы.")
            return
        self._grant_all_permissions_if_available(package)
        if give_mock_location:
            self._set_mock_location_if_available(package)

    def install_apk_auto(self, path, extra_args=None):
        """Устанавливает APK, автоматически подбирая рабочий способ — по
        очереди adb install → adb push + pm install → adb push + pm install
        -S (поток, см. install_apk_stream). Часть магнитол не тянет обычный
        "adb install" целиком (обрезанный/сломанный протокол install у
        adbd), но всё равно ставит через push+pm install на устройстве, а
        часть — только потоковым способом (реальные случаи: Jetour Dashing
        на Android 9, Soueast S09).

        Способ определяется ОДИН раз за весь запуск install.py — на первом
        же APK (используется install_selected_apks для всего списка
        выбранных приложений) — и запоминается в self._install_method для
        всех следующих файлов без повторного перебора: если что-то сработало
        один раз на этой магнитоле, нет смысла заново пробовать более
        медленные/ненадёжные способы на каждом файле. Если не сработал НИ
        ОДИН из трёх способов — останавливает установку (InstallCancelled) —
        плохой знак сразу для всего оставшегося списка, продолжать нет
        смысла.

        А вот если способ уже залочен (сработал на предыдущих файлах этого же
        запуска) и падает именно СЕЙЧАС — плохой знак только для ЭТОГО apk, не
        для всей магнитолы: реальный случай (2026-09-20, Geely Atlas New/Monji,
        логи #390/#391) — 10 приложений уже стояли, а gnss-client-v2.10.1.apk не
        подтвердил успех диффом списка пакетов (dex-хелпер при этом печатал
        "Success") — раньше AdbError отсюда никто не ловил, и он улетал прямо в
        runner.py, стирая весь этап целиком вместе с уже сделанной работой. Теперь
        такой сбой поднимается как AppInstallFailed — install_selected_apks его
        ловит и пропускает только этот файл, не роняя оставшийся список."""
        self.check_cancelled()
        path = self._maybe_resign(path)
        if self._install_method is not None:
            try:
                self._install_with_method(self._install_method, path, extra_args)
            except VersionDowngradeError as exc:
                raise InstallCancelled(self._rejection_message(path, exc))
            except AdbError as exc:
                label = _INSTALL_METHOD_LABELS[self._install_method]
                self.log(f"  ↳ не сработало ({label}): {_short_reason(exc)}")
                # Магнитола отвалилась — остальные приложения списка тоже не встанут.
                gone = _device_unavailable_message(str(exc))
                if gone:
                    raise InstallCancelled(gone) from exc
                raise AppInstallFailed(f"{Path(path).name}: {_short_reason(exc, 150)}") from exc
            return
        errors = []
        order = range(len(_INSTALL_METHOD_LABELS))
        if self._preferred_method is not None:
            order = [self._preferred_method, *(m for m in order if m != self._preferred_method)]
        for method in order:
            try:
                self._install_with_method(method, path, extra_args)
            except VersionDowngradeError as exc:
                # Причина отказа не в способе установки, а в самой версии APK
                # — дальше по списку способов пробовать бессмысленно, они
                # либо не поддерживаются этой платформой вовсе (см. остальные
                # ошибки в этом же переборе), либо упрутся в тот же самый
                # INSTALL_FAILED_VERSION_DOWNGRADE. Понятное сообщение вместо
                # длинного списка из N разных "не сработало" (см.
                # VersionDowngradeError).
                raise InstallCancelled(self._rejection_message(path, exc))
            except AdbError as exc:
                errors.append(f"{_INSTALL_METHOD_LABELS[method]}: {exc}")
                # Причину отказа каждого способа — сразу в лог: итоговое сообщение
                # уходит только в окно результата и не попадает в лог, если
                # техник закрыл программу раньше (так и вышло в реальных логах —
                # 7 «Установка APK…» подряд и ни одной причины).
                self.log(f"  ↳ не сработало ({_INSTALL_METHOD_LABELS[method]}): {_short_reason(exc)}")
                # Причина не в способе, а в том, что магнитолы нет — остальные способы
                # упрутся в то же самое (см. _device_unavailable_message).
                gone = _device_unavailable_message(str(exc))
                if gone:
                    raise InstallCancelled(gone) from exc
                continue
            self._install_method = method
            if method > 0:
                self.log(f"Сработал способ установки APK: {_INSTALL_METHOD_LABELS[method]} — "
                         "дальше буду использовать его же для остальных приложений.")
            return
        raise InstallCancelled(
            f"Не удалось установить {Path(path).name} ни одним из способов "
            "(adb install / pm install / pm install -S / localinstall.apk):\n" + "\n".join(errors)
        )

    @classmethod
    def _rejection_message(cls, path, exc) -> str:
        if isinstance(exc, SignatureMismatchError):
            match = re.search(r"Package (\S+) signatures", str(exc))
            what = f"приложение {match.group(1)}" if match else "приложение с тем же именем пакета"
            return (
                f"«{Path(path).name}» не установилось: на магнитоле уже стоит {what}, подписанное другим "
                "ключом (другая сборка или другое приложение с тем же пакетом) — поверх обновить нельзя. "
                "Удалите его на магнитоле вручную (Настройки → Приложения) и запустите установку заново "
                "или не выбирайте такие приложения вместе."
            )
        return cls._version_downgrade_message(path)

    @staticmethod
    def _version_downgrade_message(path) -> str:
        return (
            f"На магнитоле уже установлена версия «{Path(path).name}» новее (или такая же), чем в этой "
            "сборке — Android не позволяет тихо откатить версию назад. Удалите текущую версию приложения "
            "на магнитоле вручную (через её диспетчер приложений) и запустите установку заново."
        )

    def _maybe_resign(self, path) -> Path:
        """Если у модели есть files/resign_cert/{private.pk8,certificate.crt}
        (см. app/apk_signer.py) — переподписывает APK этим сертификатом
        ПЕРЕД любым способом установки. Нужно для платформ вроде Changan
        WutongOS, где ГУ проверяет serial number сертификата APK и
        отказывается ставить обычный "adb install"/"pm install" без
        совпадения (см. research/Changan/notes.md). Молча возвращает путь
        без изменений, если сертификата для этой модели нет — большинство
        моделей его не имеют."""
        from .apk_signer import ApkSignError, resign_apk, resign_cert_dir_for_model

        path = Path(path)
        cert_dir = resign_cert_dir_for_model(self.model_dir)
        if cert_dir is None or self.shared_dir is None:
            # Раньше здесь было полное молчание — из-за него потерянный
            # сертификат Changan (логи #361/#362/#365) выглядел в логе как
            # «переподпись просто не нужна». Строка один раз за запуск.
            if not self._resign_skip_logged:
                self._resign_skip_logged = True
                self.log("Переподпись APK для этой модели не используется "
                         "(сертификата files/resign_cert нет).")
            return path
        base_dir = self.shared_dir.parent.parent
        # Своя папка вне cars/ и apk/, а не <имя>_resigned.apk рядом с
        # исходником: оттуда копию при следующем запуске этапа подхватывал
        # список обязательных APK (scanner.scan_apk_dir сканирует папку) —
        # тот же пакет, другая подпись, этап падал на проверке дубликатов
        # (install_logs #589). Имя то же — в логе и на устройстве без
        # "_resigned". Под base_dir, а не в системном temp: путь с не-ASCII
        # именем пользователя Windows (%TEMP%) не всегда переваривают adb/java.
        out_path = base_dir / "resign_cache" / path.name
        out_path.parent.mkdir(parents=True, exist_ok=True)
        self.log(f"Переподписываю {path.name} сертификатом магнитолы (обязательно для этой модели)...")
        try:
            resign_apk(base_dir, path, cert_dir, out_path)
        except ApkSignError as exc:
            raise AdbError(str(exc))
        self.log(f"Подписано: {path.name}")
        return out_path

    def _install_with_method(self, method: int, path, extra_args) -> None:
        if method == 0:
            self.install_apk(path, extra_args=extra_args)
        elif method == 1:
            self.install_apk_pm(path, extra_args=extra_args)
        elif method == 2:
            self.install_apk_stream(path, extra_args=extra_args)
        elif method == 4:
            self.install_apk_pm_spoofed(path, extra_args=extra_args)
        elif method == 5:
            self.install_apk_dex_shell(path)
        elif method == 6:
            self.install_apk_haval_revived(path, extra_args=extra_args)
        elif method == 7:
            self.install_apk_jdwp_whitelist(path, extra_args=extra_args)
        else:
            self.install_apk_localinstall(path)

    def install_apk_pm(self, path, remote_dir="/sdcard/Download", extra_args=None):
        """Установка через adb push + "pm install" по пути на устройстве
        (без -r файла целиком через сам adb) — промежуточный способ между
        install_apk (adb install целиком) и install_apk_stream (поток): для
        магнитол, где протокол install у adbd не работает, но обычный push
        файла + запуск pm install на устройстве — работает."""
        self.check_cancelled()
        path = Path(path)
        remote_path = f"{remote_dir.rstrip('/')}/{path.name}"
        self.log(f"Установка APK (adb push + pm install): {path.name}")
        self.push(path, remote_path)
        extra = (" " + " ".join(extra_args)) if extra_args else ""
        result = self.shell(f"pm install -r {shlex.quote(remote_path)}{extra}", check=False)
        _check_pm_install_result(result)

    def install_apk_stream(self, path, remote_dir="/sdcard/Download", extra_args=None):
        """Установка APK через "cat <файл на устройстве> | pm install -S
        <размер>" вместо обычного install_apk (adb install) — для магнитол,
        где штатный adb install не работает (обрезанный/сломанный протокол
        install у adbd — реальный случай на Jetour Dashing на Android 9 и
        Soueast S09), но обычный adb push + adb shell с пайпами работает.
        -S <размер> обязателен для pm install в потоковом режиме (сколько
        байт читать из stdin, у пайпа нет своего EOF-сигнала для него) —
        берём его сами с локального файла, а не храним числом в коде
        модели: не отвяжется от файла, если его когда-нибудь заменят."""
        self.check_cancelled()
        path = Path(path)
        remote_path = f"{remote_dir.rstrip('/')}/{path.name}"
        self.log(f"Установка APK (потоком через pm install -S): {path.name}")
        self.push(path, remote_path)
        size = path.stat().st_size
        extra = (" " + " ".join(extra_args)) if extra_args else ""
        result = self.shell(f"cat {shlex.quote(remote_path)} | pm install -S {size}{extra}", check=False)
        _check_pm_install_result(result)

    def install_apk_pm_spoofed(self, path, remote_dir="/data/local/tmp", extra_args=None):
        """adb push + "pm install -i com.android.packageinstaller -t -g -r"
        — подмена "личности" установщика под системный Package Installer,
        портировано 1:1 из собственного deploy-скрипта MonGuard для платформы
        Geely OneOS/NewEra (install_newera.py: install_apk_via_shell) —
        некоторые сборки на этой платформе иначе не дают тихо поставить APK
        через голый adb (либо блокируют, либо всплывает системный диалог
        подтверждения, требующий тапа по экрану самой магнитолы). -t — тестовые
        пакеты, -g — сразу выдать все runtime-разрешения из манифеста
        (аналог pm install --grant), -r — переустановка поверх существующей
        версии. Если флаг -i отклонён (Unknown option/INVALID_INSTALLER —
        старые сборки pm его не знают), автоматически откатывается на
        обычный "pm install -t -g -r" без подмены — тот же приём, что и в
        оригинале."""
        self.check_cancelled()
        path = Path(path)
        remote_path = f"{remote_dir.rstrip('/')}/{path.name}"
        self.log(f"Установка APK (adb push + pm install -i, Geely OneOS/NewEra): {path.name}")
        self.push(path, remote_path)
        extra = (" " + " ".join(extra_args)) if extra_args else ""
        quoted_remote_path = shlex.quote(remote_path)
        result = self.shell(f"pm install -i com.android.packageinstaller -t -g -r {quoted_remote_path}{extra}", check=False)
        text = ((result.stdout or "") + (result.stderr or "")).strip().lower()
        if "unknown option" in text or "invalid_installer" in text or "invalid installer" in text:
            self.log("Флаг -i отклонён этой прошивкой — пробую pm install без подмены установщика")
            result = self.shell(f"pm install -t -g -r {quoted_remote_path}{extra}", check=False)
        _check_pm_install_result(result)

    def install_apk_haval_revived(self, path, extra_args=None) -> None:
        """"adb install" с флагами -g -t -d --install-reason 64 — см.
        _HAVAL_REVIVED_EXTRA_ARGS выше за обоснованием и происхождением
        флагов. В отличие от localinstall/dex_shell (отдельный протокол
        через app_process), это обычный install_apk — просто с другим
        набором флагов, поэтому реализован тонкой обёрткой поверх него."""
        flags = list(_HAVAL_REVIVED_EXTRA_ARGS)
        if extra_args:
            flags += list(extra_args)
        self.install_apk(path, extra_args=flags)

    def install_apk_jdwp_whitelist(self, path, remote_dir="/data/local/tmp", extra_args=None) -> None:
        """Магнитолы Desay Semidrive x9h (Haval Jolion 2026 / TR01025, GWM Poer 2026 / TR4314 и родня):
        прошивка запущена с ro.debuggable=1 и блокирует установку сторонних APK белым списком пакетов в
        PackageManagerService.mInstallWhiteList — обычный pm install отдаёт INSTALL_FAILED_ABORTED / -115
        (см. лог #364). Способ (по мотивам открытого DesayInstall): по JDWP добавляем имя пакета в этот
        список в памяти, затем pm install. Патч живёт до перезагрузки; установленное приложение остаётся.
        Реализация — свой минимальный JDWP-клиент (app/jdwp_whitelist.py), без полноценного JDK. На других
        магнитолах способ отваливается чисто: либо adb forward jdwp: не проходит (процесс не отлаживается),
        либо в system_server нет поля mInstallWhiteList."""
        from .jdwp_whitelist import JdwpError

        self.check_cancelled()
        path = Path(path)
        package = read_package_name(path)
        if not package:
            raise AdbError(f"не удалось прочитать имя пакета {path.name} — нужно для JDWP-патча белого списка")
        remote_path = f"{remote_dir.rstrip('/')}/{path.name}"
        self.log(f"Установка APK (JDWP-патч белого списка, Desay x9h): {path.name} [{package}]")
        try:
            self._jdwp_whitelist_packages([package])
        except JdwpError as exc:
            raise AdbError(f"JDWP-патч белого списка не удался: {exc}")
        self.push(path, remote_path)
        extra = (" " + " ".join(extra_args)) if extra_args else ""
        result = self.shell(f"pm install -r -t {shlex.quote(remote_path)}{extra}", check=False)
        _check_pm_install_result(result)

    def _jdwp_whitelist_packages(self, packages: list[str]) -> None:
        """pidof system_server → adb forward tcp:0 jdwp:<pid> → JDWP-патч mInstallWhiteList → снять forward.
        forward на порт 0 просит adb выбрать свободный порт (печатает его) — без коллизий с чужими forward."""
        import socket as _socket

        from .jdwp_whitelist import JdwpClient, patch_whitelist

        pid_result = self.shell("pidof system_server", check=False)
        pid = ((pid_result.stdout or "").strip().split() or [""])[0]
        if not pid.isdigit():
            raise AdbError("не удалось получить PID system_server (магнитола не даёт pidof?)")
        forward = self._adb.run("forward", "tcp:0", f"jdwp:{pid}", check=False)
        port_text = (forward.stdout or "").strip()
        if not port_text.isdigit():
            err = ((forward.stderr or "") + (forward.stdout or "")).strip()
            raise AdbError(f"не удалось пробросить JDWP-порт (процесс не отлаживается?): {err or 'нет порта'}")
        port = int(port_text)
        try:
            self.log(f"JDWP: system_server(pid {pid}) проброшен на localhost:{port}, патчу белый список...")
            sock = _socket.create_connection(("127.0.0.1", port), timeout=15)
            try:
                patch_whitelist(JdwpClient(sock), packages, log=self.log)
            finally:
                sock.close()
        finally:
            self._adb.run("forward", "--remove", f"tcp:{port}", check=False)

    def _installed_packages(self) -> set[str]:
        result = self.shell("pm list packages", check=False)
        return {
            line.strip()[len("package:"):].strip()
            for line in (result.stdout or "").splitlines()
            if line.strip().startswith("package:")
        }

    def install_apk_localinstall(self, path) -> None:
        """Установка через helper cars/_shared/chery_localinstall.apk —
        см. _LOCALINSTALL_HELPER_NAME выше и install_apk_auto за тем, когда
        этот способ пробуется. APK заранее не подписан известным именем
        пакета (aapt в проекте не используется), поэтому имя пакета для
        последующей выдачи разрешений определяется сравнением списка
        установленных пакетов до/после — тот же запасной способ, что
        предлагает и сам instuction.md проекта-источника."""
        self.check_cancelled()
        if not self.shared_dir:
            raise AdbError("cars/_shared недоступна — localinstall.apk не найден")
        helper = self.shared_dir / _LOCALINSTALL_HELPER_NAME
        if not helper.is_file():
            raise AdbError(f"{_LOCALINSTALL_HELPER_NAME} не найден в cars/_shared")
        path = Path(path)
        self.log(f"Установка APK (app_process + localinstall, Chery DesaySV): {path.name}")
        before = self._installed_packages()
        self.push(path, _LOCALINSTALL_REMOTE_APK)
        self.shell(f"chmod 644 {_LOCALINSTALL_REMOTE_APK}", check=False)
        self.push(helper, _LOCALINSTALL_REMOTE_HELPER)
        self.shell(f"chmod 644 {_LOCALINSTALL_REMOTE_HELPER}", check=False)
        result = self.shell(
            f"CLASSPATH={_LOCALINSTALL_REMOTE_HELPER} app_process /system/bin LocalInstall {_LOCALINSTALL_REMOTE_APK}",
            check=False)
        self.sleep(2)
        after = self._installed_packages()
        self.shell(f"rm -f {_LOCALINSTALL_REMOTE_APK} {_LOCALINSTALL_REMOTE_HELPER}", check=False)

        new_packages = after - before
        if len(new_packages) != 1:
            text = ((result.stdout or "") + (result.stderr or "")).strip()
            raise AdbError(text or "localinstall не подтвердил успех (пакет не появился в списке)")
        package = next(iter(new_packages))
        self._last_diff_package = package
        self._grant_all_permissions_if_available(package)
        self.shell(f"am force-stop {package}", check=False)
        self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1", check=False)

    def install_apk_dex_shell(self, path) -> None:
        """Установка через helper cars/_shared/dex_shell_helper.dex — см.
        _DEX_SHELL_HELPER_NAME выше за обоснованием и происхождением файла.
        Основная проверка успеха — сравнение списка пакетов до/после, та же
        логика, что и install_apk_localinstall (разбор самого .dex не дал
        понятного маркера успеха в общем случае, надёжнее не привязываться к
        точному формату его вывода как к ЕДИНСТВЕННОМУ признаку). Но для
        случая "0 новых пакетов" (переустановка уже стоящего APK — см. ветку
        ниже) diff в принципе не может отличить успех от провала, раз имя
        пакета заранее неизвестно — там ДОПОЛНИТЕЛЬНО проверяется явная
        строка "Success" в выводе (см. пояснение у самой проверки)."""
        self.check_cancelled()
        if not self.shared_dir:
            raise AdbError("cars/_shared недоступна — dex_shell_helper.dex не найден")
        helper = self.shared_dir / _DEX_SHELL_HELPER_NAME
        if not helper.is_file():
            raise AdbError(f"{_DEX_SHELL_HELPER_NAME} не найден в cars/_shared")
        path = Path(path)
        remote_apk = f"/data/local/tmp/{path.name}"
        # shlex.quote — без него APK с пробелом в имени (реальный случай:
        # "Back Button - Anywhere_2.0.7_APKPure.apk") ломает вызов
        # app_process: устройство подставляет путь прямо в свою shell-строку,
        # которая режет его по пробелам ДО того, как MonjiShellInstaller
        # успевает его увидеть целиком (java.lang.IllegalArgumentException:
        # APK not found: /data/local/tmp/Back).
        quoted_remote_apk = shlex.quote(remote_apk)
        self.log(f"Установка APK (app_process + dex-хелпер, Geely OneOS): {path.name}")
        before = self._installed_packages()
        self.push(path, remote_apk)
        self.shell(f"chmod 644 {quoted_remote_apk}", check=False)
        self.push(helper, _DEX_SHELL_REMOTE_HELPER)
        self.shell(f"chmod 644 {_DEX_SHELL_REMOTE_HELPER}", check=False)
        result = self.shell(
            f"CLASSPATH={_DEX_SHELL_REMOTE_HELPER} app_process /data/local/tmp {_DEX_SHELL_ENTRY_CLASS} "
            f"{quoted_remote_apk} --flags {hex(_DEX_SHELL_INSTALL_FLAGS)}",
            check=False)
        self.sleep(2)
        after = self._installed_packages()
        self.shell(f"rm -f {quoted_remote_apk} {_DEX_SHELL_REMOTE_HELPER}", check=False)

        text = ((result.stdout or "") + (result.stderr or "")).strip()
        new_packages = after - before
        if len(new_packages) == 1:
            package = next(iter(new_packages))
            self._last_diff_package = package
        elif not new_packages and any(line.strip() == "Success" for line in text.splitlines()):
            # Пакет уже стоял ДО этой попытки (повторная установка того же
            # APK — например после разрыва/переподключения ADB очередь
            # начинает текущее приложение заново) — diff по pm list packages
            # тогда честно не находит НИЧЕГО нового, хотя сам monji явно
            # подтвердил успех отдельной строкой "Success" (реальный случай,
            # см. память feedback_dex_shell_reinstall_false_failure — техник
            # был вынужден вручную остановить очередь, потому что она
            # проваливалась в перебор ВСЕХ способов на уже установленном
            # приложении, а они на этой платформе все отклоняются). Строка
            # "Success"/"Failure status=N message=..." — стандартный вывод
            # Android PackageInstaller session-commit, тот же формат что и у
            # штатного `pm install-commit`, а не что-то специфичное для
            # dex_shell_helper.dex — используем её только как ДОПОЛНИТЕЛЬНЫЙ
            # признак именно для случая "0 новых пакетов", реальная первая
            # установка по-прежнему проверяется diff'ом выше, без изменений.
            self.log(f"{path.name} уже был установлен, monji подтвердил успех повторной установки.")
            return
        else:
            _raise_if_version_downgrade(text)
            raise AdbError(text or "dex-хелпер не подтвердил успех (пакет не появился в списке)")
        self._grant_all_permissions_if_available(package)
        self.shell(f"am force-stop {package}", check=False)
        self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1", check=False)

    def _load_shared_module(self, name: str):
        """Модуль из cars/_shared (adb_permissions.py и т.п.) — тот же приём
        через sys.path, что и в сгенерированных stages.py; None, если папки
        нет или модуля нет на этой копии."""
        if not self.shared_dir:
            return None
        shared_str = str(self.shared_dir)
        if shared_str not in sys.path:
            sys.path.insert(0, shared_str)
        try:
            return __import__(name)
        except ImportError:
            return None

    def _grant_all_permissions_if_available(self, package: str) -> None:
        """cars/_shared/adb_permissions.py уже умеет выдавать все нужные
        разрешения/appops по имени пакета (используется "actions"-этапами
        моделей) — переиспользуем её и здесь вместо дублирования той же
        логики в app/, раз cars/_shared гарантированно синхронизирована на
        клиент (см. content_sync.py:sync_scripts). Второй раз на тот же
        пакет в одном запуске не выдаёт (см. _granted_packages). Сбой — только
        в лог, кроме «Стоп» от техника (InstallCancelled)."""
        if package in self._granted_packages:
            return
        module = self._load_shared_module("adb_permissions")
        if module is None:
            return
        self._granted_packages.add(package)
        try:
            module.grant_all_permissions(self, package)
        except InstallCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - выдача разрешений не должна ронять установку
            self.log(f"Не удалось выдать разрешения {package}: {exc}. Приложение установлено — "
                     "разрешения можно выдать вручную на этапе «Доп. действия».")

    def _set_mock_location_if_available(self, package: str) -> None:
        module = self._load_shared_module("adb_permissions")
        if module is None:
            return
        self.log(f"Выдаю фиктивное местоположение приложению {package}…")
        try:
            module.set_mock_location_app(self, package)
        except InstallCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            self.log(f"Не удалось выдать фиктивное местоположение {package}: {exc}.")

    def uninstall(self, package, check=False):
        return self._adb.uninstall(package, check=check)

    def push(self, local, remote, timeout=180):
        self.check_cancelled()
        return self._adb.push(local, remote, timeout=timeout)

    def pull(self, remote, local, timeout=180):
        self.check_cancelled()
        return self._adb.pull(remote, local, timeout=timeout)

    def reboot(self, wait=True, boot_timeout=120):
        self.log("Перезагрузка устройства...")
        self._adb.reboot()
        if wait:
            self.check_cancelled()
            self._adb.wait_for_device()
            self._adb.wait_boot_completed(timeout=boot_timeout)

    def wait_for_device(self, timeout=90):
        self._adb.wait_for_device(timeout=timeout)
