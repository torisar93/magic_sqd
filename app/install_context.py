"""Объект ctx, передаваемый в install.py каждой модели."""
from __future__ import annotations
import hashlib
import re
import shlex
import shutil
import struct
import sys
import tempfile
import time
import zipfile
from pathlib import Path

from .adb_utils import Adb, AdbError
from .apk_check import file_problem, rejection_message
from .apk_package import read_package_name
from .scanner import read_apk_mock_location
from .uninstall_helper import HELPER_NAME as UNINSTALL_HELPER_NAME, uninstall_via_helper


class InstallCancelled(RuntimeError):
    """Пользователь нажал "Стоп"."""


# Подписи для лога/сообщения об ошибке — по индексу в install_apk_auto ниже.
_INSTALL_METHOD_LABELS = ("adb install", "adb push + pm install", "adb push + pm install -S (поток)",
                          "app_process + localinstall.apk (Chery DesaySV)",
                          "adb push + pm install -i (подмена установщика, Geely OneOS/NewEra)",
                          "app_process + dex-хелпер (PackageInstaller.Session, Geely OneOS)",
                          "JDWP-патч белого списка + pm install (Desay x9h — Haval Jolion 2026)",
                          "в системную папку /system/app (adb root + remount, BAIC U5 Plus)")
# Те же способы, но короткими устойчивыми ключами — хранятся в
# StepSpec.apps_install_method/_wizard_spec.json/stages.py (см.
# car_generator.py) как явная подсказка "начни перебор с этого способа",
# когда автор модели уже знает, какой из них рабочий на этой магнитоле
# (не жёсткая привязка — если он всё-таки не сработает, install_apk_auto
# просто пойдёт дальше по остальным способам в обычном порядке).
INSTALL_METHOD_KEYS = ("adb_install", "pm_install", "pm_install_stream", "localinstall", "pm_install_spoofed",
                        "dex_shell_install", "jdwp_whitelist", "system_app")

# «В системную папку» (BAIC U5 Plus, владелец 2026-09-27): APK не ставится через PackageManager, а кладётся в
# /system/app после adb root + disable-verity + remount (см. install_apk_system_app). Только если модель выбрала
# его сама и без запасных способов: на других магнитолах программа не должна открывать системный раздел на
# запись, а обычная установка на этой не даст того, что нужно модели.
_SYSTEM_APP_METHOD = INSTALL_METHOD_KEYS.index("system_app")
_EXCLUSIVE_METHODS = frozenset({_SYSTEM_APP_METHOD})
# Свой способ установки модели (владелец, 2026-10-06: «чтобы обновлений приложения стало поменьше»):
# apps_install_method "py:<модуль>.<функция>" — функция(ctx, apk, remote) из cars/_shared ставит APK сама (remote —
# путь уже залитого на магнитолу файла или None). Тот же код исполняет Android (InstallEngine.kt → py_runner.py), так
# что новый способ для новой магнитолы приходит с каталогом, без выпуска программы. Только он, без перебора остальных
# (как system_app). Вышедшие до него версии такой ключ не узнают и перебирают способы как обычно.
_PY_METHOD_RE = re.compile(r"^py:([A-Za-z]\w*)\.([A-Za-z]\w*)$")


def parse_py_install_method(value) -> tuple[str, str] | None:
    """("модуль", "функция") для apps_install_method вида "py:модуль.функция", иначе None."""
    match = _PY_METHOD_RE.match(str(value or "").strip())
    return (match.group(1), match.group(2)) if match else None
_LOCALINSTALL_METHOD = INSTALL_METHOD_KEYS.index("localinstall")
# Метка в /system/app/<пакет>/ — «поставлено программой»: по ней кнопки «Доп. действий» отличают такие
# приложения от штатных (cars/_shared/adb_permissions.py).
SYSTEM_APP_MARKER = ".magicsqd"
# Подпапка библиотек системного приложения — по набору команд процессора, как у Android (lib/arm64, lib/arm).
_ABI_TO_ISA = {"arm64-v8a": "arm64", "armeabi-v7a": "arm", "armeabi": "arm", "x86": "x86", "x86_64": "x86_64",
               "mips": "mips", "mips64": "mips64"}
_PACKAGE_NAME_RE = re.compile(r"[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+")
_REBOOT_NEEDED_RE = re.compile(r"reboot your device|now reboot", re.IGNORECASE)


def _completed_text(result) -> str:
    """stdout+stderr ответа adb shell одной строкой (для хелперов, которые разбирают текст сами)."""
    return (result.stdout or "") + (result.stderr or "")


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
_PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")


