"""Диалог "Добавить машину.../Изменить машину..." — конструктор этапов
установки для модели (cars/<Марка>/<Модель>/), без ручного написания
install.py/stages.py. Собранный список этапов передаётся в
car_generator.create_car()/update_car(), которые и пишут файлы по уже
устоявшемуся в проекте паттерну ("apps"/"usb"/"adb"/"manual" — те же типы,
что и в stages.py, написанных вручную). В режиме редактирования (передан
edit_model_dir) поля марки/модели заблокированы, а форма предзаполняется
из car_generator.load_car_spec()."""
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import simpledialog, ttk, filedialog, messagebox

import customtkinter

from .car_generator import (CarGenerationError, NewCarSpec, StepSpec, StepVariant,
                             create_car, load_car_spec, update_car)
from .ctk_listbox import CTkListbox
from .instruction_editor import InstructionEditorDialog
from .submit_client import SubmitCancelled, SubmitError, submit_model
from .submit_config import get_submit_config
from . import instruction_html, theme

STEP_TYPE_LABELS = {
    "adb": "ADB-команды",
    "usb": "USB-флешка",
    "manual": "Ручной шаг",
    "apps": "Выбор приложений",
    "exe": "Готовый установщик (.exe)",
    "check": "Проверка/выбор",
}
STEP_TYPE_BY_LABEL = {label: value for value, label in STEP_TYPE_LABELS.items()}


