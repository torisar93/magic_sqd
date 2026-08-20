"""Объект ctx, передаваемый в install.py каждой модели."""
from __future__ import annotations
import time
from pathlib import Path

from .adb_utils import Adb, AdbError


class InstallCancelled(RuntimeError):
    """Пользователь нажал "Стоп"."""


# Подписи для лога/сообщения об ошибке — по индексу в install_apk_auto ниже.
_INSTALL_METHOD_LABELS = ("adb install", "adb push + pm install", "adb push + pm install -S (поток)")


def _check_pm_install_result(result) -> None:
    """pm install через adb shell (в отличие от "adb install" целиком) не
    всегда даёт надёжный код возврата — старые прошивки/adb используют shell
    протокол, который вообще не пробрасывает код завершения удалённой
    команды. Реальный результат смотрим в тексте, как выводит сама pm
    ("Success"/"Failure [...]") — тот же приём, что и везде в проекте, где
    нужно проверить именно вывод, а не полагаться на код возврата adb shell."""
    text = ((result.stdout or "") + (result.stderr or "")).strip()
    if "success" not in text.lower() or "failure" in text.lower():
        raise AdbError(text or "pm install не подтвердил успех (пустой вывод)")


class InstallContext:
    def __init__(self, adb_path, device_serial, model_dir: Path, selected_apks,
                 log_fn, cancel_flag, ask_input_fn=None, shared_dir: Path | None = None):
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

    def install_apk(self, path, reinstall=True, extra_args=None, timeout=180):
        self.check_cancelled()
        self.log(f"Установка APK: {Path(path).name}")
        return self._adb.install(path, reinstall=reinstall, extra_args=extra_args, timeout=timeout)

    def install_selected_apks(self, extra_args=None):
        for apk in self.selected_apks:
            self.install_apk_auto(apk, extra_args=extra_args)

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
        смысла."""
        self.check_cancelled()
        if self._install_method is not None:
            self._install_with_method(self._install_method, path, extra_args)
            return
        errors = []
        for method in range(len(_INSTALL_METHOD_LABELS)):
            try:
                self._install_with_method(method, path, extra_args)
            except AdbError as exc:
                errors.append(f"{_INSTALL_METHOD_LABELS[method]}: {exc}")
                continue
            self._install_method = method
            if method > 0:
                self.log(f"Сработал способ установки APK: {_INSTALL_METHOD_LABELS[method]} — "
                         "дальше буду использовать его же для остальных приложений.")
            return
        raise InstallCancelled(
            f"Не удалось установить {Path(path).name} ни одним из способов "
            "(adb install / pm install / pm install -S):\n" + "\n".join(errors)
        )

    def _install_with_method(self, method: int, path, extra_args) -> None:
        if method == 0:
            self.install_apk(path, extra_args=extra_args)
        elif method == 1:
            self.install_apk_pm(path, extra_args=extra_args)
        else:
            self.install_apk_stream(path, extra_args=extra_args)

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
        result = self.shell(f"pm install -r {remote_path}{extra}", check=False)
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
        result = self.shell(f"cat {remote_path} | pm install -S {size}{extra}", check=False)
        _check_pm_install_result(result)

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
