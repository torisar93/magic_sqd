"""Запись на флешку по плану (flash_plan.py, владелец 12.10.2026: «делай вынос»): какие файлы скачать и куда их
положить, решает общий код каталога с сервера — cars/_shared/flash_plan.py, подписанный ключом разработчика
(code_signing, scripts/publish_shared.py). Подпись не сошлась, другой PLAN_API, ошибка или ответ не прошёл проверку —
встроенный flash_plan.py этой версии программы. Программа выполняет план простыми операциями: ПК — app/web/api/usb_api.py
(блоки этапа «Флешка»), Android — WebBridge.kt: usbRunStage/usbListItems (через mobile_bridge.usb_plan).

Проверка ответа (check) — здесь, в программе, а не в плане: путь на флешке только внутри неё, файлы — только из папки
программы, убрать на флешке можно только файл в корне.

Файл ОДИНАКОВЫЙ на ПК (app/flash_writer.py) и Android (android/app/src/main/python/flash_writer.py) —
tests/test_flash_plan.py сверяет копии."""
from __future__ import annotations

from pathlib import Path, PurePosixPath

try:
    from . import code_signing  # ПК: модули пакета app
    from . import flash_plan as builtin_plan
except ImportError:  # Android (Chaquopy): модули верхнего уровня
    import code_signing
    import flash_plan as builtin_plan

PLAN_API = 1
SERVER_MODULE = "flash_plan.py"


class PlanError(ValueError):
    """План не годится — пишем встроенным, а если и он не годится — по-старому."""


def make_plan(request: dict, shared_dir, data_dir, log=lambda message: None) -> dict:
    """План записи: серверный, если он есть и годится, иначе встроенный. В ответе — поле source ("server"/"builtin").
    Серверного файла нет (каталог ещё не скачан, старый сервер) — молча встроенный; есть, но не годится — строка в
    журнал (по ней видно, что общий код не сработал)."""
    path = Path(shared_dir) / SERVER_MODULE
    if path.is_file():
        try:
            result = check(_server_plan(path)(request), data_dir)
            result["source"] = "server"
            return result
        except Exception as exc:  # noqa: BLE001 — любой сбой серверного плана: встроенный ниже
            log(f"Общий код каталога для флешки не годится ({_why(exc)}) — список файлов составлен встроенным.")
    result = check(builtin_plan.plan(request), data_dir)
    result["source"] = "builtin"
    return result


def _server_plan(path: Path):
    source = code_signing.read_verified(path)
    namespace = {"__name__": "flash_plan_server", "__file__": str(path)}
    exec(compile(source, str(path), "exec"), namespace)
    if namespace.get("PLAN_API") != PLAN_API or not callable(namespace.get("plan")):
        raise PlanError(f"версия плана {namespace.get('PLAN_API')!r}, а программа понимает {PLAN_API}")
    return namespace["plan"]


def _why(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text if len(text) <= 160 else text[:160] + "…"


def check(result, data_dir) -> dict:
    """Проверяет и чистит ответ плана; PlanError — если что-то вне правил."""
    if not isinstance(result, dict) or not isinstance(result.get("files"), list):
        raise PlanError("ответ плана без списка файлов")
    root = Path(data_dir).resolve()
    files = []
    for item in result["files"]:
        if not isinstance(item, dict):
            raise PlanError("элемент списка — не словарь")
        local, dest = item.get("local"), item.get("dest")
        if not isinstance(local, str) or not _inside(local, root):
            raise PlanError(f"файл не из папки программы: {local!r}")
        files.append({"local": local, "remote": item.get("remote") if isinstance(item.get("remote"), str) else None,
                      "dest": _flash_path(dest), "size": _int(item.get("size"))})
    cleanup = []
    for local in result.get("local_cleanup") or []:
        if not isinstance(local, str) or not _inside(local, root):
            raise PlanError(f"убрать можно только в папке программы: {local!r}")
        cleanup.append(local)
    remove = []
    for name in result.get("remove_on_flash") or []:
        if not isinstance(name, str) or _flash_path(name) != name or "/" in name:
            raise PlanError(f"убрать на флешке можно только файл в корне: {name!r}")
        remove.append(name)
    return {"api": PLAN_API, "files": files, "local_cleanup": cleanup, "remove_on_flash": remove}


def _inside(local: str, root: Path) -> bool:
    try:
        Path(local).resolve().relative_to(root)
        return True
    except (ValueError, OSError):
        return False


def _flash_path(dest) -> str:
    if not isinstance(dest, str) or not dest or "\\" in dest or ":" in dest or dest.startswith("/"):
        raise PlanError(f"путь на флешке не годится: {dest!r}")
    parts = PurePosixPath(dest).parts
    if any(part in ("", ".", "..") for part in parts):
        raise PlanError(f"путь на флешке не годится: {dest!r}")
    return "/".join(parts)


def _int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
