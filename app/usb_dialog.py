"""Диалог "Установка через USB-флешку": выбор диска, форматирование, копирование."""
import queue
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import ttk, scrolledtext, messagebox

from .install_context import InstallCancelled
from .usb_context import UsbContext
from .usb_runner import run_usb_install
from .usb_utils import list_removable_drives, format_drive, UsbSafetyError


class UsbDialog(tk.Toplevel):
    def __init__(self, parent, base_dir: Path, model, selected_apks, run_fn=None, title_suffix=None,
                 on_finished=None):
        """run_fn(ctx), если задан, используется вместо usb_install.py модели
        (или копирования по умолчанию) — так мастер этапов переиспользует этот
        диалог для отдельных USB-этапов.
        on_finished(success: bool), если задан, вызывается из фонового потока
        по завершении (в дополнение к обычному messagebox) — мастер этапов
        использует его, чтобы отметить этап выполненным."""
        super().__init__(parent)
        self.base_dir = base_dir
        self.model = model
        self.selected_apks = selected_apks
        self.run_fn = run_fn
        self.on_finished = on_finished

        self.title(f"USB-флешка — {model.brand} / {model.name}" +
                   (f" — {title_suffix}" if title_suffix else ""))
        self.geometry("580x520")
        self.minsize(520, 420)
        self.transient(parent)
        self.grab_set()

        self.drives = []
        self._log_queue = queue.Queue()
        self._cancel_flag = threading.Event()
        self._worker_thread = None

        self._build_ui()
        self._refresh_drives()
        self.after(100, self._drain_log_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    def _build_ui(self):
        frame = ttk.Frame(self, padding=10)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(6, weight=1)

        ttk.Label(frame, text="Флешка:", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w")
        self.drive_combo = ttk.Combobox(frame, state="readonly")
        self.drive_combo.grid(row=0, column=1, sticky="we", padx=(6, 6))
        ttk.Button(frame, text="Обновить", command=self._refresh_drives).grid(row=0, column=2)

        ttk.Label(frame, text="Показаны только съёмные USB-накопители — системный "
                               "и внутренние диски в списке не появятся.",
                  foreground="#888", wraplength=520).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 10))

        self.format_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Отформатировать флешку перед копированием",
                         variable=self.format_var, command=self._update_warning).grid(
            row=2, column=0, columnspan=3, sticky="w")

        fs_frame = ttk.Frame(frame)
        fs_frame.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 8))
        ttk.Label(fs_frame, text="Файловая система:").pack(side="left")
        self.fs_var = tk.StringVar(value="FAT32")
        ttk.Radiobutton(fs_frame, text="FAT32 (обычно нужна магнитолам, до ~32 ГБ)",
                         value="FAT32", variable=self.fs_var).pack(side="left", padx=(8, 0))
        ttk.Radiobutton(fs_frame, text="exFAT (для флешек больше 32 ГБ)",
                         value="exFAT", variable=self.fs_var).pack(side="left", padx=(8, 0))

        self.warning_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.warning_var, foreground="#a33",
                  wraplength=540, font=("Segoe UI", 9, "bold")).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(0, 8))

        ttk.Label(frame, text="Лог:").grid(row=5, column=0, columnspan=3, sticky="w")
        self.log_view = scrolledtext.ScrolledText(frame, height=14, state="disabled",
                                                    font=("Consolas", 9))
        self.log_view.grid(row=6, column=0, columnspan=3, sticky="nsew")

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=7, column=0, columnspan=3, sticky="we", pady=(10, 0))
        self.start_btn = ttk.Button(btn_frame, text="Начать", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btn_frame, text="Стоп", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(6, 0))
        ttk.Button(btn_frame, text="Закрыть", command=self._on_close).pack(side="right")

        self._update_warning()

    def _update_warning(self):
        if self.format_var.get():
            self.warning_var.set(
                "ВНИМАНИЕ: при форматировании все данные на выбранной флешке "
                "будут удалены безвозвратно!"
            )
        else:
            self.warning_var.set(
                "Форматирование выключено — файлы будут просто скопированы поверх "
                "того, что уже есть на флешке."
            )

    # ------------------------------------------------------------------
    def _refresh_drives(self):
        self.drives = list_removable_drives()
        self.drive_combo["values"] = [d.display for d in self.drives]
        if self.drives:
            self.drive_combo.current(0)
        else:
            self.drive_combo.set("")
        self._log(f"Найдено съёмных флешек: {len(self.drives)}")

    def _selected_drive(self):
        idx = self.drive_combo.current()
        if idx is None or idx < 0 or idx >= len(self.drives):
            return None
        return self.drives[idx]

    # ------------------------------------------------------------------
    def _start(self):
        drive = self._selected_drive()
        if not drive:
            messagebox.showwarning(self.title(), "Выберите флешку из списка.")
            return

        if self.format_var.get():
            confirmed = messagebox.askyesno(
                self.title(),
                f"Все данные на флешке {drive.letter}\\ ({drive.label or 'без метки'}, "
                f"{drive.total_bytes / (1024 ** 3):.1f} ГБ) будут удалены безвозвратно.\n\n"
                f"Продолжить форматирование в {self.fs_var.get()}?",
                icon="warning",
            )
            if not confirmed:
                return

        self._cancel_flag = threading.Event()
        self.start_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.drive_combo.config(state="disabled")

        self._worker_thread = threading.Thread(target=self._worker, args=(drive,), daemon=True)
        self._worker_thread.start()

    def _stop(self):
        self._cancel_flag.set()
        self._log("Останавливаю... (завершится на ближайшей проверке)")

    def _worker(self, drive):
        try:
            if self.format_var.get():
                try:
                    format_drive(drive.letter, self.fs_var.get(), self.model.name,
                                 self.base_dir, log=self._log_threaded)
                except UsbSafetyError as exc:
                    self._finished_threaded(False, str(exc))
                    return
                if self._cancel_flag.is_set():
                    self._finished_threaded(False, "Остановлено пользователем после форматирования.")
                    return

            drive_root = Path(f"{drive.letter}\\")
            ctx = UsbContext(
                drive_root=drive_root,
                model_dir=self.model.dir,
                selected_apks=self.selected_apks,
                log_fn=self._log_threaded,
                cancel_flag=self._cancel_flag,
            )
            if self.run_fn:
                self.run_fn(ctx)
            else:
                run_usb_install(self.model, ctx)
        except InstallCancelled as exc:
            self._finished_threaded(False, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку
            self._log_threaded(traceback.format_exc())
            self._finished_threaded(False, f"Ошибка: {exc}")
            return
        self._finished_threaded(True, "Копирование на флешку завершено.")

    # ------------------------------------------------------------------
    # Логи/колбэки из фонового потока — только через очередь
    # ------------------------------------------------------------------
    def _log_threaded(self, message):
        self._log_queue.put(("log", message))

    def _finished_threaded(self, success, message):
        self._log_queue.put(("finished", (success, message)))

    def _drain_log_queue(self):
        try:
            while True:
                kind, payload = self._log_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "finished":
                    success, message = payload
                    self._log(message)
                    self.start_btn.config(state="normal")
                    self.stop_btn.config(state="disabled")
                    self.drive_combo.config(state="readonly")
                    if self.on_finished:
                        self.on_finished(success)
                    if success:
                        messagebox.showinfo(self.title(), message)
                    else:
                        messagebox.showerror(self.title(), message)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._drain_log_queue)

    def _log(self, message):
        self.log_view.config(state="normal")
        self.log_view.insert(tk.END, str(message) + "\n")
        self.log_view.see(tk.END)
        self.log_view.config(state="disabled")

    def _on_close(self):
        if self._worker_thread is not None and self._worker_thread.is_alive():
            if not messagebox.askyesno(self.title(), "Операция ещё выполняется. Закрыть окно?"):
                return
            self._cancel_flag.set()
        self.destroy()
