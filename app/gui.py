"""Главное окно приложения."""
import queue
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk, scrolledtext, messagebox

from tkinterweb import HtmlFrame

from .adb_utils import find_adb_path, list_devices
from .runner import InstallRunner
from .scanner import scan_cars, scan_apks
from .stage_wizard import StageWizard
from .usb_dialog import UsbDialog

APP_TITLE = "Magic SQD — установщик приложений для мультимедиа"

PLACEHOLDER_HTML = (
    "<body style='font-family:Segoe UI, sans-serif; color:#666; padding:16px'>"
    "<p>Выберите марку и модель слева, чтобы увидеть инструкцию по получению "
    "доступа к ADB на этой магнитоле.</p></body>"
)
NO_INSTRUCTION_HTML = (
    "<body style='font-family:Segoe UI, sans-serif; color:#a33; padding:16px'>"
    "<p>Для этой модели нет файла instruction.html.</p></body>"
)


class App:
    def __init__(self, base_dir: Path, root: tk.Tk):
        """root — уже созданный (и настроенный вызывающим кодом) корень Tk.
        Так сплеш-скрин в main.py и главное окно используют один и тот же
        Tcl-интерпретатор — на Windows это важно: если создать/уничтожить
        один tk.Tk() (сплеш), а потом создать ещё один tk.Tk() для главного
        окна, у второго корня перестаёт применяться iconbitmap."""
        self.root = root
        self.base_dir = base_dir
        self.cars_dir = base_dir / "cars"
        self.apk_dir = base_dir / "apk"
        self.adb_path = find_adb_path(base_dir)

        shared_dir = self.cars_dir / "_shared"
        if shared_dir.exists():
            sys.path.insert(0, str(shared_dir))

        self.root.title(APP_TITLE)
        self.root.geometry("1200x860")
        self.root.minsize(900, 640)
        self._set_window_icon()

        self.brands = scan_cars(self.cars_dir)
        self.shared_apks = scan_apks(self.apk_dir)
        self.current_model = None
        self.apk_vars = []  # [(ApkInfo, BooleanVar), ...]
        self.device_by_label = {}

        self._log_queue = queue.Queue()
        self.runner = InstallRunner(self.adb_path, self._on_log_threaded, self._on_finished_threaded)

        self._build_ui()
        self._populate_brands()
        self._refresh_devices()
        self.root.after(100, self._drain_log_queue)

        if not self.brands:
            self._log(f"В папке {self.cars_dir} не найдено ни одной марки/модели.")

    def _set_window_icon(self):
        icon_path = self.base_dir / "assets" / "icon.ico"
        if icon_path.exists():
            try:
                self.root.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # Построение интерфейса
    # ------------------------------------------------------------------
    def _build_ui(self):
        root_pane = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        root_pane.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(root_pane, padding=8)
        right = ttk.Frame(root_pane, padding=8)
        root_pane.add(left, weight=0)
        root_pane.add(right, weight=1)

        ttk.Label(left, text="Марка авто", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.brand_list = tk.Listbox(left, height=6, exportselection=False)
        self.brand_list.pack(fill=tk.X, pady=(0, 8))
        self.brand_list.bind("<<ListboxSelect>>", self._on_brand_selected)

        ttk.Label(left, text="Модель", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.model_list = tk.Listbox(left, height=8, exportselection=False)
        self.model_list.pack(fill=tk.X, pady=(0, 8))
        self.model_list.bind("<<ListboxSelect>>", self._on_model_selected)

        device_frame = ttk.LabelFrame(left, text="Устройство ADB", padding=6)
        device_frame.pack(fill=tk.X, pady=(0, 8))
        self.device_combo = ttk.Combobox(device_frame, state="readonly")
        self.device_combo.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(device_frame, text="Обновить список устройств",
                   command=self._refresh_devices).pack(fill=tk.X)

        # Кнопки и статус закрепляем снизу колонки (side="bottom") и пакуем их
        # ДО списка APK — иначе при нехватке высоты окна растягивающийся список
        # (fill=BOTH, expand=True) заберёт себе всё место и кнопки окажутся за
        # пределами окна. У списка APK уже есть собственная прокрутка, поэтому
        # это он должен ужиматься первым, а не кнопки.
        self.status_var = tk.StringVar(value="Готово")
        ttk.Label(left, textvariable=self.status_var, foreground="#555").pack(
            side=tk.BOTTOM, anchor="w", pady=(6, 0))

        self.stages_btn = ttk.Button(left, text="Установка по этапам...",
                                      command=self._open_stage_wizard, state="disabled")
        self.stages_btn.pack(side=tk.BOTTOM, fill=tk.X)

        self.usb_btn = ttk.Button(left, text="Через USB-флешку...", command=self._open_usb_dialog,
                                   state="disabled")
        self.usb_btn.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 8))

        self.stop_btn = ttk.Button(left, text="Стоп", command=self._stop_install, state="disabled")
        self.stop_btn.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 8))

        self.install_btn = ttk.Button(left, text="Установить по ADB", command=self._start_install,
                                       state="disabled")
        self.install_btn.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 2))

        apk_frame = ttk.LabelFrame(left, text="Стандартные приложения", padding=6)
        apk_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.apk_canvas = apk_canvas = tk.Canvas(apk_frame, highlightthickness=0, width=260)
        apk_scroll = ttk.Scrollbar(apk_frame, orient="vertical", command=apk_canvas.yview)
        self.apk_inner = ttk.Frame(apk_canvas)
        self.apk_inner.bind(
            "<Configure>",
            lambda e: apk_canvas.configure(scrollregion=apk_canvas.bbox("all")),
        )
        apk_canvas.create_window((0, 0), window=self.apk_inner, anchor="nw")
        apk_canvas.configure(yscrollcommand=apk_scroll.set)
        apk_canvas.pack(side="left", fill="both", expand=True)
        apk_scroll.pack(side="right", fill="y")
        self._bind_mousewheel(apk_canvas)
        self._bind_mousewheel(self.apk_inner)
        self._populate_apks()

        right_pane = ttk.Panedwindow(right, orient=tk.VERTICAL)
        right_pane.pack(fill=tk.BOTH, expand=True)

        instr_frame = ttk.LabelFrame(
            right_pane, text="Инструкция по модели (доступ к ADB и особенности установки)", padding=4
        )
        self.instruction_view = HtmlFrame(instr_frame, messages_enabled=False)
        self.instruction_view.pack(fill=tk.BOTH, expand=True)
        self.instruction_view.load_html(PLACEHOLDER_HTML)
        right_pane.add(instr_frame, weight=3)

        log_frame = ttk.LabelFrame(right_pane, text="Лог установки", padding=4)
        self.log_view = scrolledtext.ScrolledText(log_frame, height=10, state="disabled",
                                                    font=("Consolas", 9))
        self.log_view.pack(fill=tk.BOTH, expand=True)
        right_pane.add(log_frame, weight=1)

    def _populate_apks(self):
        for child in self.apk_inner.winfo_children():
            child.destroy()
        self.apk_vars = []

        if not self.shared_apks:
            ttk.Label(self.apk_inner, text=f"Нет APK в папке\n{self.apk_dir}",
                      foreground="#888", wraplength=230).pack(anchor="w", padx=4, pady=4)
            return

        for apk in self.shared_apks:
            var = tk.BooleanVar(value=False)
            text = apk.name
            cb = ttk.Checkbutton(self.apk_inner, text=text, variable=var)
            cb.pack(anchor="w", padx=2, pady=1)
            self._bind_mousewheel(cb)
            if apk.description:
                desc = ttk.Label(self.apk_inner, text=apk.description, foreground="#888",
                                  wraplength=230, font=("Segoe UI", 8))
                desc.pack(anchor="w", padx=20)
                self._bind_mousewheel(desc)
            self.apk_vars.append((apk, var))

    def _bind_mousewheel(self, widget):
        """Прокрутка списка APK колёсиком мыши — по умолчанию Canvas её не
        поддерживает, а колёсико над чекбоксом/подписью не долетает до
        родительского Canvas без явной привязки на каждый виджет."""
        widget.bind("<MouseWheel>",
                     lambda e: self.apk_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))

    # ------------------------------------------------------------------
    # Марки / модели
    # ------------------------------------------------------------------
    def _populate_brands(self):
        self.brand_list.delete(0, tk.END)
        for brand in self.brands:
            self.brand_list.insert(tk.END, brand)

    def _on_brand_selected(self, _event=None):
        selection = self.brand_list.curselection()
        self.model_list.delete(0, tk.END)
        self.current_model = None
        self.install_btn.config(state="disabled")
        self.usb_btn.config(state="disabled")
        self.stages_btn.config(state="disabled")
        self.instruction_view.load_html(PLACEHOLDER_HTML)
        if not selection:
            return
        brand = self.brand_list.get(selection[0])
        for model in self.brands.get(brand, []):
            self.model_list.insert(tk.END, model.name)

    def _on_model_selected(self, _event=None):
        brand_sel = self.brand_list.curselection()
        model_sel = self.model_list.curselection()
        if not brand_sel or not model_sel:
            return
        brand = self.brand_list.get(brand_sel[0])
        model = self.brands[brand][model_sel[0]]
        self.current_model = model

        if model.instruction_html:
            self.instruction_view.load_file(str(model.instruction_html))
        else:
            self.instruction_view.load_html(NO_INSTRUCTION_HTML)

        can_install = model.install_script is not None and not self.runner.running
        self.install_btn.config(state="normal" if can_install else "disabled")
        self.usb_btn.config(state="normal")
        self.stages_btn.config(state="normal" if model.stages_script else "disabled")
        if not model.install_script:
            self._log(f"[{brand} / {model.name}] Внимание: нет install.py в {model.dir}")

    # ------------------------------------------------------------------
    # Устройства ADB
    # ------------------------------------------------------------------
    def _refresh_devices(self):
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
        self._log(f"Найдено устройств: {len(devices)}")

    def _selected_device_serial(self):
        label = self.device_combo.get()
        return self.device_by_label.get(label)

    # ------------------------------------------------------------------
    # Установка
    # ------------------------------------------------------------------
    def _start_install(self):
        if not self.current_model or not self.current_model.install_script:
            messagebox.showwarning(APP_TITLE, "Сначала выберите модель с установочным скриптом.")
            return

        device = self._selected_device_serial()
        if not device:
            if not messagebox.askyesno(
                APP_TITLE,
                "Не выбрано подключённое устройство ADB. Продолжить всё равно?\n"
                "(скрипт установки сам попробует найти устройство)",
            ):
                return

        selected_apks = [apk.path for apk, var in self.apk_vars if var.get()]

        self.install_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status_var.set("Установка...")
        self._log(f"=== Установка: {self.current_model.brand} / {self.current_model.name} ===")

        try:
            self.runner.start(self.current_model, device, selected_apks)
        except RuntimeError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            self.install_btn.config(state="normal")
            self.stop_btn.config(state="disabled")

    def _stop_install(self):
        if self.runner.running:
            self.runner.cancel()
            self._log("Останавливаю... (скрипт завершится на ближайшей проверке)")

    def _open_usb_dialog(self):
        if not self.current_model:
            return
        selected_apks = [apk.path for apk, var in self.apk_vars if var.get()]
        UsbDialog(self.root, self.base_dir, self.current_model, selected_apks)

    def _open_stage_wizard(self):
        if not self.current_model or not self.current_model.stages_script:
            return
        selected_apks = [apk.path for apk, var in self.apk_vars if var.get()]
        StageWizard(self.root, self.base_dir, self.adb_path, self.current_model, selected_apks)

    # ------------------------------------------------------------------
    # Логи и колбэки из фонового потока (только через очередь!)
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
                    self.status_var.set("Готово" if success else "Ошибка / остановлено")
                    self.stop_btn.config(state="disabled")
                    can_install = self.current_model and self.current_model.install_script
                    self.install_btn.config(state="normal" if can_install else "disabled")
                    if success:
                        messagebox.showinfo(APP_TITLE, message)
                    else:
                        messagebox.showerror(APP_TITLE, message)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_log_queue)

    def _log(self, message):
        self.log_view.config(state="normal")
        self.log_view.insert(tk.END, str(message) + "\n")
        self.log_view.see(tk.END)
        self.log_view.config(state="disabled")
