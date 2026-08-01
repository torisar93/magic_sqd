"""Диалог "Сообщить о проблеме" в главном окне — простая форма (причина +
краткое описание), отправляется на сервер (см. app/report_client.py),
который пересылает её на почту разработчика (server/backend.py: /report)."""
import queue
import threading
import tkinter as tk
from tkinter import messagebox

import customtkinter

from .report_client import ReportError, send_report
from .submit_config import SubmitConfig
from . import theme

REASONS = [
    "Инструкция больше не актуальна",
    "Появилась новая версия",
    "Не работает этап установки",
    "Другое",
]


class ReportDialog(customtkinter.CTkToplevel):
    def __init__(self, parent, brand: str, model: str, config: SubmitConfig):
        super().__init__(parent)
        theme.style_toplevel(self)
        self.brand = brand
        self.model = model
        self.config = config
        self._queue = queue.Queue()

        self.title(f"Сообщить о проблеме — {brand} / {model}")
        self.geometry("480x420")
        self.minsize(420, 360)
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self.after(100, self._drain_queue)

    def _build_ui(self):
        frame = customtkinter.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=14, pady=14)

        customtkinter.CTkLabel(frame, text=f"{self.brand} / {self.model}",
                                font=theme.FONT_BOLD, text_color=theme.TEXT_DIM,
                                anchor="w").pack(fill="x", pady=(0, 8))

        customtkinter.CTkLabel(frame, text="Причина", font=theme.FONT_BOLD,
                                text_color=theme.TEXT, anchor="w").pack(fill="x")
        self.reason_var = tk.StringVar(value=REASONS[0])
        customtkinter.CTkOptionMenu(
            frame, variable=self.reason_var, values=REASONS, font=theme.FONT,
            fg_color=theme.BG_CARD, button_color=theme.BORDER,
            button_hover_color=theme.ACCENT, text_color=theme.TEXT
        ).pack(fill="x", pady=(2, 10))

        customtkinter.CTkLabel(frame, text="Описание (необязательно)", font=theme.FONT_BOLD,
                                text_color=theme.TEXT, anchor="w").pack(fill="x")
        self.description_text = customtkinter.CTkTextbox(
            frame, height=140, font=theme.FONT, fg_color=theme.BG_CARD, text_color=theme.TEXT)
        self.description_text.pack(fill="both", expand=True, pady=(2, 10))

        self.status_var = tk.StringVar()
        customtkinter.CTkLabel(frame, textvariable=self.status_var, text_color=theme.TEXT_DIM,
                                font=theme.FONT_SMALL, anchor="w", wraplength=440,
                                justify="left").pack(fill="x")

        btn_row = customtkinter.CTkFrame(frame, fg_color="transparent")
        btn_row.pack(fill="x", pady=(8, 0))
        customtkinter.CTkButton(btn_row, text="Отмена", command=self.destroy,
                                 **theme.secondary_button()).pack(side="right")
        self.send_btn = customtkinter.CTkButton(btn_row, text="Отправить", command=self._send,
                                                  **theme.accent_button())
        self.send_btn.pack(side="right", padx=(0, 6))

    def _send(self):
        reason = self.reason_var.get()
        description = self.description_text.get("1.0", tk.END).strip()
        self.send_btn.configure(state="disabled")
        self.status_var.set("Отправка...")
        threading.Thread(target=self._worker, args=(reason, description), daemon=True).start()

    def _worker(self, reason, description):
        try:
            send_report(self.brand, self.model, reason, description, self.config)
        except ReportError as exc:
            self._queue.put(("done", False, str(exc)))
            return
        self._queue.put(("done", True, "Спасибо! Обращение отправлено."))

    def _drain_queue(self):
        try:
            while True:
                kind, success, message = self._queue.get_nowait()
                if kind == "done":
                    if success:
                        messagebox.showinfo(self.title(), message, parent=self)
                        self.destroy()
                        return
                    self.send_btn.configure(state="normal")
                    self.status_var.set(message)
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._drain_queue)
