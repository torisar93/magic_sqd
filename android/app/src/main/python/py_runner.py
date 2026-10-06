"""Общий Python-код каталога на Android (владелец, 2026-10-06: «чтобы обновлений приложения стало поменьше»).

На ПК процедуры из cars/_shared/*.py (выдача разрешений, удаление приложения, …) исполняет сама программа: команда
«#py модуль.функция аргументы» в командах этапа или кнопки «Доп. действий» вызывает функция(ctx, *аргументы)
(app/car_generator.py). На Android для каждой такой процедуры раньше нужен был Kotlin-порт и новый выпуск приложения.
Теперь та же функция исполняется здесь, в Chaquopy: AndroidCtx ниже повторяет нужную часть ctx ПК
(app/install_context.py: shell/shell_log/log/sleep/ask_input/ask_choice/push/pull/install_apk/uninstall/reboot/
wait_for_device/…) поверх Kotlin-примитивов (PyCtxBridge.kt — тот же ADB-сеанс, что у остального приложения).

Код с сервера исполняется на телефоне техника, поэтому из cars/_shared берутся только файлы с подписью разработчика
(code_signing.py, scripts/publish_shared.py) — и сам вызываемый модуль, и всё, что он импортирует из _shared. Стандартная
библиотека и модули приложения импортируются как обычно и раньше файлов _shared (их не подменить)."""
from __future__ import annotations

import importlib.abc
import importlib.util
import json
import re
import shlex
import sys
import time
import traceback
from pathlib import Path

import code_signing
from code_signing import UnsignedCode

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FAIL = "\x00fail:"  # PyCtxBridge.FAIL: ADB-сеанс оборвался
_RC_MARK = "__MSQD_RC="
_RC_RE = re.compile(r"\n?" + re.escape(_RC_MARK) + r"(-?\d+)\s*$")


class InstallCancelled(RuntimeError):
    """Остановка с понятной фразой — как InstallCancelled на ПК (без «Ошибка:» в итоге)."""


class AdbError(RuntimeError):
    pass


class ShellResult:
    """Как subprocess.CompletedProcess на ПК (ctx.shell там возвращает его): stdout, stderr, returncode. В ADB-сеансе
    телефона stderr приходит вместе с stdout, код возврата — меткой после команды."""

    def __init__(self, args, returncode: int, stdout: str, stderr: str = ""):
        self.args = args
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    def __repr__(self) -> str:
        return f"ShellResult(returncode={self.returncode!r}, stdout={self.stdout[:80]!r})"


def split_exit_code(raw: str):
    """Вывод команды и код возврата из метки, которую AndroidCtx.shell дописывает после команды."""
    match = _RC_RE.search(raw)
    if not match:
        return raw, -1  # метки нет — сеанс оборвался посреди команды
    return raw[:match.start()], int(match.group(1))


def _text(value) -> str:
    return "" if value is None else str(value)