class AddCarDialog(customtkinter.CTkToplevel):
    def __init__(self, parent, base_dir: Path, cars_dir: Path, existing_brands, on_created,
                 edit_model_dir: Path | None = None):
        """on_created(brand, model) вызывается после успешного создания/
        сохранения — gui.py обновляет списки марок/моделей и выбирает
        модель. edit_model_dir, если задан — открыть в режиме
        редактирования уже существующей (созданной этим же мастером ранее)
        модели вместо создания новой. base_dir нужен, чтобы проверить
        submit.json (отправка на проверку разработчику, см. submit_config.py)."""
        super().__init__(parent)
        theme.style_toplevel(self)
        self.base_dir = base_dir
        self.cars_dir = cars_dir
        self.on_created = on_created
        self.edit_model_dir = edit_model_dir
        # Path — импортированный файл; list[dict] — блоки из редактора
        # инструкции (см. app/instruction_editor.py)
        self._instruction_source: Path | list[dict] | None = None
        self._instruction_already_set = False
        # Какой вариант (StepSpec.variants) сейчас редактируется в форме
        # usb/apps-этапа с несколькими вариантами — сбрасывается при
        # переключении на другой этап (см. _select_step).
        self._editing_variant_index = 0

        loaded_spec = load_car_spec(edit_model_dir) if edit_model_dir else None
        if loaded_spec:
            self.steps: list[StepSpec] = loaded_spec.steps
        else:
            self.steps = [StepSpec(type="adb", title="Этап 1")]
        self.current_step_index = 0

        self.title("Изменить машину" if edit_model_dir else "Добавить машину")
        self.geometry("1040x880")
        self.minsize(900, 690)
        self.transient(parent)
        self.grab_set()

        self._build_ui(sorted(existing_brands), loaded_spec)
        self._select_step(0)

    # ------------------------------------------------------------------
    # Общая структура окна
    # ------------------------------------------------------------------
    def _build_ui(self, existing_brands, loaded_spec: NewCarSpec | None):
        btn_row = customtkinter.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", side="bottom", padx=10, pady=(0, 10))
        customtkinter.CTkButton(btn_row, text="Отмена", command=self.destroy,
                                 **theme.secondary_button()).pack(side="right")
        save_label = "Сохранить" if self.edit_model_dir else "Создать"
        customtkinter.CTkButton(btn_row, text=save_label, command=self._save,
                                 **theme.accent_button()).pack(side="right", padx=(0, 6))

        header = customtkinter.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", side="top", padx=10, pady=(10, 4))
        self._build_header(header, existing_brands, loaded_spec)

        body = customtkinter.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=10)
        self._build_steps_editor(body)

    def _build_header(self, header, existing_brands, loaded_spec: NewCarSpec | None):
        grid = customtkinter.CTkFrame(header, fg_color="transparent")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)

        editing = self.edit_model_dir is not None

        customtkinter.CTkLabel(grid, text="Марка", font=theme.FONT_BOLD,
                                text_color=theme.TEXT).grid(row=0, column=0, sticky="w")
        self.brand_var = tk.StringVar(value=self.edit_model_dir.parent.name if editing else "")
        brand_combo = customtkinter.CTkComboBox(
            grid, variable=self.brand_var, values=existing_brands,
            state="disabled" if editing else "normal", font=theme.FONT,
            fg_color=theme.BG_CARD, border_color=theme.BORDER, button_color=theme.BORDER,
            button_hover_color=theme.ACCENT, text_color=theme.TEXT,
            dropdown_fg_color=theme.BG_CARD, dropdown_text_color=theme.TEXT)
        brand_combo.grid(row=0, column=1, sticky="we", padx=(4, 12))

        customtkinter.CTkLabel(grid, text="Модель", font=theme.FONT_BOLD,
                                text_color=theme.TEXT).grid(row=0, column=2, sticky="w")
        self.model_var = tk.StringVar(value=self.edit_model_dir.name if editing else "")
        model_entry = customtkinter.CTkEntry(
            grid, textvariable=self.model_var, state="disabled" if editing else "normal",
            font=theme.FONT, fg_color=theme.BG_CARD, text_color=theme.TEXT,
            border_color=theme.BORDER)
        model_entry.grid(row=0, column=3, sticky="we", padx=(4, 0))

        conn_row = customtkinter.CTkFrame(header, fg_color="transparent")
        conn_row.pack(fill="x", pady=(6, 0))
        self.wifi_var = tk.BooleanVar(value=loaded_spec.wifi if loaded_spec else False)
        customtkinter.CTkCheckBox(
            conn_row, text="ADB-этапы подключаются по Wi-Fi (иначе — по USB/уже подключено)",
            variable=self.wifi_var, command=self._update_wifi_row, font=theme.FONT,
            text_color=theme.TEXT, fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
            border_color=theme.BORDER).pack(side="left")
        self.wifi_port_row = customtkinter.CTkFrame(conn_row, fg_color="transparent")
        customtkinter.CTkLabel(self.wifi_port_row, text="Порт:", text_color=theme.TEXT,
                                font=theme.FONT).pack(side="left", padx=(10, 2))
        self.wifi_port_var = tk.StringVar(value=str(loaded_spec.wifi_port if loaded_spec else 5555))
        customtkinter.CTkEntry(self.wifi_port_row, textvariable=self.wifi_port_var, width=60,
                                font=theme.FONT, fg_color=theme.BG_CARD, text_color=theme.TEXT,
                                border_color=theme.BORDER).pack(side="left")

        instr_row = customtkinter.CTkFrame(header, fg_color="transparent")
        instr_row.pack(fill="x", pady=(6, 0))
        customtkinter.CTkLabel(instr_row, text="Инструкция (обязательно):", text_color=theme.TEXT,
                                font=theme.FONT).pack(side="left")
        customtkinter.CTkButton(instr_row, text="Написать инструкцию...",
                                 command=self._open_instruction_editor,
                                 **theme.accent_button()).pack(side="left", padx=(6, 6))
        customtkinter.CTkButton(instr_row, text="Импортировать файл (HTML/TXT)...",
                                 command=self._pick_instruction,
                                 **theme.secondary_button()).pack(side="left", padx=(0, 6))
        if editing and self.edit_model_dir is not None and (self.edit_model_dir / "instruction.html").exists():
            self._instruction_already_set = True
            default_label = "instruction.html (уже задана, можно заменить)"
        else:
            default_label = "(не выбрано)"
        self.instruction_label_var = tk.StringVar(value=default_label)
        customtkinter.CTkLabel(instr_row, textvariable=self.instruction_label_var,
                                text_color=theme.TEXT_DIM, font=theme.FONT_SMALL).pack(side="left")

        self._update_wifi_row()

    def _update_wifi_row(self):
        if self.wifi_var.get():
            self.wifi_port_row.pack(side="left")
        else:
            self.wifi_port_row.pack_forget()

    def _pick_instruction(self):
        path = filedialog.askopenfilename(
            title="Файл инструкции", parent=self,
            filetypes=[("HTML или текст", "*.html;*.htm;*.txt"), ("Все файлы", "*.*")])
        if path:
            self._instruction_source = Path(path)
            self._instruction_already_set = False
            self.instruction_label_var.set(self._instruction_source.name)

    def _open_instruction_editor(self):
        """Открывает блочный редактор (app/instruction_editor.py) — общий
        для всех инструкций стиль (заголовки/шаги/плашки/фото), см.
        app/instruction_html.py. Стартовые блоки: то, что уже собрано в
        self._instruction_source этим же редактором в текущей сессии; иначе,
        в режиме редактирования — попытка разобрать уже сохранённый
        instruction.html (получится только если он тоже сделан этим
        редактором, см. instruction_html.parse_blocks); иначе — пустой
        шаблон с маркой/моделью в заголовке."""
        blocks = None
        if isinstance(self._instruction_source, list):
            blocks = self._instruction_source
        elif self._instruction_already_set and self.edit_model_dir is not None:
            existing = self.edit_model_dir / "instruction.html"
            try:
                text = existing.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            blocks = instruction_html.parse_blocks(text, self.edit_model_dir)
        if blocks is None:
            blocks = instruction_html.default_blocks(
                self.brand_var.get().strip(), self.model_var.get().strip())
        InstructionEditorDialog(self, blocks, self._on_instruction_saved)

    def _on_instruction_saved(self, blocks: list[dict]):
        self._instruction_source = blocks
        self._instruction_already_set = False
        self.instruction_label_var.set(f"Инструкция из редактора ({len(blocks)} блок(ов))")

    # ------------------------------------------------------------------
    # Редактор этапов: список слева, форма выбранного этапа справа
    # ------------------------------------------------------------------
    def _build_steps_editor(self, body):
        customtkinter.CTkLabel(body, text="Этапы установки", font=theme.FONT_BOLD,
                                text_color=theme.TEXT_DIM, anchor="w").pack(anchor="w")

        pane = ttk.Panedwindow(body, orient=tk.HORIZONTAL)
        pane.pack(fill="both", expand=True, pady=(4, 0))

        left = customtkinter.CTkFrame(pane, fg_color=theme.BG, corner_radius=0)
        right = customtkinter.CTkFrame(pane, fg_color=theme.BG, corner_radius=0)
        pane.add(left, weight=0)
        pane.add(right, weight=1)
        left_inner = customtkinter.CTkFrame(left, fg_color="transparent")
        left_inner.pack(fill="both", expand=True, padx=(0, 8))
        right_inner = customtkinter.CTkFrame(right, fg_color="transparent")
        right_inner.pack(fill="both", expand=True)

        self.step_listbox = CTkListbox(left_inner, width=32, height=14)
        self.step_listbox.pack(fill="both", expand=True)
        self.step_listbox.bind("<<ListboxSelect>>", self._on_step_list_select)

        add_row = customtkinter.CTkFrame(left_inner, fg_color="transparent")
        add_row.pack(fill="x", pady=(6, 0))
        self.new_step_type_var = tk.StringVar(value=STEP_TYPE_LABELS["adb"])
        customtkinter.CTkOptionMenu(
            add_row, variable=self.new_step_type_var, values=list(STEP_TYPE_LABELS.values()),
            width=140, font=theme.FONT, fg_color=theme.BG_CARD, button_color=theme.BORDER,
            button_hover_color=theme.ACCENT, text_color=theme.TEXT).pack(side="left")
        customtkinter.CTkButton(add_row, text="Добавить этап", command=self._add_step,
                                 **theme.secondary_button()).pack(side="left", padx=(4, 0))

        move_row = customtkinter.CTkFrame(left_inner, fg_color="transparent")
        move_row.pack(fill="x", pady=(4, 0))
        customtkinter.CTkButton(move_row, text="▲", width=36, command=lambda: self._move_step(-1),
                                 **theme.secondary_button()).pack(side="left")
        customtkinter.CTkButton(move_row, text="▼", width=36, command=lambda: self._move_step(1),
                                 **theme.secondary_button()).pack(side="left", padx=(4, 0))
        customtkinter.CTkButton(move_row, text="Удалить этап", command=self._remove_step,
                                 **theme.danger_button()).pack(side="left", padx=(10, 0))

        # CTkScrollableFrame сама даёт Canvas+Scrollbar+авто-ширину дочерних
        # виджетов (важно для Text с командами ADB — раньше без ручного
        # canvas.itemconfig(..., width=e.width) широкий Text вылезал за
        # пределы видимой области) и сама перехватывает прокрутку колёсиком.
        self.step_form_frame = customtkinter.CTkScrollableFrame(right_inner, fg_color="transparent")
        self.step_form_frame.pack(fill="both", expand=True)

        self._refresh_step_list()

    def _refresh_step_list(self):
        self.step_listbox.delete(0, tk.END)
        for i, step in enumerate(self.steps, start=1):
            title = step.title or f"Этап {i}"
            self.step_listbox.insert(tk.END, f"{i}. {title} ({STEP_TYPE_LABELS[step.type]})")
        self.step_listbox.selection_clear(0, tk.END)
        self.step_listbox.selection_set(self.current_step_index)

    def _on_step_list_select(self, _event=None):
        sel = self.step_listbox.curselection()
        if not sel or sel[0] == self.current_step_index:
            return
        self._select_step(sel[0])

    def _select_step(self, index):
        self._commit_current_step_form()
        self.current_step_index = index
        self._editing_variant_index = 0
        self.step_listbox.selection_clear(0, tk.END)
        self.step_listbox.selection_set(index)
        self.step_listbox.see(index)
        self._build_step_form()

    def _add_step(self):
        self._commit_current_step_form()
        step_type = STEP_TYPE_BY_LABEL[self.new_step_type_var.get()]
        new_index = len(self.steps) + 1
        self.steps.append(StepSpec(type=step_type, title=f"Этап {new_index}"))
        self._refresh_step_list()
        self._select_step(len(self.steps) - 1)

    def _remove_step(self):
        if len(self.steps) <= 1:
            messagebox.showwarning(self.title(), "Должен остаться хотя бы один этап.")
            return
        del self.steps[self.current_step_index]
        new_index = min(self.current_step_index, len(self.steps) - 1)
        self.current_step_index = new_index
        self._refresh_step_list()
        self._select_step(new_index)

    def _move_step(self, direction):
        i = self.current_step_index
        j = i + direction
        if j < 0 or j >= len(self.steps):
            return
        self._commit_current_step_form()
        self.steps[i], self.steps[j] = self.steps[j], self.steps[i]
        self.current_step_index = j
        self._refresh_step_list()
        self._build_step_form()

    # ------------------------------------------------------------------
    # Форма текущего этапа — общие поля + поля по типу
    # ------------------------------------------------------------------
    def _build_step_form(self):
        for child in self.step_form_frame.winfo_children():
            child.destroy()

        step = self.steps[self.current_step_index]
        form = self.step_form_frame

        customtkinter.CTkLabel(form, text="Название этапа", font=theme.FONT_BOLD,
                                text_color=theme.TEXT, anchor="w").pack(anchor="w")
        self.step_title_var = tk.StringVar(value=step.title)
        customtkinter.CTkEntry(form, textvariable=self.step_title_var, font=theme.FONT,
                                fg_color=theme.BG_CARD, text_color=theme.TEXT,
                                border_color=theme.BORDER).pack(fill="x", pady=(0, 8))

        customtkinter.CTkLabel(form, text="Описание (инструкция для этого этапа, необязательно)",
                                font=theme.FONT_BOLD, text_color=theme.TEXT, anchor="w").pack(anchor="w")
        self.step_description_text = customtkinter.CTkTextbox(
            form, height=70, font=theme.FONT, fg_color=theme.BG_CARD, text_color=theme.TEXT)
        self.step_description_text.insert("1.0", step.description)
        self.step_description_text.pack(fill="x", pady=(0, 10))

        if step.type == "adb":
            self._build_adb_fields(form, step)
        elif step.type == "usb":
            self._build_usb_fields(form, step)
        elif step.type == "apps":
            self._build_apps_fields(form, step)
        elif step.type == "manual":
            customtkinter.CTkLabel(
                form, text="Для «Ручного шага» дополнительных полей нет — "
                           "пользователь просто прочитает описание выше и отметит "
                           "этап выполненным.", text_color=theme.TEXT_DIM, font=theme.FONT_SMALL,
                wraplength=420, justify="left").pack(anchor="w")
        elif step.type == "exe":
            self._build_exe_fields(form, step)
        elif step.type == "check":
            self._build_check_fields(form, step)

        self._build_condition_fields(form, step)

    def _build_adb_fields(self, form, step: StepSpec):
        customtkinter.CTkLabel(form, text="Команды (по одной adb shell команде на строку, по порядку)",
                                font=theme.FONT_BOLD, text_color=theme.TEXT, anchor="w").pack(anchor="w")
        self.adb_commands_text = customtkinter.CTkTextbox(
            form, height=130, font=theme.FONT_MONO, fg_color=theme.BG_CARD, text_color=theme.TEXT)
        self.adb_commands_text.insert("1.0", "\n".join(step.commands))
        self.adb_commands_text.pack(fill="x", pady=(0, 6))

        customtkinter.CTkLabel(
            form, text="Спецкоманды (тоже по одной на строку, среди обычных команд):\n"
                       "#sleep 5 — пауза 5 секунд\n"
                       "#reboot — перезагрузить магнитолу и дождаться загрузки\n"
                       "#reboot_nowait — перезагрузить, не дожидаясь\n"
                       "#wait_device — дождаться устройства (можно с таймаутом: #wait_device 60)\n"
                       "#ask Введите IP-адрес — спросить у пользователя во время установки; "
                       "ответ можно подставить в следующую команду через {ask}, например:\n"
                       "connect {ask}:5555",
            text_color=theme.TEXT_DIM, font=theme.FONT_SMALL, justify="left", anchor="w"
        ).pack(anchor="w", pady=(0, 8))

        self.adb_install_var = tk.BooleanVar(value=step.adb_install_selected_apks)
        customtkinter.CTkCheckBox(
            form, text="Установить отмеченные галочками приложения после команд",
            variable=self.adb_install_var, font=theme.FONT, text_color=theme.TEXT,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
            border_color=theme.BORDER).pack(anchor="w")

    def _build_exe_fields(self, form, step: StepSpec):
        customtkinter.CTkLabel(
            form, text="Готовый установщик от производителя — пользователь просто запустит "
                       "его и завершит установку сам (для машин, для которых нет доступа к "
                       "исходным скриптам/инструкциям).",
            text_color=theme.TEXT_DIM, font=theme.FONT_SMALL, wraplength=420,
            justify="left").pack(anchor="w", pady=(0, 8))

        row = customtkinter.CTkFrame(form, fg_color="transparent")
        row.pack(fill="x")
        customtkinter.CTkButton(row, text="Выбрать .exe файл...",
                                 command=lambda: self._pick_exe_file(step),
                                 **theme.secondary_button()).pack(side="left")
        customtkinter.CTkButton(row, text="Убрать", command=lambda: self._clear_exe_file(step),
                                 **theme.danger_button()).pack(side="left", padx=(6, 0))

        label_text = step.exe_file.name if step.exe_file else "(не выбран)"
        customtkinter.CTkLabel(form, text=label_text, text_color=theme.TEXT,
                                font=theme.FONT_SMALL).pack(anchor="w", pady=(4, 0))

    def _build_check_fields(self, form, step: StepSpec):
        customtkinter.CTkLabel(
            form, text="Техник сам сверяется с магнитолой (версия аппаратного обеспечения, "
                       "прошивки и т.п.) и выбирает подходящий вариант из списка ниже во время "
                       "установки — опишите, как её проверить, в поле «Описание» выше.",
            text_color=theme.TEXT_DIM, font=theme.FONT_SMALL, wraplength=420,
            justify="left").pack(anchor="w", pady=(0, 8))

        customtkinter.CTkLabel(form, text="Имя переменной (короткое, латиницей, например hw_version)",
                                font=theme.FONT_BOLD, text_color=theme.TEXT, anchor="w").pack(anchor="w")
        self.check_var_entry_var = tk.StringVar(value=step.check_var)
        customtkinter.CTkEntry(form, textvariable=self.check_var_entry_var, font=theme.FONT,
                                fg_color=theme.BG_CARD, text_color=theme.TEXT,
                                border_color=theme.BORDER).pack(fill="x", pady=(0, 8))

        customtkinter.CTkLabel(form, text="Варианты выбора (по одному на строку)",
                                font=theme.FONT_BOLD, text_color=theme.TEXT, anchor="w").pack(anchor="w")
        self.check_options_text = customtkinter.CTkTextbox(
            form, height=90, font=theme.FONT, fg_color=theme.BG_CARD, text_color=theme.TEXT)
        self.check_options_text.insert("1.0", "\n".join(step.check_options))
        self.check_options_text.pack(fill="x", pady=(0, 6))

    def _available_check_vars(self, exclude_step: StepSpec) -> list[str]:
        names = []
        for step in self.steps:
            if step is exclude_step:
                continue
            if step.type == "check" and step.check_var and step.check_var not in names:
                names.append(step.check_var)
        return names

    def _build_condition_fields(self, form, step: StepSpec):
        available_vars = self._available_check_vars(step)
        always = "(всегда)"
        values = [always] + available_vars
        current = step.condition_var or always
        if current not in values:
            values.append(current)

        customtkinter.CTkLabel(form, text="Показывать этап только если (необязательно)",
                                font=theme.FONT_BOLD, text_color=theme.TEXT, anchor="w").pack(
            anchor="w", pady=(12, 0))
        self.condition_var_var = tk.StringVar(value=current)
        customtkinter.CTkOptionMenu(
            form, variable=self.condition_var_var, values=values, font=theme.FONT,
            fg_color=theme.BG_CARD, button_color=theme.BORDER, button_hover_color=theme.ACCENT,
            text_color=theme.TEXT).pack(anchor="w", pady=(2, 4))

        customtkinter.CTkLabel(
            form, text="Значения переменной, при которых этап нужен (через запятую)",
            text_color=theme.TEXT_DIM, font=theme.FONT_SMALL, anchor="w").pack(anchor="w")
        self.condition_values_var = tk.StringVar(value=", ".join(step.condition_values))
        customtkinter.CTkEntry(form, textvariable=self.condition_values_var, font=theme.FONT,
                                fg_color=theme.BG_CARD, text_color=theme.TEXT,
                                border_color=theme.BORDER).pack(fill="x", pady=(2, 0))

    # ------------------------------------------------------------------
    # usb/apps: один набор файлов, либо несколько именованных вариантов
    # (Full/Lite/...) — техник выбирает нужный прямо на этапе установки.
    # ------------------------------------------------------------------
    def _build_usb_fields(self, form, step: StepSpec):
        variants_var = tk.BooleanVar(value=bool(step.variants))

        def _toggle():
            if variants_var.get() and not step.variants:
                step.variants = [StepVariant(name="Вариант 1", usb_files=list(step.usb_files))]
                step.usb_files = []
            elif not variants_var.get() and step.variants:
                if not messagebox.askyesno(
                        self.title(), "Убрать варианты и вернуться к одному набору файлов? "
                                      "Файлы всех вариантов будут потеряны."):
                    variants_var.set(True)
                    return
                step.variants = []
            self._editing_variant_index = 0
            self._build_step_form()

        customtkinter.CTkCheckBox(
            form, text="Несколько вариантов (например Full/Lite) — техник выбирает при установке",
            variable=variants_var, command=_toggle, font=theme.FONT, text_color=theme.TEXT,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
            border_color=theme.BORDER).pack(anchor="w", pady=(0, 8))

        if step.variants:
            self._build_variant_manager(form, step, file_field="usb_files",
                                         pick_fn=self._pick_usb_files, heading="Файлы варианта «{name}» в корень флешки")
        else:
            customtkinter.CTkLabel(form, text="Файлы в корень флешки", font=theme.FONT_BOLD,
                                    text_color=theme.TEXT, anchor="w").pack(anchor="w")
            customtkinter.CTkButton(form, text="Добавить файлы...",
                                     command=lambda: self._pick_usb_files(step),
                                     **theme.secondary_button()).pack(anchor="w", pady=(2, 0))
            listbox = CTkListbox(form, height=4)
            for f in step.usb_files:
                listbox.insert(tk.END, f.name)
            listbox.pack(fill="x", pady=(4, 0))
            customtkinter.CTkButton(form, text="Убрать выбранное",
                                     command=lambda: self._remove_from_list(step.usb_files, listbox),
                                     **theme.danger_button()).pack(anchor="w", pady=(2, 8))

        self.usb_copy_apks_var = tk.BooleanVar(value=step.usb_copy_selected_apks)
        customtkinter.CTkCheckBox(
            form, text="Скопировать отмеченные галочками приложения на эту флешку",
            variable=self.usb_copy_apks_var, font=theme.FONT, text_color=theme.TEXT,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
            border_color=theme.BORDER).pack(anchor="w", pady=(8, 0))

    def _build_apps_fields(self, form, step: StepSpec):
        variants_var = tk.BooleanVar(value=bool(step.variants))

        def _toggle():
            if variants_var.get() and not step.variants:
                step.variants = [StepVariant(name="Вариант 1", standard_apks=list(step.standard_apks))]
                step.standard_apks = []
            elif not variants_var.get() and step.variants:
                if not messagebox.askyesno(
                        self.title(), "Убрать варианты и вернуться к одному набору APK? "
                                      "APK всех вариантов будут потеряны."):
                    variants_var.set(True)
                    return
                step.variants = []
            self._editing_variant_index = 0
            self._build_step_form()

        customtkinter.CTkCheckBox(
            form, text="Несколько вариантов (например Full/Lite) — техник выбирает при установке",
            variable=variants_var, command=_toggle, font=theme.FONT, text_color=theme.TEXT,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
            border_color=theme.BORDER).pack(anchor="w", pady=(0, 8))

        if step.variants:
            self._build_variant_manager(form, step, file_field="standard_apks",
                                         pick_fn=self._pick_pack_apks, heading="APK варианта «{name}»")
        else:
            customtkinter.CTkLabel(form, text="APK стандартного набора для этого этапа",
                                    font=theme.FONT_BOLD, text_color=theme.TEXT, anchor="w").pack(anchor="w")
            customtkinter.CTkButton(form, text="Добавить APK...",
                                     command=lambda: self._pick_pack_apks(step),
                                     **theme.secondary_button()).pack(anchor="w", pady=(2, 0))
            listbox = CTkListbox(form, height=5)
            for apk in step.standard_apks:
                listbox.insert(tk.END, apk.name)
            listbox.pack(fill="x", pady=(4, 0))
            customtkinter.CTkButton(form, text="Убрать выбранное",
                                     command=lambda: self._remove_from_list(step.standard_apks, listbox),
                                     **theme.danger_button()).pack(anchor="w", pady=(2, 0))

    def _build_variant_manager(self, form, step: StepSpec, file_field: str, pick_fn, heading: str):
        """Общий менеджер вариантов для usb/apps — file_field указывает,
        какой список файлов у StepVariant редактируется (usb_files или
        standard_apks), heading — заголовок над списком файлов ("{name}"
        подставляется именем текущего варианта)."""
        if self._editing_variant_index >= len(step.variants):
            self._editing_variant_index = 0
        names = [v.name for v in step.variants]

        row = customtkinter.CTkFrame(form, fg_color="transparent")
        row.pack(fill="x", pady=(0, 4))
        select_var = tk.StringVar(value=names[self._editing_variant_index])

        def _on_select(choice):
            self._editing_variant_index = names.index(choice)
            self._build_step_form()

        customtkinter.CTkOptionMenu(
            row, variable=select_var, values=names, command=_on_select, font=theme.FONT,
            fg_color=theme.BG_CARD, button_color=theme.BORDER, button_hover_color=theme.ACCENT,
            text_color=theme.TEXT).pack(side="left")
        customtkinter.CTkButton(row, text="Добавить вариант", command=lambda: self._add_variant(step),
                                 **theme.secondary_button()).pack(side="left", padx=(6, 0))
        customtkinter.CTkButton(row, text="Переименовать", command=lambda: self._rename_variant(step),
                                 **theme.secondary_button()).pack(side="left", padx=(6, 0))
        customtkinter.CTkButton(row, text="Удалить вариант", command=lambda: self._remove_variant(step),
                                 **theme.danger_button()).pack(side="left", padx=(6, 0))

        variant = step.variants[self._editing_variant_index]
        file_list = getattr(variant, file_field)
        customtkinter.CTkLabel(form, text=heading.format(name=variant.name), font=theme.FONT_BOLD,
                                text_color=theme.TEXT, anchor="w").pack(anchor="w", pady=(6, 0))
        customtkinter.CTkButton(form, text="Добавить...", command=lambda: pick_fn(variant),
                                 **theme.secondary_button()).pack(anchor="w", pady=(2, 0))
        listbox = CTkListbox(form, height=4)
        for f in file_list:
            listbox.insert(tk.END, f.name)
        listbox.pack(fill="x", pady=(4, 0))
        customtkinter.CTkButton(form, text="Убрать выбранное",
                                 command=lambda: self._remove_from_list(file_list, listbox),
                                 **theme.danger_button()).pack(anchor="w", pady=(2, 8))

    def _add_variant(self, step: StepSpec):
        name = simpledialog.askstring(self.title(), "Название варианта (например Full):", parent=self)
        if not name:
            return
        name = name.strip()
        if not name or any(v.name == name for v in step.variants):
            messagebox.showwarning(self.title(), "Название должно быть непустым и уникальным.")
            return
        step.variants.append(StepVariant(name=name))
        self._editing_variant_index = len(step.variants) - 1
        self._build_step_form()

    def _rename_variant(self, step: StepSpec):
        variant = step.variants[self._editing_variant_index]
        name = simpledialog.askstring(self.title(), "Новое название варианта:",
                                       initialvalue=variant.name, parent=self)
        if not name:
            return
        name = name.strip()
        if not name or any(v.name == name for v in step.variants if v is not variant):
            messagebox.showwarning(self.title(), "Название должно быть непустым и уникальным.")
            return
        variant.name = name
        self._build_step_form()

    def _remove_variant(self, step: StepSpec):
        if len(step.variants) <= 1:
            messagebox.showwarning(
                self.title(), "Должен остаться хотя бы один вариант "
                              "(или уберите галочку «Несколько вариантов»).")
            return
        del step.variants[self._editing_variant_index]
        self._editing_variant_index = max(0, self._editing_variant_index - 1)
        self._build_step_form()

    # ------------------------------------------------------------------
    def _pick_usb_files(self, step: StepSpec):
        paths = filedialog.askopenfilenames(title="Файлы для флешки", parent=self)
        if not paths:
            return
        step.usb_files.extend(Path(p) for p in paths)
        self._build_step_form()

    def _pick_pack_apks(self, step: StepSpec):
        paths = filedialog.askopenfilenames(
            title="APK стандартного набора", parent=self,
            filetypes=[("APK", "*.apk"), ("Все файлы", "*.*")])
        if not paths:
            return
        step.standard_apks.extend(Path(p) for p in paths)
        self._build_step_form()

    def _pick_exe_file(self, step: StepSpec):
        path = filedialog.askopenfilename(
            title="Готовый установщик", parent=self,
            filetypes=[("Исполняемый файл", "*.exe"), ("Все файлы", "*.*")])
        if not path:
            return
        step.exe_file = Path(path)
        self._build_step_form()

    def _clear_exe_file(self, step: StepSpec):
        step.exe_file = None
        self._build_step_form()

    def _remove_from_list(self, target_list, listbox):
        for i in reversed(listbox.curselection()):
            del target_list[i]
        self._build_step_form()

    # ------------------------------------------------------------------
    def _commit_current_step_form(self):
        """Сохраняет виджеты формы текущего этапа обратно в StepSpec — перед
        переключением на другой этап и перед сохранением модели. Также
        обновляет список этапов слева (название/тип этапа могли
        измениться) — раньше список обновлялся только при добавлении/
        удалении/перестановке этапов, поэтому переименование этапа "не
        сохранялось" визуально, пока не добавишь новый."""
        if not hasattr(self, "step_title_var"):
            return
        step = self.steps[self.current_step_index]
        step.title = self.step_title_var.get().strip()
        step.description = self.step_description_text.get("1.0", tk.END).strip()

        if step.type == "adb":
            step.commands = [line.strip() for line in
                              self.adb_commands_text.get("1.0", tk.END).splitlines() if line.strip()]
            step.adb_install_selected_apks = self.adb_install_var.get()
        elif step.type == "usb":
            step.usb_copy_selected_apks = self.usb_copy_apks_var.get()
        elif step.type == "check":
            step.check_var = self.check_var_entry_var.get().strip()
            step.check_options = [line.strip() for line in
                                   self.check_options_text.get("1.0", tk.END).splitlines() if line.strip()]

        if hasattr(self, "condition_var_var"):
            value = self.condition_var_var.get()
            step.condition_var = "" if value == "(всегда)" else value
            step.condition_values = [v.strip() for v in self.condition_values_var.get().split(",")
                                      if v.strip()]

        self._refresh_step_list()

    # ------------------------------------------------------------------
    def _save(self):
        self._commit_current_step_form()

        brand = self.brand_var.get().strip()
        model = self.model_var.get().strip()
        if not brand or not model:
            messagebox.showwarning(self.title(), "Укажите марку и модель.")
            return
        if not self._instruction_source and not self._instruction_already_set:
            messagebox.showwarning(self.title(), "Добавьте инструкцию — напишите в редакторе "
                                                   "или импортируйте файл (HTML или TXT).")
            return

        wifi_port = 5555
        if self.wifi_var.get():
            try:
                wifi_port = int(self.wifi_port_var.get().strip())
            except ValueError:
                messagebox.showwarning(self.title(), "Порт должен быть числом.")
                return

        instruction_source = self._instruction_source
        if instruction_source is None and self._instruction_already_set:
            instruction_source = self.edit_model_dir / "instruction.html"

        spec = NewCarSpec(
            brand=brand, model=model, instruction_source=instruction_source,
            wifi=self.wifi_var.get(), wifi_port=wifi_port, steps=self.steps,
        )
        try:
            if self.edit_model_dir:
                update_car(self.edit_model_dir, spec)
                model_dir = self.edit_model_dir
            else:
                model_dir = create_car(self.cars_dir, spec)
        except CarGenerationError as exc:
            messagebox.showerror(self.title(), str(exc))
            return
        except OSError as exc:
            messagebox.showerror(self.title(), f"Ошибка при сохранении файлов: {exc}")
            return

        self.on_created(brand, model)

        # Диалог отправки открываем ПОСЛЕ self.destroy() и с родителем
        # self.master (главное окно), а не self — иначе он закроется вместе
        # с этим окном (Toplevel-дети уничтожаются вместе с родителем).
        parent = self.master
        submit_config = get_submit_config(self.base_dir)
        self.destroy()
        if submit_config and messagebox.askyesno(
                "Magic SQD", "Отправить эту модель на проверку разработчику?", parent=parent):
            _SubmitProgressDialog(parent, model_dir, brand, model, submit_config)


