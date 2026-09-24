"""Обёртка usb_utils.py/usb_context.py для диалога "Через USB-флешку" внутри
stage wizard (этап type="usb"). Портировано из app/usb_dialog.py — тот же
воркер-поток и порядок операций (докачка files/выбранных APK, опциональное
форматирование, затем run_fn(ctx)), только прогресс идёт через
app/web/events.py вместо queue.Queue+self.after(100, ...)."""
from __future__ import annotations
import sys
import threading
from pathlib import Path

from ..events import event_bridge
from ...content_sync import ensure_apks_downloaded, sync_model_files, sync_shared_folder
from ...install_context import InstallCancelled
from ...stage_runner import load_stages
from ...usb_context import UsbContext, remove_other_trigger_flags, write_flash_files
from ...usb_utils import list_drives as _list_drives, format_drive, UsbSafetyError


def _drive_to_dict(d) -> dict:
    return {"letter": d.letter, "label": d.label, "total_bytes": d.total_bytes,
            "free_bytes": d.free_bytes, "display": d.display}


def _file_items(path: Path) -> list[dict]:
    if path.is_dir():
        paths = sorted(p for p in path.rglob("*") if p.is_file())
    else:
        paths = [path] if path.is_file() else []
    return [{"name": p.name, "path": str(p), "size": p.stat().st_size} for p in paths]


def _flash_write_block(stage: dict, block_index) -> dict | None:
    """Блок «Запись на флешку» этапа «Флешка» (см. car_generator.py:
    FlashBlockSpec) по номеру из JS; None — запись всего этапа по-старому
    (stage["run"])."""
    if block_index is None:
        return None
    blocks = stage.get("flash_blocks") or []
    block = blocks[block_index] if isinstance(block_index, int) and 0 <= block_index < len(blocks) else None
    if not block or block.get("kind") != "write":
        raise ValueError("Этот блок этапа не записывает файлы — обновите модель и повторите.")
    return block


def _scan_flash_block_items(base_dir: Path, block: dict, selected_apk_paths: list[str]) -> list[dict]:
    """Как _scan_usb_items, но для одного блока — в том же порядке, в каком его
    записывает usb_context.write_flash_files."""
    items: list[dict] = []
    for raw in block.get("files") or []:
        items += _file_items(Path(raw))
    if block.get("copy_selected_apks"):
        for raw in selected_apk_paths:
            items += _file_items(Path(raw))
    if block.get("shared_folder"):
        items += _file_items(base_dir / "cars" / "_shared" / block["shared_folder"])
    return items


def _scan_usb_items(base_dir: Path, model, stage: dict, stage_index: int, variant,
                     selected_apk_paths: list[str]) -> list[dict]:
    """Список файлов, которые в ЦЕЛОМ запишутся на флешку за этот запуск —
    отражает РОВНО ТУ ЖЕ структуру, что сгенерированный usb_step_N(ctx)
    фактически копирует (см. app/car_generator.py: _render_install_py,
    ветка "usb" — usb_dir → выбранные APK → общая папка _shared, в этом же
    порядке), но посчитан ЗАРАНЕЕ, до старта записи: технику нужно увидеть
    список файлов и общий счётчик ДО первого события прогресса (см.
    UsbApi.list_items). stage_index — 0-based позиция в load_stages(model),
    совпадает с (i-1) генератора (оба перебирают spec.steps/STAGES в одном
    и том же порядке, см. app/stage_runner.py:load_stages). Не гарантирует
    точность для ручной правки install.py — это чисто визуальная сводка, не
    влияет на саму запись (см. план)."""
    items: list[dict] = []
    usb_step_dir = model.dir / "usb_files" / f"step_{stage_index + 1}"
    if variant:
        usb_step_dir = usb_step_dir / str(variant)
    if usb_step_dir.is_dir():
        for path in sorted(usb_step_dir.rglob("*")):
            if path.is_file():
                items.append({"name": path.name, "path": str(path), "size": path.stat().st_size})
    if stage.get("usb_copy_selected_apks"):
        for raw in selected_apk_paths:
            path = Path(raw)
            if path.is_file():
                items.append({"name": path.name, "path": str(path), "size": path.stat().st_size})
    shared_folder = stage.get("usb_shared_folder")
    if shared_folder:
        shared_dir = base_dir / "cars" / "_shared" / shared_folder
        if shared_dir.is_dir():
            for path in sorted(shared_dir.rglob("*")):
                if path.is_file():
                    items.append({"name": path.name, "path": str(path), "size": path.stat().st_size})
    return items


