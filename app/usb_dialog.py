"""Диалог "Установка через USB-флешку": выбор диска, форматирование, копирование."""
import queue
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

import customtkinter

from .content_sync import ensure_apks_downloaded, sync_model_files
from .install_context import InstallCancelled
from .usb_context import UsbContext
from .usb_utils import list_removable_drives, format_drive, UsbSafetyError
from . import theme


class UsbDialog(customtkinter.CTkToplevel):
    def __init__(self, parent, base_dir: Path, model, selected_apks, run_fn, title_suffix=None,
                 on_finished=None, variant: str | None = None):
        """run_fn(ctx) — этап "usb" из stages.py модели (мастер этапов —
        единственный, кто открывает этот диалог).
        on_finished(success: bool), если задан, вызывается из фонового потока
        по завершении (в дополнение к обычному messagebox) — мастер этапов
        использует его, чтобы отметить этап выполненным.
        variant — выбранный техником вариант содержимого (Full/Lite/...),
        см. UsbContext."""
        super().__init__(parent)
        theme.style_toplevel(self)
        self.base_dir = base_dir
        self.model = model
        self.selected_apks = selected_apks
        self.run_fn = run_fn
        self.on_finished = on_finished
        self.variant = variant

        self.title(f"USB-флешка — {model.brand} / {model.name}" +
                   (f" — {title_suffix}" if title_suffix else ""))
        self.geometry("670x600")
        self.minsize(600, 480)
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
        frame = customtkinter.CTkFrame(self, fg_color="transparent")
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(6, weight=1)

        customtkinter.CTkLabel(frame, text="Флешка:", font=theme.FONT_BOLD,
                                text_color=theme.TEXT).grid(row=0, column=0, sticky="w")
        self.drive_combo = customtkinter.CTkOptionMenu(
            frame, values=[""], font=theme.FONT, fg_color=theme.BG_CARD,
            button_color=theme.BORDER, button_hover_color=theme.ACCENT, text_color=theme.TEXT)
        self.drive_combo.grid(row=0, column=1, sticky="we", padx=(6, 6))
        customtkinter.CTkButton(frame, text="Обновить", command=self._refresh_drives,
                                 **theme.secondary_button()).grid(row=0, column=2)

        customtkinter.CTkLabel(
            frame, text="Показаны только съёмные USB-накопители — системный "
                        "и внутренние диски в списке не появятся.",
            text_color=theme.TEXT_DIM, font=theme.FONT_SMALL, wraplength=520, justify="left"
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 10))

        self.format_var = tk.BooleanVar(value=True)
        customtkinter.CTkCheckBox(
            frame, text="Отформатировать флешку перед копированием", variable=self.format_var,
            command=self._update_warning, font=theme.FONT, text_color=theme.TEXT,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER, border_color=theme.BORDER
        ).grid(row=2, column=0, columnspan=3, sticky="w")

        fs_frame = customtkinter.CTkFrame(frame, fg_color="transparent")
        fs_frame.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4, 8))
        customtkinter.CTkLabel(fs_frame, text="Файловая система:", text_color=theme.TEXT,
                                font=theme.FONT).pack(side="left")
        self.fs_var = tk.StringVar(value="FAT32")
        radio_kwargs = dict(variable=self.fs_var, font=theme.FONT, text_color=theme.TEXT,
                             fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                             border_color=theme.BORDER)
        customtkinter.CTkRadioButton(fs_frame, text="FAT32 (обычно нужна магнитолам, до ~32 ГБ)",
                                      value="FAT32", **radio_kwargs).pack(side="left", padx=(8, 0))
        customtkinter.CTkRadioButton(fs_frame, text="exFAT (для флешек больше 32 ГБ)",
                                      value="exFAT", **radio_kwargs).pack(side="left", padx=(8, 0))

        self.warning_var = tk.StringVar()
        customtkinter.CTkLabel(frame, textvariable=self.warning_var, text_color=theme.DANGER,
                                wraplength=540, font=theme.FONT_BOLD, justify="left").grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(0, 8))

        customtkinter.CTkLabel(frame, text="Лог:", text_color=theme.TEXT_DIM,
                                font=theme.FONT).grid(row=5, column=0, columnspan=3, sticky="w")
        self.log_view = customtkinter.CTkTextbox(frame, height=200, state="disabled",
                                                   font=theme.FONT_MONO, fg_color=theme.BG_CARD,
                                                   text_color=theme.TEXT)
        self.log_view.grid(row=6, column=0, columnspan=3, sticky="nsew")

        btn_frame = customtkinter.CTkFrame(frame, fg_color="transparent")
        btn_frame.grid(row=7, column=0, columnspan=3, sticky="we", pady=(10, 0))
        self.start_btn = customtkinter.CTkButton(btn_frame, text="Начать", command=self._start,
                                                   **theme.accent_button())
        self.start_btn.pack(side="left")
        self.stop_btn = customtkinter.CTkButton(btn_frame, text="Стоп", command=self._stop,
                                                  state="disabled", **theme.danger_button())
        self.stop_btn.pack(side="left", padx=(6, 0))
        customtkinter.CTkButton(btn_frame, text="Закрыть", command=self._on_close,
                                 **theme.secondary_button()).pack(side="right")

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
        labels = [d.display for d in self.drives]
        self.drive_combo.configure(values=labels)
        self.drive_combo.set(labels[0] if labels else "")
        self._log(f"Найдено съёмных флешек: {len(self.drives)}")

    def _selected_drive(self):
        display = self.drive_combo.get()
        for d in self.drives:
            if d.display == display:
                return d
        return None

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
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.drive_combo.configure(state="disabled")

        self._worker_thread = threading.Thread(target=self._worker, args=(drive,), daemon=True)
        self._worker_thread.start()

    def _stop(self):
        self._cancel_flag.set()
        self._log("Останавливаю... (завершится на ближайшей проверке)")

    def _check_cancelled(self):
        if self._cancel_flag.is_set():
            raise InstallCancelled("Копирование остановлено пользователем.")

    def _worker(self, drive):
        try:
            sync_model_files(self.base_dir, self.model, log=self._log_threaded,
                              check_cancelled=self._check_cancelled)
            ensure_apks_downloaded(self.base_dir, self.base_dir / "apk", self.selected_apks,
                                    log=self._log_threaded, check_cancelled=self._check_cancelled)

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
                variant=self.variant,
            )
            self.run_fn(ctx)
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
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self.drive_combo.configure(state="normal")
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
        self.log_view.configure(state="normal")
        self.log_view.insert(tk.END, str(message) + "\n")
        self.log_view.see(tk.END)
        self.log_view.configure(state="disabled")

    def _on_close(self):
        if self._worker_thread is not None and self._worker_thread.is_alive():
            if not messagebox.askyesno(self.title(), "Операция ещё выполняется. Закрыть окно?"):
                return
            self._cancel_flag.set()
        self.destroy()
