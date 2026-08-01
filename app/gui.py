"""Главное окно приложения."""
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import customtkinter
from tkinterweb import HtmlFrame

from .add_car_dialog import AddCarDialog
from .adb_utils import Adb, find_adb_path, kill_server, list_devices
from .content_config import get_base_url
from .content_sync import (list_shared_apk_catalog, model_needs_download, sync_model_files,
                            sync_scripts, sync_shared_apks)
from .ctk_listbox import CTkListbox
from .install_context import InstallCancelled
from .ping_client import PingError, get_or_create_client_id, send_ping
from .report_dialog import ReportDialog
from .scanner import scan_cars, scan_apks
from .stage_wizard import StageWizard
from .submit_config import get_submit_config
from . import theme

APP_TITLE = "Magic SQD — установщик приложений для мультимедиа"
# "Пульс" на сервер для счётчика пользователей в админке (см.
# app/ping_client.py) — раз в 3 минуты, с запасом внутри "online"-окна
# сервера (ONLINE_WINDOW_SECONDS=5 минут в server/backend.py), чтобы один
# пропущенный пульс (временный сбой сети) не сбрасывал пользователя в
# "офлайн".
PING_INTERVAL_MS = 3 * 60 * 1000

PLACEHOLDER_HTML = (
    f"<body style='font-family:Segoe UI, sans-serif; background:{theme.BG_CARD}; "
    f"color:{theme.TEXT_DIM}; padding:16px'>"
    "<p>Выберите марку и модель слева, чтобы увидеть инструкцию по получению "
    "доступа к ADB на этой магнитоле.</p></body>"
)
NO_INSTRUCTION_HTML = (
    f"<body style='font-family:Segoe UI, sans-serif; background:{theme.BG_CARD}; "
    f"color:{theme.DANGER}; padding:16px'>"
    "<p>Для этой модели нет файла instruction.html.</p></body>"
)