class AndroidCtx:
    """ctx для функций из cars/_shared на телефоне. bridge — Kotlin PyCtxBridge (в тестах — подделка с теми же
    методами): shell(command, timeoutMs) → вывод; service(name) → вывод; push/pull/installApk → None или текст ошибки;
    reboot(wait, timeoutMs) → None или текст ошибки; waitForDevice(timeoutMs) → bool; ask(prompt, title, choicesJson,
    allowManual) → ответ или None; log(text); isCancelled(); isConnected(); progress(done, total);
    uninstallViaHelper(package) → bool; optimizeForMotion(package) → None или текст ошибки."""

    platform = "android"

    def __init__(self, bridge, shared_dir, model_dir=None, files=None):
        self._bridge = bridge
        self.shared_dir = Path(shared_dir)
        self.model_dir = Path(model_dir) if model_dir else None
        self.files_dir = self.model_dir / "files" if self.model_dir else None
        # Имя файла → локальный путь (файлы этапа, уже скачанные приложением); ctx.file сначала смотрит сюда.
        self._files = dict(files or {})
        self.device = None
        self.failed_apps = []
        self.installed_apps = []

    # --- служебное ---------------------------------------------------------------------------------------------
    def log(self, message) -> None:
        self._bridge.log(_text(message))

    def check_cancelled(self) -> None:
        if self._bridge.isCancelled():
            raise InstallCancelled("Остановлено пользователем.")

    def require_device(self) -> None:
        self.check_cancelled()
        if not self._bridge.isConnected():
            raise InstallCancelled("Магнитола не подключена по ADB — подключитесь и повторите.")

    def sleep(self, seconds) -> None:
        end = time.time() + float(seconds)
        while time.time() < end:
            self.check_cancelled()
            time.sleep(min(0.3, max(0.0, end - time.time())))

    def permission_progress(self, done: int, total: int) -> None:
        self._bridge.progress(int(done), int(total))

    def file(self, relative_path) -> Path:
        name = str(relative_path).replace("\\", "/")
        local = self._files.get(name) or self._files.get(name.rsplit("/", 1)[-1])
        if local:
            return Path(local)
        if self.files_dir is None:
            raise AdbError(f"Файл {name}: у этой команды нет папки модели")
        return self.files_dir / name

    # --- ввод техника --------------------------------------------------------------------------------------------
    def ask_input(self, prompt, title="Ввод данных"):
        self.check_cancelled()
        value = self._bridge.ask(_text(prompt), _text(title), "[]", True)
        self.check_cancelled()
        if not value:
            raise InstallCancelled("Ввод отменён пользователем.")
        return str(value)

    def ask_choice(self, prompt, choices, title="Выбор", allow_manual=True):
        self.check_cancelled()
        value = self._bridge.ask(_text(prompt), _text(title), json.dumps([str(c) for c in choices], ensure_ascii=False),
                                 bool(allow_manual))
        self.check_cancelled()
        if not value:
            raise InstallCancelled("Выбор отменён пользователем.")
        return str(value)

    # --- ADB ---------------------------------------------------------------------------------------------------------
    def shell(self, command, check=True, timeout=120):
        self.check_cancelled()
        command = str(command)
        raw = _text(self._bridge.shell(f"{command}\necho {_RC_MARK}$?", int(float(timeout) * 1000)))
        if raw.startswith(_FAIL):
            raise AdbError(f"adb shell {command}: {raw[len(_FAIL):]}")
        output, code = split_exit_code(raw)
        result = ShellResult(["shell", command], code, output)
        if check and code != 0:
            detail = output.strip()
            raise AdbError(f"Команда завершилась с ошибкой ({code}): adb shell {command}" + (f"\n{detail}" if detail else ""))
        return result

    def shell_log(self, command, **kwargs):
        self.log(f"$ {command}")
        kwargs.setdefault("check", False)
        result = self.shell(command, **kwargs)
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        self.log(output if output else "(пусто)")
        return result

    def _service(self, name: str, check: bool):
        self.check_cancelled()
        output = _text(self._bridge.service(name))
        if output.startswith(_FAIL):
            raise AdbError(f"adb {name.rstrip(':')}: {output[len(_FAIL):]}")
        if check and output.startswith("error:"):
            raise AdbError(f"adb {name.rstrip(':')}: {output.strip()}")
        return ShellResult([name], 0, output)

    def adb(self, *args, check=True, timeout=120):
        """То, что из adb-команд ПК нужно cars/_shared и мини-DSL; остальное — понятная ошибка, а не тишина."""
        args = [str(a) for a in args]
        if not args:
            raise AdbError("adb без команды")
        head, rest = args[0], args[1:]
        if head == "shell":
            return self.shell(" ".join(shlex.quote(a) for a in rest) if len(rest) > 1 else (rest[0] if rest else ""),
                              check=check, timeout=timeout)
        if head in ("root", "unroot", "remount", "disable-verity", "enable-verity") and not rest:
            return self._service(f"{head}:", check)
        if head == "reboot":
            self.reboot(wait=False)
            return ShellResult(args, 0, "")
        if head == "wait-for-device":
            self.wait_for_device(timeout=timeout)
            return ShellResult(args, 0, "")
        if head == "uninstall" and rest:
            return self.uninstall(rest[-1], check=check)
        if head == "push" and len(rest) == 2:
            self.push(rest[0], rest[1], timeout=timeout)
            return ShellResult(args, 0, "")
        if head == "pull" and len(rest) == 2:
            self.pull(rest[0], rest[1], timeout=timeout)
            return ShellResult(args, 0, "")
        if head == "install" and rest:
            self.install_apk(rest[-1])
            return ShellResult(args, 0, "Success")
        raise AdbError(f"adb {' '.join(args)}: на телефоне эта команда не поддерживается")

    def push(self, local, remote, timeout=180):
        self.check_cancelled()
        error = self._bridge.push(str(local), str(remote))
        if error:
            raise AdbError(f"Не удалось записать {Path(str(local)).name} на магнитолу: {error}")

    def pull(self, remote, local, timeout=180):
        self.check_cancelled()
        error = self._bridge.pull(str(remote), str(local))
        if error:
            raise AdbError(f"Не удалось снять {remote} с магнитолы: {error}")

    def install_apk(self, path, reinstall=True, extra_args=None, timeout=180):
        self.check_cancelled()
        self.log(f"Установка APK: {Path(str(path)).name}")
        error = self._bridge.installApk(str(path))
        if error:
            raise AdbError(f"{Path(str(path)).name} не установлен: {error}")

    def uninstall(self, package, check=False):
        return self.shell(f"pm uninstall {shlex.quote(str(package))}", check=check)

    def uninstall_via_helper(self, package) -> bool:
        return bool(self._bridge.uninstallViaHelper(str(package)))

    def optimize_for_motion(self, package) -> None:
        error = self._bridge.optimizeForMotion(str(package))
        if error:
            raise InstallCancelled(str(error))

    def reboot(self, wait=True, boot_timeout=120):
        self.check_cancelled()
        error = self._bridge.reboot(bool(wait), int(float(boot_timeout) * 1000))
        if error:
            raise AdbError(str(error))

    def wait_for_device(self, timeout=90):
        self.check_cancelled()
        self.log(f"Ожидание устройства (до {int(float(timeout))} сек)...")
        if not self._bridge.waitForDevice(int(float(timeout) * 1000)):
            raise AdbError(f"Магнитола не появилась за {int(float(timeout))} сек")


