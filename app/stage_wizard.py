"""Мастер установки по этапам (stages.py модели): чередование USB-флешки,
ADB и ручных шагов ("обновите прошивку на магнитоле и вставьте флешку снова")
в рамках одной последовательной установки."""
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, scrolledtext, messagebox

from tkinterweb import HtmlFrame

from .adb_utils import list_devices
from .runner import InstallRunner
from .stage_runner import StageDefinitionError, load_stages, stage_instruction_html_path
from .usb_dialog import UsbDialog

TYPE_LABELS = {"usb": "USB-флешка", "adb": "ADB", "manual": "Вручную на магнитоле"}

PLACEHOLDER_HTML = (
    "<body style='font-family:Segoe UI, sans-serif; color:#888; padding:16px'>"
    "<p>Для этого этапа нет отдельной инструкции.</p></body>"
)


def _text_to_html(text: str) -> str:
    escaped = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
               .replace("\n", "<br>"))
    return (
        "<body style='font-family:Segoe UI, sans-serif; color:#222; padding:16px'>"
        f"<p>{escaped}</p></body>"
    )


class StageWizard(tk.Toplevel):
    def __init__(self, parent, base_dir: Path, adb_path: str, model, selected_apks):
        super().__init__(parent)
        self.base_dir = base_dir
        self.adb_path = adb_path
        self.model = model
        self.selected_apks = selected_apks
        self.device_by_label = {}
        self.done = set()

        self.title(f"Установка по этапам — {model.brand} / {model.name}")
        self.geometry("980x680")
        self.minsize(820, 560)
        self.transient(parent)

        try:
            self.stages = load_stages(model)
        except StageDefinitionError as exc:
            messagebox.showerror(self.title(), f"Ошибка в stages.py: {exc}")
            self.destroy()
            return
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(self.title(), f"Не удалось загрузить stages.py: {exc}")
            self.destroy()
            return

        self.current_index = 0
        self._log_queue = queue.Queue()
        self.install_runner = InstallRunner(adb_path, self._on_log_threaded, self._on_finished_threaded)

        self.grab_set()
        self._build_ui()
        self._populate_stage_list()
        self._refresh_devices()
        self._select_stage(0)
        self.after(100, self._drain_log_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    def _build_ui(self):
        pane = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(pane, padding=8)
        right = ttk.Frame(pane, padding=8)
        pane.add(left, weight=0)
        pane.add(right, weight=1)

        ttk.Label(left, text="Этапы установки", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.stage_list = tk.Listbox(left, width=34, height=20, exportselection=False)
        self.stage_list.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.stage_list.bind("<<ListboxSelect>>", self._on_list_select)

        right_pane = ttk.Panedwindow(right, orient=tk.VERTICAL)
        right_pane.pack(fill=tk.BOTH, expand=True)

        instr_frame = ttk.LabelFrame(right_pane, text="Инструкция по этапу", padding=4)
        self.instruction_view = HtmlFrame(instr_frame, messages_enabled=False)
        self.instruction_view.pack(fill=tk.BOTH, expand=True)
        right_pane.add(instr_frame, weight=3)

        log_frame = ttk.LabelFrame(right_pane, text="Лог", padding=4)
        self.log_view = scrolledtext.ScrolledText(log_frame, height=8, state="disabled",
                                                    font=("Consolas", 9))
        self.log_view.pack(fill=tk.BOTH, expand=True)
        right_pane.add(log_frame, weight=1)

        self.action_frame = ttk.LabelFrame(right, text="Действие", padding=8)
        self.action_frame.pack(fill=tk.X, pady=(8, 0))

    def _populate_stage_list(self):
        self.stage_list.delete(0, tk.END)
        for i, stage in enumerate(self.stages):
            self.stage_list.insert(tk.END, self._stage_label(i, stage))

    def _stage_label(self, index, stage):
        mark = "✔" if index in self.done else " "
        return f"[{mark}] {index + 1}. {stage['title']}  ({TYPE_LABELS[stage['type']]})"

    def _refresh_stage_list_labels(self):
        for i, stage in enumerate(self.stages):
            self.stage_list.delete(i)
            self.stage_list.insert(i, self._stage_label(i, stage))
        self.stage_list.selection_set(self.current_index)

    # ------------------------------------------------------------------
    # Выбор этапа
    # ------------------------------------------------------------------
    def _on_list_select(self, _event=None):
        sel = self.stage_list.curselection()
        if not sel:
            return
        self._select_stage(sel[0])

    def _select_stage(self, index):
        self.current_index = index
        self.stage_list.selection_clear(0, tk.END)
        self.stage_list.selection_set(index)
        self.stage_list.see(index)

        stage = self.stages[index]
        html_path = stage_instruction_html_path(self.model, stage)
        if html_path:
            self.instruction_view.load_file(str(html_path))
        elif stage.get("description"):
            self.instruction_view.load_html(_text_to_html(stage["description"]))
        else:
            self.instruction_view.load_html(PLACEHOLDER_HTML)

        self._build_action_area(index, stage)

    # ------------------------------------------------------------------
    # Область действия — своя для каждого типа этапа
    # ------------------------------------------------------------------
    def _build_action_area(self, index, stage):
        for child in self.action_frame.winfo_children():
            child.destroy()

        stage_type = stage["type"]
        busy = self.install_runner.running
        if stage_type == "manual":
            ttk.Label(self.action_frame,
                      text="Выполните шаги из инструкции на самой магнитоле, "
                           "затем отметьте этап выполненным.",
                      wraplength=560).pack(anchor="w", pady=(0, 8))
            ttk.Button(self.action_frame, text="Готово, следующий этап →",
                       command=lambda: self._mark_done(index)).pack(anchor="w")

        elif stage_type == "usb":
            ttk.Button(self.action_frame, text="Подготовить флешку для этого этапа...",
                       command=lambda: self._run_usb_stage(index, stage)).pack(anchor="w")

        elif stage_type == "adb":
            row = ttk.Frame(self.action_frame)
            row.pack(fill=tk.X)
            ttk.Label(row, text="Устройство:").pack(side="left")
            self.device_combo = ttk.Combobox(row, state="readonly", width=36)
            self.device_combo.pack(side="left", padx=(6, 6))
            ttk.Button(row, text="Обновить", command=self._refresh_devices).pack(side="left")
            self._refresh_devices()

            btn_row = ttk.Frame(self.action_frame)
            btn_row.pack(fill=tk.X, pady=(8, 0))
            self.adb_start_btn = ttk.Button(btn_row, text="Начать этот этап",
                                             command=lambda: self._run_adb_stage(index, stage))
            self.adb_start_btn.pack(side="left")
            self.adb_stop_btn = ttk.Button(btn_row, text="Стоп", command=self._stop_adb_stage,
                                            state="normal" if busy else "disabled")
            self.adb_stop_btn.pack(side="left", padx=(6, 0))
            if busy:
                self.adb_start_btn.config(state="disabled")

    # ------------------------------------------------------------------
    # USB-этап — переиспользуем существующий диалог флешки
    # ------------------------------------------------------------------
    def _run_usb_stage(self, index, stage):
        def on_finished(success):
            if success:
                self._mark_done(index, advance=True)
            self._log(f"Этап {index + 1} ({stage['title']}): "
                      f"{'выполнен' if success else 'завершился с ошибкой'}.")

        UsbDialog(self, self.base_dir, self.model, self.selected_apks,
                   run_fn=stage["run"], title_suffix=stage["title"], on_finished=on_finished)

    # ------------------------------------------------------------------
    # ADB-этап — встроенный запуск через InstallRunner
    # ------------------------------------------------------------------
    def _selected_device_serial(self):
        label = self.device_combo.get() if hasattr(self, "device_combo") else ""
        return self.device_by_label.get(label)

    def _refresh_devices(self):
        if not hasattr(self, "device_combo"):
            return
        devices = list_devices(self.adb_path)
        self.device_by_label = {}
        labels = []
        for d in devices:
            label = d["serial"]
            if d["model"]:
                label += f"  ({d['model']})"
            if d["state"] != "device":
                label += f"  [{d['state']}]"
            labels.append(label)
            self.device_by_label[label] = d["serial"] if d["state"] == "device" else None
        self.device_combo["values"] = labels
        if labels:
            self.device_combo.current(0)
        else:
            self.device_combo.set("")

    def _run_adb_stage(self, index, stage):
        device = self._selected_device_serial()
        if not device:
            if not messagebox.askyesno(
                self.title(),
                "Не выбрано подключённое устройство ADB. Продолжить всё равно?",
            ):
                return

        self._pending_adb_index = index
        self.adb_start_btn.config(state="disabled")
        self.adb_stop_btn.config(state="normal")
        self._log(f"=== Этап {index + 1}: {stage['title']} ===")
        try:
            self.install_runner.start(self.model, device, self.selected_apks, run_fn=stage["run"])
        except RuntimeError as exc:
            messagebox.showerror(self.title(), str(exc))
            self.adb_start_btn.config(state="normal")
            self.adb_stop_btn.config(state="disabled")

    def _stop_adb_stage(self):
        if self.install_runner.running:
            self.install_runner.cancel()
            self._log("Останавливаю этап...")

    # ------------------------------------------------------------------
    def _mark_done(self, index, advance=True):
        self.done.add(index)
        self._refresh_stage_list_labels()
        if advance and index + 1 < len(self.stages):
            self._select_stage(index + 1)
        else:
            self._select_stage(index)
        if len(self.done) == len(self.stages):
            messagebox.showinfo(self.title(), "Все этапы установки отмечены как выполненные.")

    # ------------------------------------------------------------------
    # Логи/колбэки ADB-этапа из фонового потока — только через очередь
    # ------------------------------------------------------------------
    def _on_log_threaded(self, message):
        self._log_queue.put(("log", message))

    def _on_finished_threaded(self, success, message):
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
                    if hasattr(self, "adb_start_btn"):
                        self.adb_start_btn.config(state="normal")
                        self.adb_stop_btn.config(state="disabled")
                    index = getattr(self, "_pending_adb_index", None)
                    if index is not None and success:
                        self._mark_done(index, advance=True)
                    if not success:
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
        if self.install_runner.running:
            if not messagebox.askyesno(self.title(), "Этап ещё выполняется. Закрыть окно?"):
                return
            self.install_runner.cancel()
        self.destroy()