class App:
    def __init__(self, base_dir: Path, root):
        """root — уже созданный (и настроенный вызывающим кодом) корень
        CTk. Так сплеш-скрин в main.py и главное окно используют один и
        тот же Tcl-интерпретатор — на Windows это важно: если создать/
        уничтожить один корень (сплеш), а потом создать для главного окна
        ещё один — у второго перестаёт применяться iconbitmap."""
        self.root = root
        self.base_dir = base_dir
        self.cars_dir = base_dir / "cars"
        self.apk_dir = base_dir / "apk"
        self.adb_path = find_adb_path(base_dir)

        shared_dir = self.cars_dir / "_shared"
        if shared_dir.exists():
            sys.path.insert(0, str(shared_dir))

        self.root.title(APP_TITLE)
        self.root.geometry("1380x990")
        self.root.minsize(1040, 740)

        self.shared_apks = scan_apks(self.apk_dir)
        self.current_model = None
        self.device_by_label = {}
        self._console_queue = queue.Queue()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()
        self._sync_scripts_from_server()
        self._sync_shared_apks_catalog()
        self._client_id = get_or_create_client_id(self.base_dir)
        self._start_heartbeat()
        self.brands = scan_cars(self.cars_dir)
        self._populate_brands()
        self._refresh_devices()
        self.root.after(100, self._drain_console_queue)

        if get_base_url(self.base_dir):
            self.download_all_btn.configure(state="normal")

        if not self.brands:
            self._log(f"В папке {self.cars_dir} не найдено ни одной марки/модели.")

    def _sync_scripts_from_server(self):
        """Тихое автообновление скриптов/инструкций моделей (cars/) со
        своего сервера при каждом запуске — без APK (см. content_sync.py,
        server/README.md). Если server.json не настроен, sync_scripts()
        молча ничего не делает. Отдельно логирует марки/модели, которых не
        было в cars/ до синхронизации — чтобы в логе было явно видно, что
        именно появилось нового, а не просто число скачанных файлов."""
        before = scan_cars(self.cars_dir)
        before_keys = {(brand, m.name) for brand, models in before.items() for m in models}
        try:
            downloaded = sync_scripts(self.base_dir, self.cars_dir, log=self._log)
        except Exception as exc:  # noqa: BLE001 - сбой сети не должен ломать запуск
            self._log(f"Не удалось проверить обновления моделей на сервере: {exc}")
            return
        if not downloaded:
            return
        after = scan_cars(self.cars_dir)
        after_keys = {(brand, m.name) for brand, models in after.items() for m in models}
        new_keys = sorted(after_keys - before_keys)
        if new_keys:
            names = ", ".join(f"{brand} / {model}" for brand, model in new_keys)
            self._log(f"Новые модели с сервера: {names}.")
        self._log(f"Обновлено файлов моделей с сервера: {downloaded}.")

    def _sync_shared_apks_catalog(self):
        """Обновляет ТОЛЬКО список общей библиотеки apk/ с сервера при
        каждом запуске — имена/размеры, без скачивания самих файлов (см.
        content_sync.list_shared_apk_catalog: лёгкий обход папок, как и
        cars/-скрипты выше). Ещё не скачанные записи попадают в
        self.shared_apks с ApkInfo.remote_only=True — дерево выбора
        приложений (stage_wizard.py) сразу видит полный список, а не
        только то, что уже когда-то скачали кнопкой "Скачать". Сами файлы
        докачиваются по одному непосредственно перед установкой (см.
        runner.py/usb_dialog.py: ensure_apks_downloaded), а не здесь —
        полная докачка всей библиотеки заранее по-прежнему только по
        кнопке "Скачать"/"Скачать всё"."""
        try:
            catalog = list_shared_apk_catalog(self.base_dir)
        except Exception as exc:  # noqa: BLE001 - сбой сети не должен ломать запуск
            self._log(f"Не удалось получить список общей библиотеки приложений: {exc}")
            return
        if catalog:
            self.shared_apks = scan_apks(self.apk_dir, remote_catalog=catalog)

    def _start_heartbeat(self):
        """Периодический "пульс" на сервер (см. app/ping_client.py) — только
        для счётчика пользователей в админке (total/online), ни на что в
        самой программе не влияет. Тихо ничего не делает, если submit.json
        не настроен. Сам пульс шлётся из отдельного короткоживущего фонового
        потока, чтобы сетевая задержка не подвешивала интерфейс — периодичность
        держится через self.root.after на главном потоке."""
        config = get_submit_config(self.base_dir)
        if config:
            threading.Thread(target=self._send_ping_silently, args=(config,), daemon=True).start()
        self.root.after(PING_INTERVAL_MS, self._start_heartbeat)

    def _send_ping_silently(self, config):
        try:
            send_ping(self._client_id, config)
        except PingError:
            pass  # счётчик пользователей необязателен — сбой сети тут не показываем

    def _on_close(self):
        kill_server(self.adb_path)
        self.root.destroy()

    # ------------------------------------------------------------------
    # Построение интерфейса
    # ------------------------------------------------------------------
    def _section_label(self, parent, text):
        return customtkinter.CTkLabel(parent, text=text, font=theme.FONT_BOLD,
                                       text_color=theme.TEXT_DIM, anchor="w")

    def _build_ui(self):
        root_pane = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        root_pane.pack(fill=tk.BOTH, expand=True)

        left = customtkinter.CTkFrame(root_pane, fg_color=theme.BG, corner_radius=0)
        right = customtkinter.CTkFrame(root_pane, fg_color=theme.BG, corner_radius=0)
        root_pane.add(left, weight=0)
        root_pane.add(right, weight=1)

        left_inner = customtkinter.CTkFrame(left, fg_color="transparent")
        left_inner.pack(fill="both", expand=True, padx=8, pady=8)
        right_inner = customtkinter.CTkFrame(right, fg_color="transparent")
        right_inner.pack(fill="both", expand=True, padx=8, pady=8)

        self._section_label(left_inner, "Марка авто").pack(anchor="w")
        self.brand_list = CTkListbox(left_inner, height=6)
        self.brand_list.pack(fill=tk.X, pady=(0, 8))
        self.brand_list.bind("<<ListboxSelect>>", self._on_brand_selected)

        self._section_label(left_inner, "Модель").pack(anchor="w")
        self.model_list = CTkListbox(left_inner, height=8)
        self.model_list.pack(fill=tk.BOTH, expand=True, pady=(0, 4))
        self.model_list.bind("<<ListboxSelect>>", self._on_model_selected)

        customtkinter.CTkButton(left_inner, text="Добавить машину...",
                                 command=self._open_add_car_dialog,
                                 **theme.secondary_button()).pack(fill=tk.X, pady=(0, 2))
        self.edit_car_btn = customtkinter.CTkButton(
            left_inner, text="Изменить машину...", command=self._open_edit_car_dialog,
            state="disabled", **theme.secondary_button())
        self.edit_car_btn.pack(fill=tk.X, pady=(0, 2))
        self.report_btn = customtkinter.CTkButton(
            left_inner, text="Сообщить о проблеме...", command=self._open_report_dialog,
            state="disabled", **theme.secondary_button())
        self.report_btn.pack(fill=tk.X, pady=(0, 2))
        self.download_all_btn = customtkinter.CTkButton(
            left_inner, text="Скачать всё с сервера...", command=self._open_download_all_dialog,
            state="disabled", **theme.secondary_button())
        self.download_all_btn.pack(fill=tk.X, pady=(0, 8))

        self._section_label(left_inner, "Устройство ADB").pack(anchor="w", pady=(0, 4))
        self.device_combo = customtkinter.CTkOptionMenu(left_inner, values=[""], font=theme.FONT,
                                                          fg_color=theme.BG_CARD,
                                                          button_color=theme.BORDER,
                                                          button_hover_color=theme.ACCENT,
                                                          text_color=theme.TEXT)
        self.device_combo.pack(fill=tk.X, pady=(0, 4))
        customtkinter.CTkButton(left_inner, text="Обновить список устройств",
                                 command=self._refresh_devices,
                                 **theme.secondary_button()).pack(fill=tk.X, pady=(0, 8))

        self.status_var = tk.StringVar(value="Готово")
        customtkinter.CTkLabel(left_inner, textvariable=self.status_var, font=theme.FONT_SMALL,
                                text_color=theme.TEXT_DIM, anchor="w").pack(
            side=tk.BOTTOM, anchor="w", pady=(6, 0))

        self.install_btn = customtkinter.CTkButton(
            left_inner, text="Установка", command=self._open_stage_wizard,
            state="disabled", **theme.accent_button())
        self.install_btn.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 2))
        self.download_model_btn = customtkinter.CTkButton(
            left_inner, text="Скачать файлы модели...", command=self._open_download_model_dialog,
            **theme.secondary_button())
        # Показываем только когда реально нужно (сервер настроен и файлы,
        # похоже, ещё не скачаны) — pack/pack_forget вместо state=disabled,
        # чтобы не путать с обычной "неактивной" кнопкой.

        right_pane = ttk.Panedwindow(right_inner, orient=tk.VERTICAL)

        instr_wrapper, instr_content = theme.build_card(
            right_pane, "Инструкция по модели (доступ к ADB и особенности установки)")
        self.instruction_view = HtmlFrame(instr_content, messages_enabled=False)
        self.instruction_view.pack(fill=tk.BOTH, expand=True)
        theme.load_dark_html(self.instruction_view, PLACEHOLDER_HTML)
        right_pane.add(instr_wrapper, weight=3)

        log_wrapper, log_content = theme.build_card(right_pane, "Лог")
        self.log_view = customtkinter.CTkTextbox(log_content, height=150, state="disabled",
                                                   font=theme.FONT_MONO, fg_color="transparent",
                                                   text_color=theme.TEXT)
        self.log_view.pack(fill=tk.BOTH, expand=True)
        right_pane.add(log_wrapper, weight=1)

        # Мини-консоль ADB вместо отдельного окна "Консоль ADB..." — команда
        # шлётся как "adb shell <текст>" выбранному устройству, вывод идёт в
        # лог главного окна выше. Пакуем ДО right_pane (со side="bottom") —
        # иначе Panedwindow с инструкцией/логом (fill=BOTH, expand=True)
        # разбирает себе всё место первым и строка ввода уходит за пределы
        # окна при обычном размере (тот же баг, что был с кнопкой "Готово" в
        # мастере установки).
        console_row = customtkinter.CTkFrame(right_inner, fg_color="transparent")
        console_row.pack(side="bottom", fill=tk.X, pady=(4, 0))
        customtkinter.CTkLabel(console_row, text="adb shell", font=theme.FONT,
                                text_color=theme.TEXT_DIM).pack(side="left")
        self.console_var = tk.StringVar()
        console_entry = customtkinter.CTkEntry(console_row, textvariable=self.console_var,
                                                 fg_color=theme.BG_CARD, text_color=theme.TEXT,
                                                 border_color=theme.BORDER, font=theme.FONT)
        console_entry.pack(side="left", fill=tk.X, expand=True, padx=(6, 6))
        console_entry.bind("<Return>", lambda e: self._send_console_command())
        customtkinter.CTkButton(console_row, text="Отправить", command=self._send_console_command,
                                 **theme.secondary_button()).pack(side="left")

        right_pane.pack(fill=tk.BOTH, expand=True)

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
        self.install_btn.configure(state="disabled")
        self.edit_car_btn.configure(state="disabled")
        self.report_btn.configure(state="disabled")
        self.download_model_btn.pack_forget()
        theme.load_dark_html(self.instruction_view, PLACEHOLDER_HTML)
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
            theme.load_dark_html_file(self.instruction_view, model.instruction_html)
        else:
            theme.load_dark_html(self.instruction_view, NO_INSTRUCTION_HTML)

        self.install_btn.configure(state="normal" if model.stages_script else "disabled")
        if not model.stages_script:
            self._log(f"[{brand} / {model.name}] Внимание: нет stages.py в {model.dir}")

        self.report_btn.configure(state="normal")

        has_wizard_spec = (model.dir / "_wizard_spec.json").exists()
        self.edit_car_btn.configure(state="normal" if has_wizard_spec else "disabled")

        self.download_model_btn.pack_forget()
        if get_base_url(self.base_dir) and model_needs_download(model):
            self.download_model_btn.pack(side=tk.BOTTOM, fill=tk.X, pady=(0, 2), before=self.install_btn)

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

        self.device_combo.configure(values=labels)
        self.device_combo.set(labels[0] if labels else "")
        self._log(f"Найдено устройств: {len(devices)}")

    def _selected_device_serial(self):
        label = self.device_combo.get()
        return self.device_by_label.get(label)

    def _send_console_command(self):
        command = self.console_var.get().strip()
        if not command:
            return
        self.console_var.set("")
        device = self._selected_device_serial()
        threading.Thread(target=self._console_worker, args=(command, device), daemon=True).start()

    def _console_worker(self, command, device):
        adb = Adb(self.adb_path, device, log=self._console_queue.put)
        try:
            adb.shell(command, check=False)
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку
            self._console_queue.put(f"Ошибка: {exc}")

    def _drain_console_queue(self):
        try:
            while True:
                self._log(self._console_queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._drain_console_queue)

    # ------------------------------------------------------------------
    # Добавление новой модели
    # ------------------------------------------------------------------
    def _open_add_car_dialog(self):
        AddCarDialog(self.root, self.base_dir, self.cars_dir, self.brands.keys(), self._on_car_created)

    def _open_edit_car_dialog(self):
        if not self.current_model:
            return
        AddCarDialog(self.root, self.base_dir, self.cars_dir, self.brands.keys(), self._on_car_created,
                     edit_model_dir=self.current_model.dir)

    def _open_report_dialog(self):
        if not self.current_model:
            return
        submit_config = get_submit_config(self.base_dir)
        if not submit_config:
            messagebox.showinfo(
                "Magic SQD",
                "Отправка обращений не настроена (нет submit.json рядом с программой).")
            return
        ReportDialog(self.root, self.current_model.brand, self.current_model.name, submit_config)

    def _on_car_created(self, brand, model_name):
        self.brands = scan_cars(self.cars_dir)
        self._populate_brands()
        brand_names = list(self.brands.keys())
        if brand not in brand_names:
            self._log(f"Добавлена модель: {brand} / {model_name}")
            return
        brand_index = brand_names.index(brand)
        self.brand_list.selection_clear(0, tk.END)
        self.brand_list.selection_set(brand_index)
        self.brand_list.see(brand_index)
        self._on_brand_selected()

        model_names = [m.name for m in self.brands[brand]]
        if model_name in model_names:
            model_index = model_names.index(model_name)
            self.model_list.selection_clear(0, tk.END)
            self.model_list.selection_set(model_index)
            self.model_list.see(model_index)
            self._on_model_selected()
        self._log(f"Добавлена модель: {brand} / {model_name}")

    # ------------------------------------------------------------------
    # Установка
    # ------------------------------------------------------------------
    def _open_stage_wizard(self):
        if not self.current_model or not self.current_model.stages_script:
            return
        StageWizard(self.root, self.base_dir, self.adb_path, self.current_model, self.shared_apks, self._log)

    # ------------------------------------------------------------------
    # Скачивание с сервера "по требованию"
    # ------------------------------------------------------------------
    def _open_download_model_dialog(self):
        if not self.current_model:
            return
        model = self.current_model

        def worker(log, check_cancelled):
            sync_model_files(self.base_dir, model, log=log, check_cancelled=check_cancelled)
            sync_shared_apks(self.base_dir, self.apk_dir, log=log, check_cancelled=check_cancelled)

        _DownloadProgressDialog(self.root, f"Скачивание файлов — {model.brand} / {model.name}", worker,
                                 on_done=self._after_download)

    def _open_download_all_dialog(self):
        def worker(log, check_cancelled):
            sync_scripts(self.base_dir, self.cars_dir, log=log)
            for models in self.brands.values():
                for model in models:
                    check_cancelled()
                    sync_model_files(self.base_dir, model, log=log, check_cancelled=check_cancelled)
            sync_shared_apks(self.base_dir, self.apk_dir, log=log, check_cancelled=check_cancelled)

        _DownloadProgressDialog(self.root, "Скачивание всех моделей и приложений", worker,
                                 on_done=self._after_download)

    def _after_download(self):
        """Вызывается (из главного потока) после закрытия диалога скачивания
        — общая библиотека apk/ могла обновиться, пересканируем её; кнопка
        "Скачать файлы модели" могла стать ненужной."""
        self.shared_apks = scan_apks(self.apk_dir)
        if self.current_model:
            self._on_model_selected()

    # ------------------------------------------------------------------
    def _log(self, message):
        self.log_view.configure(state="normal")
        self.log_view.insert(tk.END, str(message) + "\n")
        self.log_view.see(tk.END)
        self.log_view.configure(state="disabled")