def localinstall_wait_seconds(apk_size: int) -> int:
    """Сколько ждать, пока магнитола допишет пакет после localinstall: 15 с + 1 с на каждые 3 МБ, не больше 2 мин
    (FreeZona 20 МБ — 21 с, VK Видео 167 МБ — 68 с). Копия — android AdbInstall.kt:localinstallWaitSeconds."""
    return max(15, min(120, 15 + apk_size // (3 * 1024 * 1024)))


def wait_for_package_command(package: str, seconds: int) -> str:
    """Одна команда на магнитолу: раз в 2 с проверяет «pm path», пока пакет не встанет или не выйдет время."""
    tries = (seconds + 1) // 2
    return (f"i=0; while [ $i -lt {tries} ]; do pm path {package} 2>/dev/null | grep -q '^package:' && break; "
            f"sleep 2; i=$((i+1)); done")


def localinstall_status(logcat: str) -> str:
    """Итог установки из logcat Chery-хелпера (тег LocalInstall): строка «install status=…» после последней
    «created session» — в выводе самой команды хелпер ничего не пишет, причина отказа была пустой."""
    lines = (logcat or "").splitlines()
    start = max((i for i, line in enumerate(lines) if "created session" in line), default=-1)
    if start < 0:
        return ""  # своей сессии хелпер не записал — чужой старый итог не подставляем
    for line in reversed(lines[start + 1:]):
        if "install status=" in line:
            return "install status=" + line.split("install status=", 1)[1].strip()
    return ""

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
# Часть прошивок не даёт shell флаг INSTALL_GRANT_RUNTIME_PERMISSIONS (Changan CS75 Plus, лог #1689: сессия не
# создаётся, «You need the android.permission.INSTALL_GRANT_RUNTIME_PERMISSIONS permission to use …») — тогда
# хелпер запускается ещё раз без него (0x16); разрешения программа и так выдаёт сама после установки.
_INSTALL_GRANT_RUNTIME_PERMISSIONS = 0x100
_GRANT_FLAG_DENIED = "INSTALL_GRANT_RUNTIME_PERMISSIONS permission to use"


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


# Маркер «команда на магнитоле дошла до конца» (cp … && echo …) — у shell с check=False код возврата не виден.
_STAGED_MARK = "MSQD_STAGED_OK"


class UnsuitableApk(AppInstallFailed):
    """Сам файл не встанет никаким способом: не APK, XAPK/APKS, сборка под другой процессор, нужен Android новее
    (app/apk_check.py). Перебор остальных способов бесполезен — install_selected_apks пропускает это приложение
    с понятной причиной и ставит остальные (владелец, 2026-10-01: «сразу сообщать вместо перебора способов»)."""


def _file_sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_df_free_bytes(text: str) -> int | None:
    """Свободное место из ответа df на магнитоле: toybox — «Filesystem 1K-blocks Used Available Use% Mounted on»
    (числа в КБ), старый toolbox — «Filesystem Size Used Free Blksize» (1.9G, 120.5M). None — формат не узнан."""
    rows = [line.split() for line in text.strip().splitlines() if line.strip()]
    if len(rows) < 2:
        return None
    header = [word.lower() for word in rows[0]]
    column = next((header.index(k) for k in ("available", "avail", "free") if k in header), None)
    if column is None or len(rows[-1]) <= column:
        return None
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([KMGT]?)", rows[-1][column], re.IGNORECASE)
    if not match:
        return None
    number, unit = float(match.group(1)), match.group(2).upper()
    if not unit:
        return int(number * 1024)  # и у toybox, и у toolbox без суффикса — килобайты
    return int(number * 1024 ** "KMGT".index(unit) * 1024)


def _stored_page_aligned(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bool:
    """.so лежит в APK несжатым и с данными на границе страницы 4096 — Android загрузит его прямо из APK."""
    if info.compress_type != zipfile.ZIP_STORED:
        return False
    zf.fp.seek(info.header_offset)
    header = zf.fp.read(30)
    if len(header) != 30 or header[:4] != b"PK\x03\x04":
        return False
    name_len, extra_len = struct.unpack("<HH", header[26:30])
    return (info.header_offset + 30 + name_len + extra_len) % 4096 == 0


class NewerVersionInstalled(RuntimeError):
    """На магнитоле уже стоит версия новее, чем в сборке (INSTALL_FAILED_VERSION_DOWNGRADE). Раньше это
    останавливало весь этап, и после перезапуска техник заново ставил всё, что уже встало (лог #968:
    19 приложений по второму кругу). Владелец (2026-09-25): «если стоит более новая — пропускаем» —
    install_selected_apks пропускает установку, выдаёт разрешения уже стоящей версии и идёт дальше."""


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
# Тексты ниже узнаёт окно «что сделать» (app/web/frontend/js/components/user_errors.js).
_DEVICE_UNAUTHORIZED_RE = re.compile(r"\bdevice unauthorized\b", re.IGNORECASE)
_DEVICE_GONE_RE = re.compile(r"no devices/emulators found|device '[^']*' not found|device not found|device offline",
                             re.IGNORECASE)


def device_unavailable_message(text: str, during: bool = False) -> str | None:
    """Понятный текст, если ошибка adb значит «магнитолы нет», иначе None. during — магнитола уже была на
    связи в этом запуске (проверка перед этапом прошла), значит она отключилась по ходу, а не «не подключена»."""
    if _DEVICE_UNAUTHORIZED_RE.search(text):
        return ("Магнитола не разрешила отладку по USB — подтвердите запрос «Разрешить отладку» на её экране "
                "и запустите этап заново. (adb: device unauthorized)")
    match = _DEVICE_GONE_RE.search(text)
    if not match:
        return None
    if during:
        return ("Магнитола отключилась во время установки — дальше ничего не ставилось. Проверьте кабель "
                f"(или Wi-Fi), подключитесь заново и запустите этап ещё раз. (adb: {match.group(0)})")
    return ("Магнитола не подключена — установка не начиналась. Подключите её кабелем (или по Wi-Fi), "
            f"проверьте отладку по USB и запустите этап заново. (adb: {match.group(0)})")


def check_device(adb: Adb) -> str | None:
    """Магнитола на связи? Одна команда `adb get-state` ДО установки и команд этапа: без неё программа
    перебирала все способы установки с «no devices/emulators found» на каждом приложении (логи #734, #736,
    #737, #786, #789 — техник ответил «Продолжить всё равно» без выбранной магнитолы). Отвечает сам
    adb-сервер, магнитолу команда не трогает. Возвращает понятный текст, если магнитолы нет; None — если
    она на связи или проверить не удалось (нет adb, таймаут): тогда этап идёт как раньше и сам покажет ошибку."""
    try:
        result = adb.run("get-state", check=False, timeout=20)
    except AdbError:
        return None
    if result.returncode == 0:
        return None
    return device_unavailable_message((result.stdout or "") + (result.stderr or ""))


def _short_reason(exc, limit: int = 300) -> str:
    """Причина отказа одной строкой для лога: без переводов строк, не длиннее limit.
    Сообщение AdbError начинается с длинной команды (полный путь к adb, файлы),
    а сама причина — в конце, поэтому при обрезке оставляем ХВОСТ."""
    text = " ".join(str(exc).split())
    return text if len(text) <= limit else "…" + text[-(limit - 1):]


class InstallContext:
    def __init__(self, adb_path, device_serial, model_dir: Path, selected_apks,
                 log_fn, cancel_flag, ask_input_fn=None, shared_dir: Path | None = None,
                 preferred_install_method: str = "", on_apk_progress=None, device_confirmed: bool = False):
        # on_apk_progress(путь, готово, всего, "running"/"done"/"error", фаза|None[, (шаг, шагов)]) — ход
        # установки каждого выбранного APK для очереди окна установки (см. install_selected_apks); шаги —
        # только у фазы grant (выдача разрешений, см. permission_progress).
        self._on_apk_progress = on_apk_progress or (lambda path, completed, total, state, phase, steps=None: None)
        # Строка окна установки, которую сейчас обрабатывает install_selected_apks: (путь, индекс, всего).
        # Выдача разрешений (в том числе внутри localinstall/dex_shell) показывает по ней фазу «Выдача
        # разрешений»; вне очереди (кнопка «Выдать разрешения» в «Доп. действиях») — None, окна установки нет.
        self._progress_item: tuple[str, int, int] | None = None
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
        # Уже переподписанные в этом запуске: исходник → (копия, её размер и mtime_ns). Проверка «этот же файл
        # уже стоит» переподписывает раньше установки — install_apk_auto берёт готовую копию, если её с тех пор
        # не перезаписал другой APK с тем же именем файла.
        self._resigned: dict[Path, tuple[Path, int, int]] = {}
        # Подсказка "начни перебор с этого способа" (см. StepSpec.
        # apps_install_method в car_generator.py) — только меняет ПОРЯДОК
        # попыток в install_apk_auto, не пропускает остальные способы, если
        # автор модели ошибся. "" или незнакомый ключ — обычный порядок.
        self._preferred_method: int | None = (
            INSTALL_METHOD_KEYS.index(preferred_install_method)
            if preferred_install_method in INSTALL_METHOD_KEYS else None
        )
        # Свой способ модели из cars/_shared ("py:модуль.функция", см. parse_py_install_method) — вместо перебора.
        self._py_method = parse_py_install_method(preferred_install_method)
        self._py_method_worked = False
        # Пакеты, которым уже выдали разрешения в этом запуске — чтобы
        # инлайн-выдача в localinstall/dex_shell и общая после установки
        # (_after_app_installed) не сработали дважды на одном приложении.
        self._granted_packages: set[str] = set()
        # Файлы, уже лежащие на самой магнитоле (локальный путь → путь на ней): push копирует их там же, без передачи
        # по сети, а install_apk ставит через pm install — см. optimize_for_motion.
        self._prestaged: dict[str, str] = {}
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
        self.apps_ok = 0  # сколько приложений списка встало или уже стояло — для итога «не установлено ничего»
        # Приложения, поставленные в этом запуске ({"package", "name", "path"}): окно итога предлагает
        # «Откатить в сток» — удалить ровно их (владелец, 2026-09-25). Пропущенные «уже стоит новее» — не наши.
        self.installed_apps: list[dict] = []
        # Магнитола уже подтверждена на связи в этом запуске (check_device перед этапом — см.
        # runner.py — или require_device ниже): тогда «нет устройства» дальше значит «отключилась».
        self._device_confirmed = device_confirmed
        # Способ «в системную папку» (install_apk_system_app): раздел уже открыт на запись в этом запуске;
        # сколько приложений записано (им разрешения — после перезагрузки, отдельным этапом); ABI магнитолы.
        self._system_rw_ready = False
        self._system_apps_written = 0
        self._device_abis: list[str] | None = None

    # --- служебное -------------------------------------------------
    def log(self, message):
        self._log_fn(str(message))

    def check_cancelled(self):
        if self._cancel_flag.is_set():
            raise InstallCancelled("Установка остановлена пользователем.")

    def require_device(self):
        """Остановить этап понятной фразой, если магнитолы нет (см. check_device) — до первой команды
        или установки, а не после перебора всех способов."""
        self.check_cancelled()
        if self._device_confirmed:
            return
        message = check_device(self._adb)
        if message:
            raise InstallCancelled(message)
        self._device_confirmed = True

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

    def uninstall_via_helper(self, package: str) -> bool:
        """Для cars/_shared/adb_permissions.py: uninstall_app (кнопка «Удалить приложение») — прошивка не пускает
        pm uninstall («error: closed»), удаляем dex-хелпером (app/uninstall_helper.py). True — удалено; причину отказа
        пишет в лог. Файл adb_permissions.py приходит с сервера и в старые версии — там метода нет, он проверяет
        его наличие сам."""
        if self.shared_dir is None:
            return False
        self.log("Магнитола не пускает pm uninstall — удаляю через dex-хелпер...")
        removed, reason = uninstall_via_helper(
            self.push, lambda command: _completed_text(self.shell(command, check=False, timeout=90)),
            self.shared_dir / UNINSTALL_HELPER_NAME, package)
        if not removed:
            self.log(f"dex-хелпер не удалил: {reason}")
        return removed

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
        staged = self._prestaged.get(str(path))
        if staged:
            # Файл уже на магнитоле (optimize_for_motion): adb install снова тянул бы его по сети — ставим с её диска.
            args = " ".join(["-r"] * reinstall + [shlex.quote(a) for a in (extra_args or [])])
            result = self.shell(f"pm install {args} {shlex.quote(staged)}", check=False, timeout=timeout)
            text = _completed_text(result)
            if "Success" not in text:
                _raise_if_version_downgrade(text)
                raise AdbError(f"pm install (файл уже на магнитоле) не вернул Success: {text[:300]}")
            return result
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
                digests = {_file_sha256(f) for f in files}
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
        # Магнитолы нет — ни одного «Установка APK…»: сразу окно «Магнитола не подключена».
        self.require_device()
        mock_target = self._mock_location_target()
        # Очередь окна установки (progress08.js) — та же последовательность, что шлёт Android
        # (InstallEngine.installApksWithProgress): «устанавливается» (готово = index) → «готово»
        # (index + 1) или «ошибка». Раньше десктоп слал только скачивание, и после него окно так и
        # оставалось на «Скачивание приложений» с «Готово 0 из N» (жалоба владельца, 2026-09-23).
        total = len(self.selected_apks)
        try:
            for index, apk in enumerate(self.selected_apks):
                self._last_diff_package = None
                self._progress_item = (str(apk), index, total)
                self._on_apk_progress(str(apk), index, total, "running", "install")
                if self._same_apk_already_installed(apk):
                    # Этап повторяют после «Файл не скачан» или обрыва связи — уже поставленное не ставим заново
                    # (владелец, 2026-09-27); разрешения выдаём, как и после установки.
                    self.log(f"«{apk.name}»: на магнитоле уже стоит этот же файл — установку пропускаю.")
                    self._finish_app(apk, index, total, apk == mock_target, installed_now=False)
                    continue
                try:
                    self.install_apk_auto(apk, extra_args=extra_args)
                except NewerVersionInstalled:
                    # Приложение на магнитоле уже есть (версия новее) — не стоп, а пропуск; разрешения
                    # выдаём уже стоящей версии, как после установки (владелец, 2026-09-25).
                    self.log(f"«{apk.name}»: на магнитоле уже стоит версия новее — установку пропускаю.")
                    self._finish_app(apk, index, total, apk == mock_target, installed_now=False)
                    continue
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
                self._finish_app(apk, index, total, apk == mock_target)
        finally:
            self._progress_item = None
        if self._system_apps_written:
            self.log("Приложения записаны в системную папку. Android увидит их после перезагрузки магнитолы — "
                     "тогда им можно выдать разрешения (кнопка «Выдать разрешения установленным приложениям»).")
        if self.failed_apps and not self.apps_ok:
            self.log("Не установлено: " + "; ".join(self.failed_apps))
        elif self.failed_apps:
            self.log("Не установлено (пропущено, остальные приложения из списка "
                      "установлены): " + "; ".join(self.failed_apps))

    def _finish_app(self, apk: Path, index: int, total: int, give_mock_location: bool,
                    installed_now: bool = True) -> None:
        """Разрешения — ещё часть строки окна установки: «Готово» только после них. Раньше строка закрывалась
        до выдачи, и на последнем приложении кольцо показывало «Все приложения установлены», пока разрешения
        ещё шли — техник не понимал, зависла ли программа (владелец, 2026-09-28). Стоп посреди выдачи — строка
        всё равно «Готово»: приложение уже стоит."""
        self.apps_ok += 1
        try:
            self._after_app_installed(apk, give_mock_location, installed_now=installed_now)
        finally:
            self._on_apk_progress(str(apk), index + 1, total, "done", None)

    def _same_apk_already_installed(self, apk: Path) -> bool:
        """На магнитоле уже стоит РОВНО этот файл: SHA-256 base.apk установленного пакета совпадает с нашим.
        Раньше после «Файл не скачан» или обрыва связи этап повторяли, и все уже поставленные приложения
        ставились заново (лог #1347: WiFi Manager и Back Button — по три раза). Сравниваем файл, а не
        versionCode: у модов номер версии обычно как у оригинала, и совпадение по версии оставило бы на
        магнитоле не ту сборку. Любая неясность (нет sha256sum на старой прошивке, приложение из нескольких
        файлов, pm не отвечает) — ставим, как раньше.

        У моделей с переподписью (Changan) на магнитоле стоит переподписанная копия — сравниваем с ней (apksigner с
        RSA-ключом каждый раз даёт один и тот же файл; Android так и делал). Раньше сравнивался исходник, совпадения
        не было, и повтор этапа ставил приложение заново: localinstall поверх установленного не ставит, и на
        CS75 Plus всё кончалось «ни одним из способов» (лог #1689)."""
        package = read_package_name(apk)
        if not package:
            return False
        try:
            listed = self.shell(f"pm path {package}", check=False, timeout=30).stdout or ""
            paths = [line.strip()[len("package:"):] for line in listed.splitlines()
                     if line.strip().startswith("package:")]
            if len(paths) != 1 or not paths[0] or any(ch.isspace() for ch in paths[0]):
                return False
            remote = (self.shell(f"sha256sum {paths[0]}", check=False, timeout=120).stdout or "").split()
            return bool(remote) and remote[0].lower() == _file_sha256(self._maybe_resign(apk))
        except (AdbError, OSError):
            return False

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

    def _after_app_installed(self, apk: Path, give_mock_location: bool, installed_now: bool = True) -> None:
        """После КАЖДОГО успешно установленного приложения (любым способом
        и по любому подключению — сюда приходят все семь способов через
        install_apk_auto) выдаёт ему все разрешения, а помеченному GPS-
        приложению — ещё и фиктивное местоположение. Ошибка выдачи не должна
        срывать установку: приложение уже стоит, разрешения можно выдать
        вручную на этапе «Доп. действия» — поэтому любой сбой (кроме
        «Стоп» от техника) только пишется в лог."""
        if installed_now and self._install_method == _SYSTEM_APP_METHOD:
            # Приложение из /system/app Android увидит только после перезагрузки, до неё разрешения не выдать
            # («Unknown package»). А ADB на BAIC U5 Plus перезагрузку не переживает — разрешения выдаёт
            # отдельный этап после неё (adb_permissions.grant_system_apps_permissions); фиктивное
            # местоположение он возьмёт из метки. В «Откатить в сток» не попадает: pm uninstall системное не удаляет.
            self._system_apps_written += 1
            package = read_package_name(apk)
            if give_mock_location and package:
                self.shell(f"echo 'magicsqd mock_location' > /system/app/{package}/{SYSTEM_APP_MARKER}", check=False)
            return
        package = read_package_name(apk) or self._last_diff_package
        if not package:
            self.log(f"Не удалось определить имя пакета «{apk.name}» — разрешения автоматически не выданы.")
            return
        if installed_now and all(app["package"] != package for app in self.installed_apps):
            self.installed_apps.append({"package": package, "name": apk.name, "path": str(apk)})
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
        name = Path(path).name  # исходное имя — после переподписи файл называется «…_resigned.apk»
        problem = file_problem(path, name)
        if problem:
            # Файл не APK — ни заливать на магнитолу, ни перебирать способы смысла нет (логи №1986, №2099).
            self.log(problem)
            raise UnsuitableApk(problem)
        path = self._maybe_resign(path)
        if self._py_method is not None:
            self._install_apk_py_auto(path, name)
            return
        if self._install_method is not None:
            try:
                self._install_with_method(self._install_method, path, extra_args)
            except SignatureMismatchError as exc:
                raise InstallCancelled(self._signature_mismatch_message(path, exc))
            except VersionDowngradeError as exc:
                raise NewerVersionInstalled(Path(path).name) from exc
            except AdbError as exc:
                label = _INSTALL_METHOD_LABELS[self._install_method]
                self.log(f"  ↳ не сработало ({label}): {_short_reason(exc)}")
                # Магнитола отвалилась — остальные приложения списка тоже не встанут.
                gone = device_unavailable_message(str(exc), during=True)
                if gone:
                    raise InstallCancelled(gone) from exc
                unsuitable = rejection_message(name, str(exc))
                if unsuitable:
                    self.log(unsuitable)
                    raise UnsuitableApk(unsuitable) from exc
                raise AppInstallFailed(f"{Path(path).name}: {_short_reason(exc, 150)}") from exc
            return
        errors = []
        if self._preferred_method in _EXCLUSIVE_METHODS:
            order = [self._preferred_method]
        else:
            order = [m for m in range(len(_INSTALL_METHOD_LABELS)) if m not in _EXCLUSIVE_METHODS]
            if self._preferred_method is not None:
                order = [self._preferred_method, *(m for m in order if m != self._preferred_method)]
        for method in order:
            try:
                self._install_with_method(method, path, extra_args)
            except SignatureMismatchError as exc:
                # Причина отказа не в способе установки, а в самом APK — дальше по
                # списку способов пробовать бессмысленно (см. VersionDowngradeError).
                raise InstallCancelled(self._signature_mismatch_message(path, exc))
            except VersionDowngradeError as exc:
                # Та же причина — не способ, а версия; приложение уже стоит (новее) —
                # не стоп, а пропуск (см. NewerVersionInstalled).
                raise NewerVersionInstalled(Path(path).name) from exc
            except AdbError as exc:
                errors.append(f"{_INSTALL_METHOD_LABELS[method]}: {exc}")
                # Причину отказа каждого способа — сразу в лог: итоговое сообщение
                # уходит только в окно результата и не попадает в лог, если
                # техник закрыл программу раньше (так и вышло в реальных логах —
                # 7 «Установка APK…» подряд и ни одной причины).
                self.log(f"  ↳ не сработало ({_INSTALL_METHOD_LABELS[method]}): {_short_reason(exc)}")
                # Причина не в способе, а в том, что магнитолы нет — остальные способы
                # упрутся в то же самое (см. device_unavailable_message).
                gone = device_unavailable_message(str(exc), during=self._device_confirmed)
                if gone:
                    raise InstallCancelled(gone) from exc
                # Отказ из-за самого файла (другой процессор, нужен Android новее, файл не читается) — остальные
                # способы упрутся в то же самое: сразу понятная причина, приложение пропускаем (логи №1942, №2087).
                unsuitable = rejection_message(name, str(exc))
                if unsuitable:
                    self.log(unsuitable)
                    raise UnsuitableApk(unsuitable) from exc
                continue
            self._install_method = method
            if method > 0 and method not in _EXCLUSIVE_METHODS:
                self.log(f"Сработал способ установки APK: {_INSTALL_METHOD_LABELS[method]} — "
                         "дальше буду использовать его же для остальных приложений.")
            return
        if self._preferred_method in _EXCLUSIVE_METHODS:
            # Один способ без перебора — его причина и есть итог (без списка «ни одним из способов»).
            raise InstallCancelled(f"«{Path(path).name}» не установлено: "
                                   + _short_reason(errors[-1].split(": ", 1)[-1], 400))
        raise InstallCancelled(
            f"Не удалось установить {Path(path).name} ни одним из способов "
            "(adb install / pm install / pm install -S / localinstall.apk):\n" + "\n".join(errors)
        )

    @staticmethod
    def _signature_mismatch_message(path, exc) -> str:
        match = re.search(r"Package (\S+) signatures", str(exc))
        what = f"приложение {match.group(1)}" if match else "приложение с тем же именем пакета"
        return (
            f"«{Path(path).name}» не установилось: на магнитоле уже стоит {what}, подписанное другим "
            "ключом (другая сборка или другое приложение с тем же пакетом) — поверх обновить нельзя. "
            "Удалите его на магнитоле вручную (Настройки → Приложения) и запустите установку заново "
            "или не выбирайте такие приложения вместе."
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
        cached = self._resigned.get(path)
        if cached is not None:
            copy, size, mtime_ns = cached
            try:
                stat = copy.stat()
                if (stat.st_size, stat.st_mtime_ns) == (size, mtime_ns):
                    return copy
            except OSError:
                pass
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
        stat = out_path.stat()
        self._resigned[path] = (out_path, stat.st_size, stat.st_mtime_ns)
        return out_path

    def _installed_apk_paths(self, package: str) -> list[str]:
        """Пути установленного APK на устройстве (pm path). У обычного приложения — один base.apk, у собранного
        из частей (split) — несколько."""
        out = (self.shell(f"pm path {package}", check=False, timeout=30).stdout or "")
        return [line.strip()[len("package:"):] for line in out.splitlines()
                if line.strip().startswith("package:") and line.strip() != "package:"]

    def _log_motion_verdict(self, package: str) -> None:
        """Приняла ли магнитола пометку — по dumpsys car_service (см. motion_patch.car_service_verdict)."""
        from . import motion_patch
        dump = self.shell("dumpsys car_service", check=False, timeout=60).stdout or ""
        self.log(motion_patch.verdict_line(motion_patch.car_service_verdict(dump, package), package, dump))

    def optimize_for_motion(self, package: str) -> None:
        """«Работа в движении» (владелец, 2026-09-30: на Haval Dargo 2026 не работает видео в движении). Снимает
        установленный APK с магнитолы, вписывает во все его окна пометку distractionOptimized=true (motion_patch),
        переподписывает нашим ключом (cars/_shared/motion_cert) и ставит заново — служба машины Android Automotive
        пускает на экран в движении только окна с этой пометкой. Подпись после правки другая, поверх не встанет,
        поэтому старое приложение удаляется, а при неудаче возвращается исходное (данные приложения при этом
        теряются — техник ставит это при настройке). У приложения из нескольких частей (split) так не сработает."""
        from . import motion_patch
        self.check_cancelled()
        if self.shared_dir is None:
            self.log("Не удалось включить работу в движении: не найдена папка cars/_shared.")
            return
        cert_dir = self.shared_dir / "motion_cert"
        def cert_ready() -> bool:
            return (cert_dir / "private.pk8").is_file() and (cert_dir / "certificate.crt").is_file()
        if not cert_ready():
            # Обычно ключ скачан при запуске (content_sync.STARTUP_SHARED_FOLDERS); если программа запускалась без
            # интернета — докачиваем сейчас (лог №1985: до 1.0.54 он не скачивался вовсе).
            from .content_sync import sync_shared_folder
            sync_shared_folder(self.shared_dir.parent.parent, "motion_cert", log=self.log)
        if not cert_ready():
            self.log("Не удалось включить работу в движении: нет ключа подписи — не удалось скачать его с сервера. "
                     "Проверьте интернет и повторите.")
            return
        self.log(f"Работа в движении: снимаю {package} с магнитолы...")
        paths = self._installed_apk_paths(package)
        if not paths:
            self.log(f"Не удалось: приложение {package} не установлено на магнитоле.")
            return
        if len(paths) > 1:
            self.log("Не удалось: приложение собрано из нескольких частей (split APK) — включить работу "
                     "в движении для него нельзя.")
            return
        base_dir = self.shared_dir.parent.parent
        work = base_dir / "motion_cache"
        work.mkdir(parents=True, exist_ok=True)
        original = work / f"{package}.apk"
        patched = work / f"{package}.motion.apk"
        signed = work / f"{package}.signed.apk"
        for stale in (original, patched, signed):
            stale.unlink(missing_ok=True)
        try:
            self.pull(paths[0], original)
            if not original.is_file() or original.stat().st_size == 0:
                self.log("Не удалось: не получилось скопировать приложение с магнитолы.")
                return
            marked = motion_patch.patch_apk(original, patched)
        except (AdbError, OSError) as exc:
            self.log(f"Не удалось снять приложение с магнитолы: {_short_reason(exc)}")
            return
        except motion_patch.MotionPatchError as exc:
            self.log(f"Не удалось пометить приложение: {exc}. Оно оставлено как было.")
            return
        if marked == 0:
            self.log("У приложения уже все окна помечены — менять ничего не нужно.")
            self._log_motion_verdict(package)
            return
        self.log(f"Помечено окон: {marked}. Переподписываю и ставлю заново...")
        from .apk_signer import ApkSignError, resign_apk
        try:
            resign_apk(base_dir, patched, cert_dir, signed)
        except ApkSignError as exc:
            self.log(f"Не удалось переподписать приложение: {exc}. Оно оставлено как было.")
            return
        # Подпись изменилась — поверх старого приложения не встанет, его придётся снять. Сначала всё нужное кладём на
        # саму магнитолу: помеченную версию (по сети — пока исходное ещё стоит) и копию исходного (на ней же, без
        # сети). Снимаем только после этого: установка и возврат идут с её диска, и обрыв связи на заливке больше не
        # оставляет магнитолу без приложения (лог №2784: Strelka HUD на Haval H3 по Wi-Fi, Android).
        signed_remote = f"/data/local/tmp/{package}.motion.apk"
        original_remote = f"/data/local/tmp/{package}.orig.apk"
        try:
            self.push(signed, signed_remote)
            backup = self.shell(f"cp {shlex.quote(paths[0])} {original_remote} && chmod 644 {signed_remote} "
                                f"{original_remote} && echo {_STAGED_MARK}", check=False, timeout=300)
        except AdbError as exc:
            self._drop_motion_staging(signed_remote, original_remote)
            self.log(f"Не удалось залить помеченную версию на магнитолу: {_short_reason(exc)}. Приложение оставлено как было.")
            return
        if _STAGED_MARK not in _completed_text(backup):
            self._drop_motion_staging(signed_remote, original_remote)
            self.log("Не удалось сохранить копию приложения на самой магнитоле — оно оставлено как было.")
            return
        self._prestaged.update({str(signed): signed_remote, str(original): original_remote})
        try:
            self.shell(f"pm uninstall {package}", check=False, timeout=120)
            self._install_method = None  # заново подобрать способ для этой установки
            try:
                self.install_apk_auto(signed)
            except (InstallCancelled, AppInstallFailed, NewerVersionInstalled, AdbError) as exc:
                self.log(f"Не удалось поставить помеченную версию: {_short_reason(exc)}. Возвращаю исходную...")
                self._install_method = None
                try:
                    self.install_apk_auto(original)
                    self.log("Исходное приложение возвращено — работа в движении не включена.")
                except (InstallCancelled, AppInstallFailed, NewerVersionInstalled, AdbError):
                    self.log(f"Не удалось вернуть исходную версию — установите {package} заново из каталога.")
                return
        finally:
            self._prestaged.pop(str(signed), None)
            self._prestaged.pop(str(original), None)
            self._drop_motion_staging(signed_remote, original_remote)
        self._granted_packages.discard(package)
        self._grant_all_permissions_if_available(package)
        self.log("Приложение переустановлено — если оно просит вход, войдите в него заново на магнитоле.")
        self._log_motion_verdict(package)

    def _drop_motion_staging(self, *remote_paths) -> None:
        try:
            self.shell("rm -f " + " ".join(remote_paths), check=False)
        except AdbError:
            pass  # связь пропала — файлы в /data/local/tmp безвредны

    def restore_wifi_features(self, features=("wifi", "drl", "arkamys")) -> None:
        """«Вернуть Wi-Fi / ДХО / Arkamys» (владелец, 2026-10-06; ГУ Desay SV NV8020/18 — Omoda C5, Chery Tiggo 4 New,
        Tenet T4, XCITE X-Cross 8 на SemiDrive X9H, Android 10; разобрано по теме 4PDA 1117181). На «урезанных»
        прошивках производитель прячет в интерфейсе Wi-Fi (пункт и плитку шторки), управление ДХО и переключатель
        звука Arkamys — методами-гейтами класса CarConfigInfoClient в com.android.systemui и com.chery.settings, а
        включение Wi-Fi ещё и игнорируется в WifiReposity.setWifiEnabled (см. wifi_patch.py). Сам стек/функции в
        прошивке целы — скрыт только UI.

        Снимаем ОБА системных APK С САМОЙ МАШИНЫ (версия всегда совпадает — готовые чужие файлы с другой версии ломают
        настройки, вплоть до «кирпича»), правим dex точечно и одной длиной, переподписываем ПУБЛИЧНЫМ платформенным
        ключом AOSP (cars/_shared/platform_cert — ГУ собрано userdebug/test-keys, поэтому этот открытый ключ подходит),
        кладём обратно в /system поверх штатных (root → disable-verity → remount, бэкап на саму машину, rm <app>/oat,
        chmod 644, chown root:root, chcon u:object_r:system_file:s0), затем перезагрузка.

        ВНИМАНИЕ: правит системный раздел — делать ТОЛЬКО по USB-кабелю. Оригиналы сохраняются на самой машине
        (путь в логе), при сбое заливки возвращаются автоматически. Особенность части прошивок: Wi-Fi подключается,
        но может не передавать данные (аппаратное ограничение ГУ)."""
        from . import wifi_patch
        from .apk_signer import ApkSignError, resign_apk
        self.check_cancelled()
        if self.shared_dir is None:
            self.log("Не удалось вернуть Wi-Fi/ДХО/Arkamys: не найдена папка cars/_shared.")
            return
        cert_dir = self.shared_dir / "platform_cert"

        def cert_ready() -> bool:
            return (cert_dir / "private.pk8").is_file() and (cert_dir / "certificate.crt").is_file()

        if not cert_ready():
            from .content_sync import sync_shared_folder
            sync_shared_folder(self.shared_dir.parent.parent, "platform_cert", log=self.log)
        if not cert_ready():
            self.log("Не удалось: нет платформенного ключа подписи (cars/_shared/platform_cert) — проверьте интернет "
                     "и повторите.")
            return

        base_dir = self.shared_dir.parent.parent
        work = base_dir / "wifi_patch_cache"
        work.mkdir(parents=True, exist_ok=True)
        targets = [("com.android.systemui", "SystemUI"), ("com.chery.settings", "Настройки")]

        # 1) снять, пропатчить и подписать всё заранее — системный раздел ещё не трогаем.
        prepared: list[tuple[str, str, str, Path]] = []  # (package, title, remote_path, signed_local)
        for package, title in targets:
            self.check_cancelled()
            paths = self._installed_apk_paths(package)
            if not paths:
                self.log(f"Пропускаю {title}: пакет {package} на магнитоле не найден.")
                continue
            if len(paths) > 1:
                self.log(f"Пропускаю {title}: собран из нескольких частей (split APK) — так патчить нельзя.")
                continue
            remote = paths[0]
            if not remote.startswith("/system/"):
                self.log(f"Пропускаю {title}: {package} стоит не в /system ({remote}).")
                continue
            original = work / f"{package}.apk"
            patched = work / f"{package}.patched.apk"
            signed = work / f"{package}.signed.apk"
            for stale in (original, patched, signed):
                stale.unlink(missing_ok=True)
            try:
                self.log(f"{title}: снимаю {package} с магнитолы...")
                self.pull(remote, original)
                if not original.is_file() or original.stat().st_size == 0:
                    self.log(f"Пропускаю {title}: не удалось скопировать с магнитолы.")
                    continue
                done = wifi_patch.patch_apk_inplace(original, patched, list(features))
            except (AdbError, OSError) as exc:
                self.log(f"Пропускаю {title}: {_short_reason(exc)}")
                continue
            except wifi_patch.WifiPatchError as exc:
                self.log(f"Пропускаю {title}: не удалось пропатчить ({exc}) — оставляю как есть.")
                continue
            if not done:
                self.log(f"{title}: нужные функции уже открыты — менять нечего.")
                continue
            self.log(f"{title}: правки — {', '.join(done)}. Переподписываю платформенным ключом...")
            try:
                resign_apk(base_dir, patched, cert_dir, signed)
            except ApkSignError as exc:
                self.log(f"Пропускаю {title}: не удалось переподписать ({exc}).")
                continue
            prepared.append((package, title, remote, signed))

        if not prepared:
            self.log("Wi-Fi/ДХО/Arkamys: менять нечего или не удалось подготовить файлы — система не тронута.")
            return

        # 2) открыть /system на запись и залить (с бэкапом на самой машине и авто-откатом при сбое).
        self._open_system_partition()
        backup_dir = "/data/local/tmp/magicsqd_wifi_backup"
        self.shell(f"mkdir -p {backup_dir}", check=False)

        def _restore(remote: str, backup: str) -> None:
            self.shell(f"cp -a {shlex.quote(backup)} {shlex.quote(remote)} && chmod 644 {shlex.quote(remote)} "
                       f"&& chcon u:object_r:system_file:s0 {shlex.quote(remote)}", check=False, timeout=300)

        for package, title, remote, signed in prepared:
            self.check_cancelled()
            folder = remote.rsplit("/", 1)[0]
            backup = f"{backup_dir}/{package}.apk"
            self.log(f"{title}: бэкап {remote} → {backup}, заливаю пропатченный...")
            try:
                self.shell(f"cp -a {shlex.quote(remote)} {shlex.quote(backup)}", check=False, timeout=300)
                self._push_to_system(signed, remote, timeout=900)
                result = self.shell(
                    f"rm -rf {shlex.quote(folder)}/oat && chmod 644 {shlex.quote(remote)} "
                    f"&& chown root:root {shlex.quote(remote)} "
                    f"&& chcon u:object_r:system_file:s0 {shlex.quote(remote)} && echo MSQD_OK",
                    check=False, timeout=120)
            except AdbError as exc:
                self.log(f"{title}: сбой заливки ({_short_reason(exc)}) — возвращаю оригинал из бэкапа.")
                _restore(remote, backup)
                self.log("Патч прерван. Перезагрузите магнитолу — интерфейс вернётся к исходному.")
                return
            if "MSQD_OK" not in _completed_text(result):
                self.log(f"{title}: не удалось выставить права/контекст — возвращаю оригинал.")
                _restore(remote, backup)
                return
            self.log(f"{title}: записано в {remote} (права 644, SELinux-контекст системный). Бэкап: {backup}.")

        self.log("Готово. Перезагружаю магнитолу, чтобы изменения вступили в силу...")
        self.shell("reboot", check=False)
        self.log(f"После перезагрузки проверьте Wi-Fi/ДХО/Arkamys. Если пропадёт часть интерфейса — верните оригиналы "
                 f"из {backup_dir} (adb push обратно в /system, chmod 644, chcon system_file) и перезагрузите.")

    def _install_apk_py(self, path) -> None:
        """Свой способ модели: функция(ctx, apk, remote) из cars/_shared. Любой её сбой — AdbError (способ не
        сработал); остановка техником и понятная остановка (InstallCancelled) проходят как есть."""
        module_name, function_name = self._py_method
        module = self._load_shared_module(module_name)
        function = getattr(module, function_name, None) if module is not None else None
        if not callable(function) or function_name.startswith("_"):
            raise AdbError(f"нет функции {module_name}.{function_name} в cars/_shared — синхронизируйте каталог")
        try:
            function(self, Path(path), self._prestaged.get(str(path)))
        except (InstallCancelled, AdbError):
            raise
        except Exception as exc:  # noqa: BLE001 — ошибка кода способа = способ не сработал, а не падение программы
            raise AdbError(f"{type(exc).__name__}: {exc}") from exc

    def _install_apk_py_auto(self, path, name: str) -> None:
        """install_apk_auto для своего способа модели: только он, как system_app; причина отказа — итог."""
        label = "свой способ модели ({})".format(".".join(self._py_method))
        try:
            self._install_apk_py(path)
        except SignatureMismatchError as exc:
            raise InstallCancelled(self._signature_mismatch_message(path, exc))
        except VersionDowngradeError as exc:
            raise NewerVersionInstalled(Path(path).name) from exc
        except AdbError as exc:
            self.log(f"  ↳ не сработало ({label}): {_short_reason(exc)}")
            gone = device_unavailable_message(str(exc), during=self._device_confirmed)
            if gone:
                raise InstallCancelled(gone) from exc
            unsuitable = rejection_message(name, str(exc))
            if unsuitable:
                self.log(unsuitable)
                raise UnsuitableApk(unsuitable) from exc
            if self._py_method_worked:
                # Способ на этой магнитоле уже ставил приложения — не встало только это (как AppInstallFailed выше).
                raise AppInstallFailed(f"{Path(path).name}: {_short_reason(exc, 150)}") from exc
            raise InstallCancelled(f"«{Path(path).name}» не установлено: " + _short_reason(exc, 400)) from exc
        self._py_method_worked = True

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
            self.install_apk_jdwp_whitelist(path, extra_args=extra_args)
        elif method == _SYSTEM_APP_METHOD:
            self.install_apk_system_app(path)
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
        except (JdwpError, OSError) as exc:
            # OSError — таймаут/обрыв сокета JDWP: это отказ способа, а не повод ронять весь этап.
            raise AdbError(f"JDWP-патч белого списка не удался: {exc or type(exc).__name__}")
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
            # Ответ adb — в текст ошибки: «no devices/emulators found» здесь раньше терялся, и этот способ
            # (первый у Haval Jolion 2026) писал «магнитола не даёт pidof?», когда магнитолы просто нет
            # (логи #786, #789).
            detail = " ".join(((pid_result.stderr or "") + " " + (pid_result.stdout or "")).split())
            raise AdbError("не удалось получить PID system_server (магнитола не даёт pidof?)"
                           + (f": {detail}" if detail else ""))
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
            # Уборка проброса — коротко и без исключений: в логе #799 «forward --remove» висел 120 с и его
            # таймаут подменил настоящую причину сбоя JDWP (приложение пропущено с «Команда не ответила»).
            try:
                self._adb.run("forward", "--remove", f"tcp:{port}", check=False, timeout=15)
            except AdbError as exc:
                self.log(f"  JDWP: не удалось снять проброс порта {port}: {_short_reason(exc, 160)}")

    def install_apk_system_app(self, path) -> None:
        """BAIC U5 Plus (владелец, 2026-09-27): приложения не ставятся через PackageManager, а кладутся в системную
        папку — adb root, adb disable-verity, adb remount, файл в /system/app, chmod 644. Своя папка
        /system/app/<пакет>/<пакет>.apk: повторная установка заменяет её целиком, дублей пакета нет. Сжатые
        нативные библиотеки — рядом, в lib/<arm|arm64>/: системному приложению Android их из APK не распаковывает,
        и без этого не запустились бы, например, Яндекс Навигатор и Кинопоиск из каталога. Метка
        SYSTEM_APP_MARKER — «поставлено программой». Android увидит приложение только после перезагрузки —
        разрешения выдаёт отдельный этап после неё (adb_permissions.grant_system_apps_permissions)."""
        self.check_cancelled()
        path = Path(path)
        package = read_package_name(path)
        if not package or not _PACKAGE_NAME_RE.fullmatch(package):
            raise AdbError(f"не удалось прочитать имя пакета {path.name} — без него некуда положить файл в /system/app")
        self._open_system_partition()
        folder = f"/system/app/{package}"
        self.log(f"Установка APK в системную папку: {path.name} → {folder}")
        libs = self._extract_native_libs(path)
        try:
            # Старая копия этого же пакета, положенная раньше вручную прямо в /system/app (так ставили .bat-файлом),
            # иначе после перезагрузки у Android было бы два приложения с одним именем.
            flat = self._flat_system_copy(package)
            self.shell(f"rm -rf {folder}" + (f" {shlex.quote(flat)}" if flat else ""), check=False)
            need = path.stat().st_size + (libs[2] if libs else 0)
            free = self._system_free_bytes()
            if free is not None and free < need + (1 << 20):
                raise AdbError(f"на системном разделе магнитолы не хватает места для «{path.name}»: нужно "
                               f"{need / (1 << 20):.0f} МБ, свободно {free / (1 << 20):.0f} МБ")
            self._push_to_system(path, f"{folder}/{package}.apk", timeout=900)
            dirs, files = [folder], [f"{folder}/{package}.apk"]
            if libs:
                isa, lib_dir, _ = libs
                dirs += [f"{folder}/lib", f"{folder}/lib/{isa}"]
                for so in sorted(lib_dir.iterdir()):
                    self._push_to_system(so, f"{folder}/lib/{isa}/{so.name}", timeout=300)
                    files.append(f"{folder}/lib/{isa}/{so.name}")
        finally:
            if libs:
                shutil.rmtree(libs[1].parent, ignore_errors=True)
        marker = f"{folder}/{SYSTEM_APP_MARKER}"
        result = self.shell(f"chmod 755 {' '.join(dirs)} && chmod 644 {' '.join(files)} && echo magicsqd > {marker} "
                            f"&& chmod 644 {marker} && echo MSQD_OK", check=False, timeout=60)
        if "MSQD_OK" not in (result.stdout or ""):
            text = ((result.stdout or "") + (result.stderr or "")).strip()
            raise AdbError(f"не удалось выставить права файлам в {folder}: {text or 'нет ответа'}")
        self.log(f"Записано в {folder}" + (f" (с библиотеками lib/{libs[0]})" if libs else "") + ", права 644.")

    def _push_to_system(self, local: Path, remote: str, timeout: int) -> None:
        """push в /system с понятной причиной: «No space left» и «Read-only file system» здесь — про память
        магнитолы, а не про флешку (окно «что сделать» узнаёт эти фразы, см. user_errors.js)."""
        try:
            self.push(local, remote, timeout=timeout)
        except AdbError as exc:
            reason = _short_reason(exc, 200)
            if "no space left" in reason.lower():
                raise AdbError(f"на системном разделе магнитолы не хватает места для «{local.name}» ({reason})") from exc
            raise AdbError(f"не удалось записать в системный раздел «{local.name}»: {reason}") from exc

    def _adb_text(self, *args, timeout=60) -> str:
        """Вывод adb-команды (stdout+stderr) — для root/disable-verity/remount, где итог только в тексте.
        Магнитолы нет — AdbError с ответом adb: install_apk_auto превратит его в «Магнитола отключилась…»."""
        self.check_cancelled()
        result = self._adb.run(*args, check=False, timeout=timeout)
        text = " ".join(((result.stdout or "") + " " + (result.stderr or "")).split())
        if device_unavailable_message(text):
            raise AdbError(text)
        return text

    def _open_system_partition(self) -> None:
        """adb root → adb disable-verity → adb remount — один раз за запуск. Если disable-verity только что
        выключил проверку раздела, она перестанет действовать лишь после перезагрузки (remount тогда пишет
        «remount succeeded», но записать в /system ничего нельзя). Перезагрузить сама программа не может: ADB на
        BAIC U5 Plus перезагрузку не переживает (владелец, 2026-09-27) — просим техника."""
        if self._system_rw_ready:
            return
        self.log("Открываю системный раздел на запись: adb root → adb disable-verity → adb remount.")
        self._adb_root()
        verity = self._adb_text("disable-verity")
        self.log(f"  adb disable-verity: {verity or '(пусто)'}")
        if _REBOOT_NEEDED_RE.search(verity):
            raise InstallCancelled(
                "Проверка системного раздела отключена, но начнёт действовать только после перезагрузки. "
                "Перезагрузите магнитолу, снова включите ADB в инженерном меню и запустите этап ещё раз.")
        remount = self._adb_text("remount")
        self.log(f"  adb remount: {remount or '(пусто)'}")
        if "remount succeeded" not in remount.lower():
            raise AdbError("системный раздел не открылся на запись (adb remount: "
                           f"{_short_reason(remount or 'нет ответа', 200)})")
        self._system_rw_ready = True

    def _adb_root(self) -> None:
        out = self._adb_text("root")
        self.log(f"  adb root: {out or '(пусто)'}")
        if "cannot run as root" in out.lower() or "root access is disabled" in out.lower():
            raise AdbError(f"магнитола не дала права root (adb root: {out})")
        # adbd перезапускается от root — ждём, пока магнитола снова на связи.
        self._wait_device(60)

    def _wait_device(self, timeout: int) -> None:
        """adb wait-for-device короткими шагами — чтобы «Стоп» срабатывал сразу, а не по истечении ожидания.
        Магнитола по Wi-Fi после перезапуска adbd сама не переподключается — зовём adb connect."""
        deadline = time.time() + timeout
        while True:
            self.check_cancelled()
            if ":" in (self.device or ""):
                try:
                    Adb(self._adb.adb_path).run("connect", self.device, check=False, timeout=10)
                except AdbError:
                    pass
            try:
                if self._adb.run("wait-for-device", check=False, timeout=10).returncode == 0:
                    return
            except AdbError:
                pass  # не дождались за 10 с — следующий шаг
            if time.time() >= deadline:
                raise AdbError(f"магнитола не вернулась по ADB за {timeout} с")
            self.sleep(1)

    def _flat_system_copy(self, package: str) -> str | None:
        """Путь уже стоящего этого пакета, если это файл прямо в /system/app (не наша папка и не штатная)."""
        listed = self.shell(f"pm path {package}", check=False, timeout=30).stdout or ""
        paths = [line.strip()[len("package:"):] for line in listed.splitlines() if line.strip().startswith("package:")]
        if len(paths) == 1 and re.fullmatch(r"/system/app/[^/\s]+\.apk", paths[0]):
            return paths[0]
        return None

    def _system_free_bytes(self) -> int | None:
        """Свободное место на разделе с /system/app (df); None — не разобрать ответ, тогда не проверяем."""
        text = self.shell("df /system/app", check=False, timeout=30).stdout or ""
        return parse_df_free_bytes(text)

    def _device_abi_list(self) -> list[str]:
        if self._device_abis is None:
            out = self.shell("getprop ro.product.cpu.abilist", check=False, timeout=30).stdout or ""
            abis = [a.strip() for a in out.strip().split(",") if a.strip()]
            if not abis:
                one = (self.shell("getprop ro.product.cpu.abi", check=False, timeout=30).stdout or "").strip()
                abis = [one] if one else []
            self._device_abis = abis or ["arm64-v8a", "armeabi-v7a", "armeabi"]
        return self._device_abis

    def _extract_native_libs(self, apk: Path) -> tuple[str, Path, int] | None:
        """Сжатые .so из APK под процессор магнитолы → (isa, локальная папка с файлами, размер). None — библиотек
        нет или все лежат несжатыми и выровненными (их Android грузит прямо из APK). Какой набор взять — как у
        самого Android: первый из ro.product.cpu.abilist, который есть в APK."""
        try:
            with zipfile.ZipFile(apk) as zf:
                by_abi: dict[str, list[zipfile.ZipInfo]] = {}
                for info in zf.infolist():
                    parts = info.filename.split("/")
                    if len(parts) == 3 and parts[0] == "lib" and parts[2].endswith(".so"):
                        by_abi.setdefault(parts[1], []).append(info)
                if not by_abi:
                    return None
                abis = self._device_abi_list()
                abi = next((a for a in abis if a in by_abi), None)
                if abi is None:
                    raise AdbError(f"в «{apk.name}» нет библиотек под процессор магнитолы ({', '.join(abis)}), "
                                   f"есть только {', '.join(sorted(by_abi))}")
                entries = by_abi[abi]
                if all(_stored_page_aligned(zf, info) for info in entries):
                    return None
                isa = _ABI_TO_ISA.get(abi, abi)
                lib_dir = self._scratch_dir("system_app_libs") / isa
                lib_dir.mkdir()
                total = 0
                for info in entries:
                    target = lib_dir / info.filename.rsplit("/", 1)[-1]
                    with zf.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst, 1 << 20)
                    total += info.file_size
                return isa, lib_dir, total
        except (OSError, zipfile.BadZipFile) as exc:
            raise AdbError(f"не удалось прочитать библиотеки из «{apk.name}»: {exc}") from exc

    def _scratch_dir(self, name: str) -> Path:
        """Пустая рабочая папка под base_dir (рядом с resign_cache — путь без не-ASCII имени пользователя
        Windows, который не всегда переваривает adb), в тестах — во временной."""
        base = self.shared_dir.parent.parent if self.shared_dir is not None else Path(tempfile.gettempdir())
        root = base / name
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        return root

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
        expected = read_package_name(path)
        wait_for = expected if expected and expected not in before and _PACKAGE_NAME_RE.match(expected) else None
        if wait_for and wait_for not in after:
            # Хелпер коммитит сессию и сразу выходит, пакет система дописывает сама: крупное приложение на Haval
            # sa8155 встаёт ~10 с (лог №2042, FreeZona 20 МБ). Раньше через 2 с его считали «не вставшим», а позднее
            # появление засчитывалось следующему приложению. Ждём именно этот пакет — на самой магнитоле.
            wait = localinstall_wait_seconds(path.stat().st_size)
            self.log(f"Магнитола ещё дописывает {wait_for} — жду до {wait} с...")
            self.shell(wait_for_package_command(wait_for, wait), check=False, timeout=wait + 30)
            after = self._installed_packages()
        self.shell(f"rm -f {_LOCALINSTALL_REMOTE_APK} {_LOCALINSTALL_REMOTE_HELPER}", check=False)

        new_packages = after - before
        if wait_for and wait_for in after:
            package = wait_for  # встал именно он; чужое позднее появление не засчитываем
        elif not wait_for and len(new_packages) == 1:
            package = next(iter(new_packages))
        else:
            text = ((result.stdout or "") + (result.stderr or "")).strip()
            # localinstall уже сработал на этой магнитоле в сеансе или задан моделью (Chery DesaySV, обновлённый
            # Haval H3): уже стоящее приложение — обновление dex-хелпером (Android так же, логи №2786, №3031).
            localinstall_expected = self._install_method == _LOCALINSTALL_METHOD or (
                self._install_method is None and self._preferred_method == _LOCALINSTALL_METHOD)
            if expected and expected in before and localinstall_expected:
                self._update_with_dex_shell(path, expected)
                return
            status = localinstall_status(_completed_text(self.shell("logcat -d -s LocalInstall", check=False)))
            reason = " ".join(part for part in (text, status) if part)
            raise AdbError(reason or "localinstall не подтвердил успех (пакет не появился в списке)")
        self._last_diff_package = package
        self._grant_all_permissions_if_available(package)
        self.shell(f"am force-stop {package}", check=False)
        self.shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1", check=False)

    def _update_with_dex_shell(self, path: Path, package: str) -> None:
        """Chery-хелпер ставит только новые приложения: флагов сессии он не задаёт, а вызывающему от shell
        PackageManager «заменить существующее» сам не добавляет — поверх установленного отказ «Attempt to
        re-install … without first uninstalling» (проверено на эмуляторе), причём в logcat, а не в вывод, и в
        логе причина пустая. На Haval sa8155 штатный VK Video так пропускался, пока не пошёл dex-хелпер (лог
        #1670). Когда localinstall на этой магнитоле уже сработал, а приложение уже стоит (обновление, штатное
        приложение), ставим его dex-хелпером — у него флаг замены есть. Отказ из-за версии или подписи уходит
        дальше как есть (пропуск «версия новее» / стоп)."""
        self.log(f"«{path.name}»: {package} уже стоит на магнитоле, а localinstall ставит только новые "
                 "приложения — обновляю через dex-хелпер.")
        try:
            self.install_apk_dex_shell(path)
        except VersionDowngradeError:
            raise
        except AdbError as exc:
            raise AdbError(f"{package} уже стоит, localinstall поверх не ставит, dex-хелпер не обновил: {exc}") from exc

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

        def run_helper(flags: int):
            return self.shell(
                f"CLASSPATH={_DEX_SHELL_REMOTE_HELPER} app_process /data/local/tmp {_DEX_SHELL_ENTRY_CLASS} "
                f"{quoted_remote_apk} --flags {hex(flags)}",
                check=False)

        result = run_helper(_DEX_SHELL_INSTALL_FLAGS)
        if _GRANT_FLAG_DENIED in (result.stdout or "") + (result.stderr or ""):
            self.log("Прошивка не даёт хелперу выдавать разрешения при установке — повторяю без этого "
                     "(разрешения выдам после установки).")
            result = run_helper(_DEX_SHELL_INSTALL_FLAGS & ~_INSTALL_GRANT_RUNTIME_PERMISSIONS)
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
        # Кольцо окна установки — «Выдача разрешений» (ход по шагам присылает сам модуль, см. permission_progress;
        # модуль старше 1.0.47 его не шлёт — тогда просто крутится).
        self._report_grant(None)
        try:
            module.grant_all_permissions(self, package)
        except InstallCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - выдача разрешений не должна ронять установку
            self.log(f"Не удалось выдать разрешения {package}: {exc}. Приложение установлено — "
                     "разрешения можно выдать вручную на этапе «Доп. действия».")

    def permission_progress(self, done: int, total: int) -> None:
        """Ход выдачи разрешений: cars/_shared/adb_permissions.py (grant_all_permissions) зовёт после каждого
        шага, если у ctx есть этот метод — в кольцо окна установки «Разрешение N из M»."""
        self._report_grant((done, total))

    def _report_grant(self, steps: tuple[int, int] | None) -> None:
        if self._progress_item is None:
            return  # не очередь установки (кнопка «Выдать разрешения») — окна с кольцом нет
        path, index, total = self._progress_item
        if steps is None:
            self._on_apk_progress(path, index, total, "running", "grant")
        else:
            self._on_apk_progress(path, index, total, "running", "grant", steps)

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
        staged = self._prestaged.get(str(local))
        if staged:
            # Уже на магнитоле (optimize_for_motion) — копируем там же, без передачи по сети.
            result = self.shell(f"cp {shlex.quote(staged)} {shlex.quote(str(remote))} && echo {_STAGED_MARK}",
                                check=False, timeout=timeout)
            if _STAGED_MARK in _completed_text(result):
                return result
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