class _SharedLoader(importlib.abc.Loader):
    def __init__(self, path: Path, loaded: list):
        self._path = path
        self._loaded = loaded

    def create_module(self, spec):
        return None

    def exec_module(self, module) -> None:
        source = code_signing.read_verified(self._path)
        module.__file__ = str(self._path)
        self._loaded.append(module.__name__)
        exec(compile(source, str(self._path), "exec"), module.__dict__)


class _SharedFinder(importlib.abc.MetaPathFinder):
    """import <модуль> внутри кода _shared — только подписанные cars/_shared/<модуль>.py. Стоит в конце
    sys.meta_path: стандартная библиотека и модули приложения находятся раньше и не подменяются."""

    def __init__(self, shared_dir: Path):
        self.shared_dir = shared_dir
        self.loaded = []

    def spec_for(self, name: str):
        path = self.shared_dir / f"{name}.py"
        return importlib.util.spec_from_loader(name, _SharedLoader(path, self.loaded), origin=str(path))

    def find_spec(self, fullname, path=None, target=None):
        if "." in fullname or not _NAME_RE.match(fullname) or not (self.shared_dir / f"{fullname}.py").is_file():
            return None
        return self.spec_for(fullname)


def _from_shared(module, shared: Path) -> bool:
    file = getattr(module, "__file__", None)
    try:
        return bool(file) and Path(file).parent == shared
    except (TypeError, ValueError):
        return False