class _DownloadProgressDialog(customtkinter.CTkToplevel):
    """Маленькое модальное окно "Скачивание..." для кнопок "Скачать файлы
    модели"/"Скачать всё с сервера" — worker_fn(log, check_cancelled)
    выполняется в фоновом потоке, лог/статус приходят через queue.Queue
    (тот же потокобезопасный приём, что уже используется по всему проекту —
    stage_wizard.py, add_car_dialog._SubmitProgressDialog)."""

    def __init__(self, parent, title, worker_fn, on_done=None):
        super().__init__(parent)
        theme.style_toplevel(self)
        self.title(title)
        self.geometry("550x370")
        self.minsize(480, 300)
        self.transient(parent)
        self.grab_set()
        self.on_done = on_done

        self._queue = queue.Queue()
        self._cancel_event = threading.Event()

        self.log_view = customtkinter.CTkTextbox(self, height=200, state="disabled",
                                                   font=theme.FONT_MONO, fg_color=theme.BG_CARD,
                                                   text_color=theme.TEXT)
        self.log_view.pack(fill="both", expand=True, padx=10, pady=(10, 4))
        self.progress = customtkinter.CTkProgressBar(self, mode="indeterminate",
                                                       progress_color=theme.ACCENT_2)
        self.progress.pack(fill="x", padx=10)
        self.progress.start()
        self.cancel_btn = customtkinter.CTkButton(self, text="Отмена", command=self._cancel,
                                                    **theme.secondary_button())
        self.cancel_btn.pack(pady=10)

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        threading.Thread(target=self._worker, args=(worker_fn,), daemon=True).start()
        self.after(100, self._drain_queue)

    def _cancel(self):
        self._cancel_event.set()
        self.cancel_btn.configure(state="disabled", text="Останавливаю...")

    def _check_cancelled(self):
        if self._cancel_event.is_set():
            raise InstallCancelled("Остановлено пользователем.")

    def _worker(self, worker_fn):
        try:
            worker_fn(self._queue.put, self._check_cancelled)
        except InstallCancelled:
            self._queue.put(("__done__", "Остановлено."))
            return
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку
            self._queue.put(("__done__", f"Ошибка: {exc}"))
            return
        self._queue.put(("__done__", "Готово."))

    def _drain_queue(self):
        try:
            while True:
                item = self._queue.get_nowait()
                if isinstance(item, tuple) and item and item[0] == "__done__":
                    self.progress.stop()
                    self._log(item[1])
                    if self.on_done:
                        self.on_done()
                    self.after(600, self.destroy)
                    return
                self._log(str(item))
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._drain_queue)

    def _log(self, message):
        self.log_view.configure(state="normal")
        self.log_view.insert(tk.END, str(message) + "\n")
        self.log_view.see(tk.END)
        self.log_view.configure(state="disabled")
