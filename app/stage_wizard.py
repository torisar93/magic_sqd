"""Мастер установки по этапам (stages.py модели): чередование USB-флешки,
ADB и ручных шагов ("обновите прошивку на магнитоле и вставьте флешку снова")
в рамках одной последовательной установки."""
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox, simpledialog

import customtkinter
from tkinterweb import HtmlFrame

from .adb_utils import list_devices
from .ctk_listbox import CTkListbox
from .runner import InstallRunner
from .scanner import scan_apk_dir
from .stage_runner import StageDefinitionError, load_stages, stage_instruction_html_path
from .usb_dialog import UsbDialog
from . import theme

TYPE_LABELS = {"usb": "USB-флешка", "adb": "ADB", "manual": "Вручную на магнитоле",
                "apps": "Выбор приложений", "exe": "Готовый установщик (.exe)",
                "check": "Проверка/выбор"}

PLACEHOLDER_HTML = (
    f"<body style='font-family:Segoe UI, sans-serif; background:{theme.BG_CARD}; "
    f"color:{theme.TEXT_DIM}; padding:16px'>"
    "<p>Для этого этапа нет отдельной инструкции.</p></body>"
)


def _text_to_html(text: str) -> str:
    escaped = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
               .replace("\n", "<br>"))
    return (
        f"<body style='font-family:Segoe UI, sans-serif; background:{theme.BG_CARD}; "
        f"color:{theme.TEXT}; padding:16px'>"
        f"<p>{escaped}</p></body>"
    )