class _SubmitProgressDialog(customtkinter.CTkToplevel):
    """Маленькое модальное окно "Отправка на проверку..." — пакует и шлёт
    модель в фоновом потоке (см. submit_client.submit_model), лог/статус
    приходят через queue.Queue (тот же потокобезопасный приём, что уже
    используется по всему проекту — stage_wizard.py, gui.py._console_worker)."""

    def __init__(self, parent, model_dir: Path, brand: str, model: str, submit_config):
        super().__init__(parent)
        theme.style_toplevel(self)
        self.title("Отправка на проверку")
        self.geometry("480x175")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._queue = queue.Queue()
        self._cancel_event = threading.Event()

        customtkinter.CTkLabel(self, text=f"Отправляю «{brand} / {model}» разработчику...",
                                text_color=theme.TEXT, font=theme.FONT, anchor="w").pack(
            anchor="w", padx=10, pady=(10, 0))
        self.status_var = tk.StringVar(value="Подготовка...")
        customtkinter.CTkLabel(self, textvariable=self.status_var, text_color=theme.TEXT_DIM,
                                font=theme.FONT_SMALL, anchor="w").pack(
            anchor="w", padx=10, pady=(4, 0))
        self.progress = customtkinter.CTkProgressBar(self, mode="indeterminate",
                                                       progress_color=theme.ACCENT_2)
        self.progress.pack(fill="x", padx=10, pady=10)
        self.progress.start()
        customtkinter.CTkButton(self, text="Отмена", command=self._cancel,
                                 **theme.secondary_button()).pack(pady=(0, 10))

        self.protocol("WM_DELETE_WINDOW", self._cancel)
        threading.Thread(target=self._worker, args=(model_dir, brand, model, submit_config),
                          daemon=True).start()
        self.after(100, self._drain_queue)

    def _cancel(self):
        self._cancel_event.set()
        self.status_var.set("Отмена...")

    def _check_cancelled(self):
        if self._cancel_event.is_set():
            raise SubmitCancelled("Отменено пользователем.")

    def _worker(self, model_dir, brand, model, submit_config):
        try:
            submit_model(model_dir, brand, model, submit_config,
                         log=self._queue.put, check_cancelled=self._check_cancelled)
        except SubmitCancelled as exc:
            self._queue.put(("__done__", False, str(exc)))
            return
        except SubmitError as exc:
            self._queue.put(("__done__", False, str(exc)))
            return
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку
            self._queue.put(("__done__", False, f"Неожиданная ошибка: {exc}"))
            return
        self._queue.put(("__done__", True, "Отправлено. Спасибо!"))

    def _drain_queue(self):
        try:
            while True:
                item = self._queue.get_nowait()
                if isinstance(item, tuple) and item and item[0] == "__done__":
                    _, success, message = item
                    self.progress.stop()
                    if success:
                        messagebox.showinfo(self.title(), message, parent=self)
                    else:
                        messagebox.showerror(self.title(), message, parent=self)
                    self.destroy()
                    return
                self.status_var.set(str(item))
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._drain_queue)