def _jsonable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


def _message(exc: BaseException) -> str:
    if isinstance(exc, (RuntimeError, UnsignedCode)) and str(exc):
        return str(exc)  # понятная фраза из самого кода (_stop в _shared, InstallCancelled, AdbError)
    return f"{type(exc).__name__}: {exc}"


def _failure(error: str, cancelled: bool = False, unavailable: bool = False) -> dict:
    return {"ok": False, "error": error, "cancelled": cancelled, "unavailable": unavailable}


def _log_traceback(bridge) -> None:
    tail = traceback.format_exc().strip().splitlines()[-6:]
    try:
        bridge.log("Подробности для разработчика:\n" + "\n".join(tail))
    except Exception:  # noqa: BLE001
        pass


def call(bridge, shared_dir, module: str, function: str, args=(), model_dir=None, files=None) -> dict:
    """<module>.<function>(ctx, *args) из cars/_shared. {"ok": True, "result": …} или {"ok": False, "error": …,
    "cancelled": bool, "unavailable": bool}. unavailable — функция даже не запускалась (нет модуля/подписи/функции,
    модуль не загрузился): вызывающий может сделать то же своим способом. Модули _shared загружаются заново на каждый
    вызов (свежая версия с сервера без перезапуска приложения) и убираются из sys.modules после него."""
    if not (_NAME_RE.match(module or "") and _NAME_RE.match(function or "")) or function.startswith("_"):
        return _failure(f"недопустимое имя функции: {module}.{function}", unavailable=True)
    shared = Path(shared_dir)
    for name, loaded in list(sys.modules.items()):
        if _from_shared(loaded, shared):
            del sys.modules[name]  # прошлый вызов не должен оставить старую версию модуля
    if module in sys.modules:
        return _failure(f"{module}.py: имя совпадает с модулем приложения — переименуйте модуль", unavailable=True)
    ctx = AndroidCtx(bridge, shared, model_dir, files)
    finder = _SharedFinder(shared)
    sys.meta_path.append(finder)
    try:
        try:
            spec = finder.spec_for(module)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module] = mod
            spec.loader.exec_module(mod)
        except Exception as exc:  # noqa: BLE001 — нет подписи, синтаксис, импорт: модуль не годится
            if not isinstance(exc, UnsignedCode):
                _log_traceback(bridge)
            return _failure(_message(exc), unavailable=True)
        func = getattr(mod, function, None)
        if not callable(func):
            return _failure(f"в {module}.py нет функции {function} — обновите каталог", unavailable=True)
        result = func(ctx, *list(args or []))
        return {"ok": True, "result": _jsonable(result)}
    except InstallCancelled as exc:
        return _failure(str(exc), cancelled=True)
    except Exception as exc:  # noqa: BLE001 — любая ошибка кода с сервера — итог этапа, а не падение приложения
        _log_traceback(bridge)
        return _failure(_message(exc))
    finally:
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)
        for name in set(finder.loaded) | {module}:
            sys.modules.pop(name, None)


def run(bridge, shared_dir: str, module: str, function: str, args_json: str = "[]", model_dir: str = "",
        files_json: str = "{}") -> str:
    """Точка входа для Kotlin (PyCtxBridge.call): аргументы и итог — JSON-строками."""
    try:
        args = json.loads(args_json or "[]")
        files = json.loads(files_json or "{}")
    except ValueError:
        return json.dumps(_failure("битые аргументы команды", unavailable=True), ensure_ascii=False)
    if not isinstance(args, list) or not isinstance(files, dict):
        return json.dumps(_failure("битые аргументы команды", unavailable=True), ensure_ascii=False)
    result = call(bridge, shared_dir, module, function, args, model_dir or None, files)
    return json.dumps(result, ensure_ascii=False)