class StageWizard(customtkinter.CTkToplevel):
    def __init__(self, parent, base_dir: Path, adb_path: str, model, shared_apks, log_fn):
        """log_fn(str) — лог мастера пишется в него (лог главного окна), а не
        в отдельный виджет здесь: два независимых лога в двух окнах только
        путают, а само главное окно всё равно остаётся открытым рядом."""
        super().__init__(parent)
        theme.style_toplevel(self)
        self.base_dir = base_dir
        self.adb_path = adb_path
        self.model = model
        self.shared_apks = shared_apks
        self._log_fn = log_fn
        self.device_by_label = {}
        self.done = set()
        # Ответы на этапы "check" (переменная -> выбранное значение) —
        # только на время текущего сеанса мастера, см. StepSpec.check_var/
        # condition_var в car_generator.py и _is_stage_visible ниже.
        self._vars: dict[str, str] = {}
        # Выбранный техником вариант (Full/Lite/...) для этапов usb/apps с
        # несколькими вариантами — {индекс этапа: имя варианта}.
        self._chosen_variants: dict[int, str] = {}

        self.title(f"Установка — {model.brand} / {model.name}")
        self.geometry("1130x780")
        self.minsize(940, 645)
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

        # {str(Path): bool} — состояние галочек на этапе(ах) "apps", живёт
        # отдельно от виджетов (которые пересоздаются при каждом заходе на
        # этап), поэтому переживает переключение между этапами. "Стандартные
        # приложения" отмечены по умолчанию, "Дополнительные" — нет.
        self._app_selection: dict[str, bool] = {}
        for i, stage in enumerate(self.stages):
            if stage["type"] != "apps":
                continue
            for apk in self._standard_apks(stage, i):
                self._app_selection.setdefault(str(apk.path), True)
        for apk in self.shared_apks:
            self._app_selection.setdefault(str(apk.path), False)

        # {section_key: True/False} — свёрнута ли секция дерева приложений
        # ("standard", "extra", "extra:<категория>"); отдельно от виджетов
        # по той же причине, что и _app_selection выше.
        self._section_collapsed: dict[str, bool] = {}

        self.current_index = 0
        self._log_queue = queue.Queue()
        self.install_runner = InstallRunner(adb_path, self._on_log_threaded, self._on_finished_threaded,
                                             base_dir=base_dir, ask_input_fn=self._ask_input_threaded)

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

        left = customtkinter.CTkFrame(pane, fg_color=theme.BG, corner_radius=0)
        right = customtkinter.CTkFrame(pane, fg_color=theme.BG, corner_radius=0)
        pane.add(left, weight=0)
        pane.add(right, weight=1)

        left_inner = customtkinter.CTkFrame(left, fg_color="transparent")
        left_inner.pack(fill="both", expand=True, padx=8, pady=8)
        right_inner = customtkinter.CTkFrame(right, fg_color="transparent")
        right_inner.pack(fill="both", expand=True, padx=8, pady=8)

        customtkinter.CTkLabel(left_inner, text="Этапы установки", font=theme.FONT_BOLD,
                                text_color=theme.TEXT_DIM, anchor="w").pack(anchor="w")
        self.stage_list = CTkListbox(left_inner, width=34, height=20)
        self.stage_list.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.stage_list.bind("<<ListboxSelect>>", self._on_list_select)

        self.right_pane = right_pane = ttk.Panedwindow(right_inner, orient=tk.VERTICAL)
        right_pane.pack(fill=tk.BOTH, expand=True)

        instr_wrapper, instr_content = theme.build_card(right_pane, "Инструкция по этапу")
        self.instruction_view = HtmlFrame(instr_content, messages_enabled=False)
        self.instruction_view.pack(fill=tk.BOTH, expand=True)
        right_pane.add(instr_wrapper, weight=1)

        # "Действие" — тоже пейн (не fill=X снизу), иначе для этапа "apps"
        # (дереву приложений нужно много места) ему просто не хватает высоты
        # в фиксированной раскладке, и содержимое (в том числе кнопка
        # "Готово") уходит за пределы окна. Как пейн — можно перетащить
        # разделитель и увидеть весь список. Лог отдельного виджета здесь
        # нет — все сообщения идут в лог главного окна (см. self._log_fn).
        action_wrapper, self.action_frame = theme.build_card(right_pane, "Действие")
        right_pane.add(action_wrapper, weight=2)

    def _fit_instruction_pane(self):
        """Подгоняет высоту пейна "Инструкция по этапу" под реальную высоту
        уже отрисованного HTML — одна строка текста даёт маленький пейн,
        длинная инструкция — побольше, а не фиксированная доля окна.
        self.instruction_view._html — внутренний виджет tkinterweb
        (Tkhtml3); официального API для замера высоты контента нет, поэтому
        при любой неожиданности просто не трогаем разделитель. Вызывается
        через after() после каждой загрузки инструкции — до реального показа
        окна и до завершения рендеринга Tkhtml высота ещё не известна."""
        try:
            _, top, _, bottom = self.instruction_view._html.bbox()
        except Exception:
            return
        total_height = self.right_pane.winfo_height()
        if total_height <= 100:
            return
        pane_height = (bottom - top) + 40  # + заголовок/паддинг карточки
        pane_height = max(60, min(pane_height, int(total_height * 0.6)))
        self.right_pane.sashpos(0, pane_height)

    def _populate_stage_list(self):
        self.stage_list.delete(0, tk.END)
        for i, stage in enumerate(self.stages):
            self.stage_list.insert(tk.END, self._stage_label(i, stage))

    def _stage_label(self, index, stage):
        visible = self._is_stage_visible(stage)
        mark = "✔" if index in self.done else ("—" if not visible else " ")
        suffix = "" if visible else " (не требуется)"
        return f"[{mark}] {index + 1}. {stage['title']}{suffix}  ({TYPE_LABELS[stage['type']]})"

    def _is_stage_visible(self, stage) -> bool:
        """Этап без condition_var виден всегда; иначе — только если техник
        уже ответил на связанный этап "check" (см. StepSpec.condition_var/
        condition_values в car_generator.py) подходящим значением."""
        var = stage.get("condition_var")
        if not var:
            return True
        return self._vars.get(var) in (stage.get("condition_values") or [])

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
            theme.load_dark_html_file(self.instruction_view, html_path)
        elif stage.get("description"):
            theme.load_dark_html(self.instruction_view, _text_to_html(stage["description"]))
        else:
            theme.load_dark_html(self.instruction_view, PLACEHOLDER_HTML)

        self._build_action_area(index, stage)
        self.after(120, self._fit_instruction_pane)

    # ------------------------------------------------------------------
    # Выбор приложений (этап type="apps")
    # ------------------------------------------------------------------
    def _standard_apks(self, stage, index=None):
        """APK "Стандартных приложений" этапа — имя/описание можно задать
        в <файл>.json рядом с APK, как и для общей папки apk/ (см.
        scanner.scan_apk_dir). Для этапа с вариантами (standard_dir_base +
        variant_names, см. car_generator.py) берём подпапку выбранного
        техником варианта — по умолчанию первый вариант из списка."""
        standard_dir = stage.get("standard_dir")
        if standard_dir is None and stage.get("standard_dir_base"):
            variant_names = stage.get("variant_names") or []
            variant = self._chosen_variants.get(index) if index is not None else None
            variant = variant or (variant_names[0] if variant_names else None)
            if not variant:
                return []
            standard_dir = Path(stage["standard_dir_base"]) / variant
        if not standard_dir:
            return []
        standard_dir = Path(standard_dir)
        if not standard_dir.exists():
            return []
        return scan_apk_dir(standard_dir)

    def _selected_apks(self):
        """Текущий список отмеченных APK — пересчитывается из
        _app_selection, а не хранится статично, чтобы не зависеть от того,
        заходил ли пользователь на этап "apps" перед запуском другого этапа."""
        return [Path(p) for p, checked in self._app_selection.items() if checked]

    def _apps_checkbutton(self, parent, apk):
        key = str(apk.path)
        var = tk.BooleanVar(value=self._app_selection.get(key, False))

        def _on_change(*_args):
            self._app_selection[key] = var.get()

        var.trace_add("write", _on_change)
        # remote_only=True — есть на сервере, но локально ещё не скачан
        # (см. content_sync.list_shared_apk_catalog/ensure_apks_downloaded)
        # — скачается автоматически, только если отметить и начать этап,
        # использующий его (adb install/копирование на флешку).
        label = apk.name + ("  ⬇ (будет скачан)" if apk.remote_only else "")
        cb = customtkinter.CTkCheckBox(parent, text=label, variable=var, font=theme.FONT,
                                        text_color=theme.TEXT_DIM if apk.remote_only else theme.TEXT,
                                        fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                                        border_color=theme.BORDER)
        cb.pack(anchor="w", padx=4, pady=1)
        if apk.description:
            customtkinter.CTkLabel(parent, text=apk.description, text_color=theme.TEXT_DIM,
                                    font=theme.FONT_SMALL, anchor="w").pack(anchor="w", padx=28)

    def _build_collapsible_section(self, parent, key, title, apks=None,
                                    font=None, text_color=None, indent=16):
        """Заголовок с кнопкой ▾/▸ + сворачиваемое тело. Если apks задан —
        тело сразу заполняется чекбоксами (лист дерева); если нет — вызывающий
        код сам наполняет возвращённый Frame (используется для родительской
        секции "Дополнительные приложения", у которой внутри не чекбоксы, а
        под-секции по категориям). Состояние (свёрнуто/нет) хранится в
        self._section_collapsed по key — переживает пересборку дерева при
        переключении между этапами."""
        collapsed = self._section_collapsed.get(key, False)
        font = font or theme.FONT_BOLD
        text_color = text_color or theme.TEXT

        header = customtkinter.CTkFrame(parent, fg_color="transparent")
        header.pack(anchor="w", fill="x", pady=(6, 0))
        toggle_btn = customtkinter.CTkButton(header, width=24, height=24, text="▸" if collapsed else "▾",
                                              **theme.secondary_button())
        toggle_btn.pack(side="left")
        title_label = customtkinter.CTkLabel(header, text=title, font=font, text_color=text_color)
        title_label.pack(side="left", padx=(4, 0))

        body = customtkinter.CTkFrame(parent, fg_color="transparent")
        if not collapsed:
            body.pack(anchor="w", fill="x", padx=(indent, 0))

        def _toggle():
            now_collapsed = not self._section_collapsed.get(key, False)
            self._section_collapsed[key] = now_collapsed
            if now_collapsed:
                body.pack_forget()
            else:
                # after=header — без этого pack() после pack_forget() кладёт
                # body в КОНЕЦ текущего порядка упаковки parent (обычная
                # причина того, что после сворачивания/разворачивания
                # категория "уезжает" в самый низ, за пределы своего же
                # заголовка — то есть визуально выглядит как будто попала
                # не в свою секцию, а в последнюю/"Без категории").
                body.pack(anchor="w", fill="x", padx=(indent, 0), after=header)
            toggle_btn.configure(text="▸" if now_collapsed else "▾")

        toggle_btn.configure(command=_toggle)

        if apks is not None:
            for apk in apks:
                self._apps_checkbutton(body, apk)

        return body

    def _build_apps_stage(self, index, stage):
        customtkinter.CTkButton(self.action_frame, text="Готово, следующий этап →",
                                 command=lambda: self._mark_done(self.current_index),
                                 **theme.accent_button()).pack(side="bottom", pady=(8, 0))

        self._build_variant_picker(self.action_frame, stage, index)

        # CTkScrollableFrame сама даёт Canvas+Scrollbar+авто-ширину и сама
        # перехватывает прокрутку колёсиком для всех своих дочерних
        # виджетов — не нужен ни ручной canvas.bind("<Configure>", ...width),
        # ни привязка mousewheel к каждому виджету по отдельности (было
        # раньше нужно из-за ручной сборки на tk.Canvas).
        inner = customtkinter.CTkScrollableFrame(self.action_frame, fg_color="transparent")
        inner.pack(fill="both", expand=True)

        standard_apks = self._standard_apks(stage, index)
        # Новые APK, впервые увиденные при переключении варианта, по
        # умолчанию отмечены — как и при первом заходе на этап (см.
        # __init__): .get(..., False) в _apps_checkbutton иначе оставил бы
        # их снятыми.
        for apk in standard_apks:
            self._app_selection.setdefault(str(apk.path), True)
        if standard_apks:
            self._build_collapsible_section(
                inner, "standard", stage.get("standard_label", "Стандартные приложения"), standard_apks)

        by_category: dict[str, list] = {}
        for apk in self.shared_apks:
            by_category.setdefault(apk.category, []).append(apk)

        extra_body = self._build_collapsible_section(inner, "extra", "Дополнительные приложения")
        if not by_category:
            customtkinter.CTkLabel(extra_body, text=f"Нет APK в папке {self.base_dir / 'apk'}",
                                    text_color=theme.TEXT_DIM, anchor="w").pack(anchor="w", padx=4)
        for category in sorted(by_category, key=lambda c: (c != "", c.lower())):
            label_text = category or "Без категории"
            self._build_collapsible_section(
                extra_body, f"extra:{category}", label_text, by_category[category],
                font=theme.FONT_SMALL, text_color=theme.ACCENT_2)

    # ------------------------------------------------------------------
    # Область действия — своя для каждого типа этапа
    # ------------------------------------------------------------------
    def _build_action_area(self, index, stage):
        for child in self.action_frame.winfo_children():
            child.destroy()

        if not self._is_stage_visible(stage):
            customtkinter.CTkLabel(
                self.action_frame,
                text="Этот этап не требуется при текущем выборе на этапе проверки — "
                     "можно пропустить или всё равно выполнить вручную.",
                text_color=theme.TEXT_DIM, font=theme.FONT_SMALL, wraplength=560, justify="left"
            ).pack(anchor="w", pady=(0, 8))

        stage_type = stage["type"]
        busy = self.install_runner.running
        if stage_type == "check":
            self._build_check_stage(index, stage)

        elif stage_type == "apps":
            self._build_apps_stage(index, stage)

        elif stage_type == "manual":
            customtkinter.CTkLabel(
                self.action_frame,
                text="Выполните шаги из инструкции на самой магнитоле, "
                     "затем отметьте этап выполненным.",
                text_color=theme.TEXT, font=theme.FONT, wraplength=560, justify="left"
            ).pack(anchor="w", pady=(0, 8))
            customtkinter.CTkButton(self.action_frame, text="Готово, следующий этап →",
                                     command=lambda: self._mark_done(index),
                                     **theme.accent_button()).pack(anchor="w")

        elif stage_type == "usb":
            self._build_variant_picker(self.action_frame, stage, index)
            customtkinter.CTkButton(self.action_frame, text="Подготовить флешку для этого этапа...",
                                     command=lambda: self._run_usb_stage(index, stage),
                                     **theme.accent_button()).pack(anchor="w")

        elif stage_type == "exe":
            exe_path = Path(stage["exe_path"])
            customtkinter.CTkLabel(
                self.action_frame,
                text=f"Для этой модели готовый установщик — {exe_path.name}. Запустите его и "
                     "завершите установку в нём самостоятельно, затем отметьте этап выполненным.",
                text_color=theme.TEXT, font=theme.FONT, wraplength=560, justify="left"
            ).pack(anchor="w", pady=(0, 8))
            if not exe_path.exists():
                customtkinter.CTkLabel(
                    self.action_frame, text=f"Файл не найден: {exe_path}",
                    text_color=theme.DANGER, font=theme.FONT_SMALL, wraplength=560, justify="left"
                ).pack(anchor="w", pady=(0, 8))
            btn_row = customtkinter.CTkFrame(self.action_frame, fg_color="transparent")
            btn_row.pack(anchor="w")
            customtkinter.CTkButton(
                btn_row, text=f"Запустить {exe_path.name}",
                command=lambda: self._run_exe_stage(exe_path), state="normal" if exe_path.exists() else "disabled",
                **theme.accent_button()).pack(side="left")
            customtkinter.CTkButton(btn_row, text="Готово, следующий этап →",
                                     command=lambda: self._mark_done(index),
                                     **theme.secondary_button()).pack(side="left", padx=(6, 0))

        elif stage_type == "adb":
            row = customtkinter.CTkFrame(self.action_frame, fg_color="transparent")
            row.pack(fill=tk.X)
            customtkinter.CTkLabel(row, text="Устройство:", text_color=theme.TEXT,
                                    font=theme.FONT).pack(side="left")
            self.device_combo = customtkinter.CTkOptionMenu(
                row, values=[""], width=280, font=theme.FONT, fg_color=theme.BG_CARD,
                button_color=theme.BORDER, button_hover_color=theme.ACCENT, text_color=theme.TEXT)
            self.device_combo.pack(side="left", padx=(6, 6))
            customtkinter.CTkButton(row, text="Обновить", command=self._refresh_devices,
                                     **theme.secondary_button()).pack(side="left")
            self._refresh_devices()

            btn_row = customtkinter.CTkFrame(self.action_frame, fg_color="transparent")
            btn_row.pack(fill=tk.X, pady=(8, 0))
            self.adb_start_btn = customtkinter.CTkButton(
                btn_row, text="Начать этот этап", command=lambda: self._run_adb_stage(index, stage),
                **theme.accent_button())
            self.adb_start_btn.pack(side="left")
            self.adb_stop_btn = customtkinter.CTkButton(
                btn_row, text="Стоп", command=self._stop_adb_stage,
                state="normal" if busy else "disabled", **theme.danger_button())
            self.adb_stop_btn.pack(side="left", padx=(6, 0))
            if busy:
                self.adb_start_btn.configure(state="disabled")

    # ------------------------------------------------------------------
    # check-этап — техник вручную определяет версию/вариант и выбирает её
    # из списка (см. StepSpec.check_var/check_options в car_generator.py);
    # ответ управляет видимостью последующих этапов через condition_var.
    # ------------------------------------------------------------------
    def _build_check_stage(self, index, stage):
        check_var = stage.get("check_var", "")
        options = stage.get("check_options") or []
        row = customtkinter.CTkFrame(self.action_frame, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        customtkinter.CTkLabel(row, text="Значение:", text_color=theme.TEXT,
                                font=theme.FONT).pack(side="left")
        current = self._vars.get(check_var) or (options[0] if options else "")
        value_var = tk.StringVar(value=current)
        customtkinter.CTkOptionMenu(
            row, variable=value_var, values=options, font=theme.FONT, fg_color=theme.BG_CARD,
            button_color=theme.BORDER, button_hover_color=theme.ACCENT,
            text_color=theme.TEXT).pack(side="left", padx=(6, 0))
        customtkinter.CTkButton(
            self.action_frame, text="Готово, следующий этап →",
            command=lambda: self._confirm_check(index, check_var, value_var.get()),
            **theme.accent_button()).pack(anchor="w")

    def _confirm_check(self, index, check_var, value):
        if check_var:
            self._vars[check_var] = value
        self._mark_done(index, advance=True)

    # ------------------------------------------------------------------
    # Выбор варианта содержимого (Full/Lite/...) — общий для usb/apps
    # этапов с несколькими вариантами (см. StepSpec.variants).
    # ------------------------------------------------------------------
    def _build_variant_picker(self, parent, stage, index):
        variant_names = stage.get("variant_names") or []
        if not variant_names:
            return
        current = self._chosen_variants.get(index) or variant_names[0]
        self._chosen_variants[index] = current

        row = customtkinter.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 8))
        customtkinter.CTkLabel(row, text="Вариант:", text_color=theme.TEXT,
                                font=theme.FONT).pack(side="left")
        variant_var = tk.StringVar(value=current)

        def _on_change(choice):
            self._chosen_variants[index] = choice
            self._build_action_area(index, stage)

        customtkinter.CTkOptionMenu(
            row, variable=variant_var, values=variant_names, command=_on_change, font=theme.FONT,
            fg_color=theme.BG_CARD, button_color=theme.BORDER, button_hover_color=theme.ACCENT,
            text_color=theme.TEXT).pack(side="left", padx=(6, 0))

    # ------------------------------------------------------------------
    # USB-этап — переиспользуем существующий диалог флешки
    # ------------------------------------------------------------------
    def _run_usb_stage(self, index, stage):
        def on_finished(success):
            if success:
                self._mark_done(index, advance=True)
            self._log(f"Этап {index + 1} ({stage['title']}): "
                      f"{'выполнен' if success else 'завершился с ошибкой'}.")

        UsbDialog(self, self.base_dir, self.model, self._selected_apks(),
                   run_fn=stage["run"], title_suffix=stage["title"], on_finished=on_finished,
                   variant=self._chosen_variants.get(index))

    # ------------------------------------------------------------------
    # exe-этап — готовый установщик производителя, запускается на самом ПК
    # (не через adb — у пользователя просто нет исходников/инструкции)
    # ------------------------------------------------------------------
    def _run_exe_stage(self, exe_path: Path):
        try:
            subprocess.Popen([str(exe_path)], cwd=str(exe_path.parent))
        except OSError as exc:
            messagebox.showerror(self.title(), f"Не удалось запустить {exe_path.name}: {exc}")
            return
        self._log(f"Запущен {exe_path.name} — завершите установку в открывшемся окне.")

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
        self.device_combo.configure(values=labels)
        self.device_combo.set(labels[0] if labels else "")

    def _run_adb_stage(self, index, stage):
        device = self._selected_device_serial()
        if not device:
            if not messagebox.askyesno(
                self.title(),
                "Не выбрано подключённое устройство ADB. Продолжить всё равно?",
            ):
                return

        self._pending_adb_index = index
        self.adb_start_btn.configure(state="disabled")
        self.adb_stop_btn.configure(state="normal")
        self._log(f"=== Этап {index + 1}: {stage['title']} ===")
        try:
            self.install_runner.start(self.model, device, self._selected_apks(), run_fn=stage["run"])
        except RuntimeError as exc:
            messagebox.showerror(self.title(), str(exc))
            self.adb_start_btn.configure(state="normal")
            self.adb_stop_btn.configure(state="disabled")

    def _stop_adb_stage(self):
        if self.install_runner.running:
            self.install_runner.cancel()
            self._log("Останавливаю этап...")

    # ------------------------------------------------------------------
    def _mark_done(self, index, advance=True):
        self.done.add(index)
        self._refresh_stage_list_labels()
        next_index = index
        if advance:
            # Пропускаем этапы, которые стали неактуальны после ответа на
            # check-этап (см. _is_stage_visible) — техник просто не должен
            # их видеть при обычном линейном движении "дальше".
            candidate = index + 1
            while candidate < len(self.stages) and not self._is_stage_visible(self.stages[candidate]):
                candidate += 1
            if candidate < len(self.stages):
                next_index = candidate
        self._select_stage(next_index)
        if all(i in self.done or not self._is_stage_visible(s) for i, s in enumerate(self.stages)):
            messagebox.showinfo(self.title(), "Все этапы установки отмечены как выполненные.")

    # ------------------------------------------------------------------
    # Логи/колбэки ADB-этапа из фонового потока — только через очередь
    # ------------------------------------------------------------------
    def _on_log_threaded(self, message):
        self._log_queue.put(("log", message))

    def _on_finished_threaded(self, success, message):
        self._log_queue.put(("finished", (success, message)))

    def _ask_input_threaded(self, prompt, title):
        """См. App._ask_input_threaded в gui.py — тот же приём для окна мастера."""
        result_holder = {}
        event = threading.Event()
        self._log_queue.put(("ask_input", (prompt, title, result_holder, event)))
        event.wait()
        return result_holder.get("value")

    def _drain_log_queue(self):
        try:
            while True:
                kind, payload = self._log_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "ask_input":
                    prompt, title, result_holder, event = payload
                    result_holder["value"] = simpledialog.askstring(title, prompt, parent=self)
                    event.set()
                elif kind == "finished":
                    success, message = payload
                    self._log(message)
                    if hasattr(self, "adb_start_btn"):
                        self.adb_start_btn.configure(state="normal")
                        self.adb_stop_btn.configure(state="disabled")
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
        self._log_fn(f"[{self.model.brand} / {self.model.name}] {message}")

    def _on_close(self):
        if self.install_runner.running:
            if not messagebox.askyesno(self.title(), "Этап ещё выполняется. Закрыть окно?"):
                return
            self.install_runner.cancel()
        self.destroy()