class UsbApi:
    def __init__(self, base_dir, scanner_api):
        self.base_dir = base_dir
        self._scanner_api = scanner_api
        self._cancel_flag = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def _running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def list_drives(self, include_all: bool = False) -> list[dict]:
        return [_drive_to_dict(d) for d in _list_drives(include_all, self.base_dir)]

    def list_items(self, model_key: str, stage_index: int, variant, selected_apk_paths: list[str],
                   block: int | None = None) -> dict:
        """Считает список файлов, которые запишутся на флешку — вызывается
        ДО start(), чтобы фронтенд успел показать очередь в окне прогресса
        (см. app/web/frontend/js/screens/dialogs.js) раньше, чем начнётся
        сама запись. Быстрый, синхронный (только stat() файлов, уже
        докачанных ранее содержимым модели — см. sync_model_files в
        load_spec/_worker ниже), ничего не запускает. block — номер блока
        записи этапа «Флешка» (0-based), None — весь этап по-старому."""
        model = self._scanner_api.get_model(model_key)
        if model is None:
            return {"ok": False, "error": "unknown model key"}
        stage = load_stages(model)[stage_index]
        try:
            flash_block = _flash_write_block(stage, block)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        items = (_scan_flash_block_items(self.base_dir, flash_block, selected_apk_paths) if flash_block
                 else _scan_usb_items(self.base_dir, model, stage, stage_index, variant, selected_apk_paths))
        return {"ok": True, "items": items}

    def start(self, model_key: str, stage_index: int, variant, selected_apk_paths: list[str],
              drive_letter: str, do_format: bool, filesystem: str, block: int | None = None) -> dict:
        if self._running:
            return {"ok": False, "error": "Копирование уже выполняется."}
        model = self._scanner_api.get_model(model_key)
        if model is None:
            return {"ok": False, "error": "unknown model key"}
        stage = load_stages(model)[stage_index]
        try:
            flash_block = _flash_write_block(stage, block)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}

        self._cancel_flag = threading.Event()
        self._thread = threading.Thread(
            target=self._worker,
            args=(model, stage, stage_index, variant, selected_apk_paths, drive_letter, do_format, filesystem,
                  flash_block),
            daemon=True,
        )
        self._thread.start()
        return {"ok": True}

    def cancel(self) -> dict:
        if self._running:
            self._cancel_flag.set()
            self._log("Останавливаю... (завершится на ближайшей проверке)")
        return {"ok": True}

    def _check_cancelled(self):
        if self._cancel_flag.is_set():
            raise InstallCancelled("Копирование остановлено пользователем.")

    def _worker(self, model, stage, stage_index, variant, selected_apk_paths, drive_letter, do_format, filesystem,
                flash_block: dict | None = None):
        try:
            sync_model_files(self.base_dir, model, log=self._log,
                             check_cancelled=self._check_cancelled,
                             on_progress=self._progress)
            ensure_apks_downloaded(self.base_dir, self.base_dir / "apk", selected_apk_paths,
                                    log=self._log, check_cancelled=self._check_cancelled,
                                    on_progress=self._progress)
            shared_folder = flash_block.get("shared_folder") if flash_block else stage.get("usb_shared_folder")
            if shared_folder:
                sync_shared_folder(self.base_dir, shared_folder,
                                    log=self._log, check_cancelled=self._check_cancelled,
                                    on_progress=self._progress)

            new_mount = None
            if do_format:
                try:
                    new_mount = format_drive(drive_letter, filesystem, model.name, self.base_dir, log=self._log)
                except UsbSafetyError as exc:
                    self._finish(False, str(exc))
                    return
                if self._cancel_flag.is_set():
                    self._finish(False, "Остановлено пользователем после форматирования.")
                    return

            # На Windows буква диска не меняется после форматирования
            # (format_drive там ничего не возвращает — new_mount всегда
            # None). На macOS diskutil eraseDisk стирает ВЕСЬ физический
            # диск и монтирует новый том по новому пути — см.
            # usb_utils_mac.py:format_drive, drive_letter выше уже
            # недействителен.
            if new_mount:
                drive_root = Path(new_mount)
            elif sys.platform == "win32":
                drive_root = Path(f"{drive_letter}\\")
            else:
                drive_root = Path(drive_letter)

            # Пересчитываем список файлов (тот же, что list_items() уже
            # отдал фронтенду до старта, см. её докстринг) — только чтобы
            # узнать files_total к этому моменту; сами файлы к этому моменту
            # уже докачаны шагами выше (sync_model_files/ensure_apks_downloaded/
            # sync_shared_folder), так что список не должен разъехаться.
            items = (_scan_flash_block_items(self.base_dir, flash_block, selected_apk_paths) if flash_block
                     else _scan_usb_items(self.base_dir, model, stage, stage_index, variant, selected_apk_paths))
            # Пишем svlog.flag — со флешки уходит svengmode.flag прошлого шага (и наоборот), см. TRIGGER_FLAGS.
            remove_other_trigger_flags(drive_root, [item["name"] for item in items], self._log)
            ctx = UsbContext(
                drive_root=drive_root,
                model_dir=model.dir,
                selected_apks=selected_apk_paths,
                log_fn=self._log,
                cancel_flag=self._cancel_flag,
                variant=variant,
                shared_dir=self.base_dir / "cars" / "_shared",
                on_progress=lambda path, bytes_done, bytes_total, files_done, files_total, state: (
                    self._progress_apk(stage_index, path, files_done, files_total, state, bytes_done, bytes_total)
                ),
                files_total=len(items),
            )
            if flash_block:
                write_flash_files(ctx, flash_block)
            else:
                stage["run"](ctx)
        except InstallCancelled as exc:
            self._finish(False, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку
            # Traceback больше не льём в видимый лог — см. app/runner.py за
            # тем же решением и обоснованием.
            self._finish(False, f"Ошибка: {exc}")
            return
        self._finish(True, "Копирование на флешку завершено.")

    def _log(self, message) -> None:
        event_bridge.push({"kind": "usb_log", "text": str(message)})

    @staticmethod
    def _progress(done: int, total: int, files_done: int | None = None,
                  files_total: int | None = None) -> None:
        event_bridge.push({"kind": "sync_progress", "done": done, "total": total,
                           "files_done": files_done, "files_total": files_total})

    @staticmethod
    def _progress_apk(stage_index: int, path: str, completed: int, total: int, state: str,
                       bytes_done: int, bytes_total: int) -> None:
        """Тот же формат события "apk_progress", что уже используют
        установка приложений на Android (см. WebBridge.kt: pushApkProgress) и
        общий UI-компонент app/web/frontend/js/progress08.js — переиспользуем
        готовое кольцо с процентом и очередь файлов вместо изобретения
        нового индикатора специально для записи на флешку (см. dialogs.js)."""
        event_bridge.push({
            "kind": "apk_progress", "stage_index": stage_index, "path": path,
            "completed": completed, "total": total, "state": state,
            "phase": "transfer", "determinate": bytes_total > 0,
            "bytes_done": bytes_done, "bytes_total": bytes_total,
        })

    def _finish(self, success: bool, message: str) -> None:
        self._progress(0, 0)
        event_bridge.push({"kind": "usb_finished", "success": success, "message": message})
