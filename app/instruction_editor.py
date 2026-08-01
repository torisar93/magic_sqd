"""Простой блочный редактор instruction.html — вместо ручного HTML даёт
набор типовых "блоков" (заголовок, текст, шаги, жёлтая/красная плашка,
фото), из которых app/instruction_html.py собирает документ в одном и том
же оформлении для всех моделей (тот же стиль, что и в cars/Demo/Test Model
X1, cars/Geely/Atlas New — их писали руками, этот редактор даёт то же
самое без HTML)."""
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

import customtkinter
from tkinterweb import HtmlFrame

from . import instruction_html, theme

IMAGE_FILETYPES = [("Изображения", "*.png;*.jpg;*.jpeg;*.gif;*.bmp;*.webp"), ("Все файлы", "*.*")]

# Цвета кнопок панели инструментов для warn/steps/danger — те же оттенки,
# что и у самих плашек в instruction_html.INSTRUCTION_CSS (.warn/.danger),
# чтобы кнопка сама подсказывала, как будет выглядеть блок, и заодно
# отличалась от соседних без длинной подписи в скобках.
TOOLBAR_BLOCK_COLORS = {
    "steps": dict(fg_color="#1a2e42", hover_color="#25415c",
                  text_color=theme.ACCENT_2, border_color=theme.ACCENT_2),
    "warn": dict(fg_color="#4a3f1a", hover_color="#5c4e22",
                 text_color="#f3e3a8", border_color="#8a6d1f"),
    "danger": dict(fg_color="#4a2222", hover_color="#5c2b2b",
                   text_color="#f3c6c6", border_color="#8a3a3a"),
}


class InstructionEditorDialog(customtkinter.CTkToplevel):
    def __init__(self, parent, blocks: list[dict], on_save):
        """blocks — начальный список блоков (фото уже с абсолютными путями,
        см. instruction_html.parse_blocks). on_save(blocks) вызывается
        только по кнопке "Сохранить" — закрытие крестиком/"Отмена"
        отбрасывает изменения, как и остальные диалоги мастера."""
        super().__init__(parent)
        theme.style_toplevel(self)
        self.on_save = on_save
        self._blocks: list[dict] = [dict(b) for b in blocks] or instruction_html.default_blocks("", "")
        self._field_widgets: list[dict] = []

        self.title("Инструкция")
        self.geometry("1260x830")
        self.minsize(1040, 645)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self.destroy)

        self._build_ui()
        self._rebuild_rows()
        self._refresh_preview()

    # ------------------------------------------------------------------
    def _build_ui(self):
        btn_row = customtkinter.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", side="bottom", padx=10, pady=(0, 10))
        customtkinter.CTkButton(btn_row, text="Отмена", command=self.destroy,
                                 **theme.secondary_button()).pack(side="right")
        customtkinter.CTkButton(btn_row, text="Сохранить", command=self._save,
                                 **theme.accent_button()).pack(side="right", padx=(0, 6))

        pane = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        pane.pack(fill="both", expand=True, padx=10, pady=(10, 0))

        left = customtkinter.CTkFrame(pane, fg_color=theme.BG, corner_radius=0)
        right = customtkinter.CTkFrame(pane, fg_color=theme.BG, corner_radius=0)
        pane.add(left, weight=1)
        pane.add(right, weight=1)

        left_wrapper, left_content = theme.build_card(left, "Блоки инструкции")
        left_wrapper.pack(fill="both", expand=True)
        self._build_toolbar(left_content)
        self.rows_frame = customtkinter.CTkScrollableFrame(left_content, fg_color="transparent")
        self.rows_frame.pack(fill="both", expand=True, pady=(8, 0))
        customtkinter.CTkButton(left_content, text="Обновить предпросмотр",
                                 command=self._refresh_preview,
                                 **theme.secondary_button()).pack(fill="x", pady=(8, 0))

        right_wrapper, right_content = theme.build_card(right, "Предпросмотр")
        right_wrapper.pack(fill="both", expand=True)
        self.preview_frame = HtmlFrame(right_content, messages_enabled=False)
        self.preview_frame.pack(fill="both", expand=True)

    def _build_toolbar(self, parent):
        toolbar = customtkinter.CTkFrame(parent, fg_color="transparent")
        toolbar.pack(fill="x")
        for i in range(4):
            toolbar.columnconfigure(i, weight=1)
        base_kwargs = {**theme.secondary_button(), "font": theme.FONT_SMALL}
        for i, (block_type, label) in enumerate(instruction_html.BLOCK_TYPE_LABELS.items()):
            button_kwargs = {**base_kwargs, **TOOLBAR_BLOCK_COLORS.get(block_type, {})}
            btn = customtkinter.CTkButton(
                toolbar, text="+ " + label, command=lambda t=block_type: self._add_block(t),
                **button_kwargs)
            btn.grid(row=i // 4, column=i % 4, sticky="we", padx=2, pady=2)

    # ------------------------------------------------------------------
    # Блоки: синхронизация виджетов <-> self._blocks, перерисовка списка
    # ------------------------------------------------------------------
    def _sync_from_widgets(self):
        for block, widgets in zip(self._blocks, self._field_widgets):
            if "entry" in widgets:
                block["text"] = widgets["entry"].get()
            if "textbox" in widgets:
                block["text"] = widgets["textbox"].get("1.0", "end-1c")
            if "caption_entry" in widgets:
                block["caption"] = widgets["caption_entry"].get()

    def _add_block(self, block_type: str):
        self._sync_from_widgets()
        if block_type == "photo":
            self._blocks.append({"type": "photo", "path": "", "caption": ""})
        else:
            self._blocks.append({"type": block_type, "text": ""})
        self._rebuild_rows()

    def _delete_block(self, index: int):
        self._sync_from_widgets()
        del self._blocks[index]
        self._rebuild_rows()

    def _move_block(self, index: int, delta: int):
        self._sync_from_widgets()
        target = index + delta
        if 0 <= target < len(self._blocks):
            self._blocks[index], self._blocks[target] = self._blocks[target], self._blocks[index]
        self._rebuild_rows()

    def _pick_photo(self, index: int):
        path = filedialog.askopenfilename(title="Файл фото", parent=self, filetypes=IMAGE_FILETYPES)
        if not path:
            return
        self._sync_from_widgets()
        self._blocks[index]["path"] = path
        self._rebuild_rows()

    def _rebuild_rows(self):
        for child in self.rows_frame.winfo_children():
            child.destroy()
        self._field_widgets = []
        if not self._blocks:
            customtkinter.CTkLabel(
                self.rows_frame, text="Добавьте блок кнопками выше.",
                text_color=theme.TEXT_DIM, font=theme.FONT_SMALL).pack(anchor="w", pady=8)
            return
        for index, block in enumerate(self._blocks):
            self._field_widgets.append(self._build_row(index, block))

    def _build_row(self, index: int, block: dict) -> dict:
        row = customtkinter.CTkFrame(self.rows_frame, **theme.card_kwargs())
        row.pack(fill="x", pady=(0, 8))

        header = customtkinter.CTkFrame(row, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(6, 0))
        label = instruction_html.BLOCK_TYPE_LABELS.get(block["type"], block["type"])
        customtkinter.CTkLabel(header, text=label, font=theme.FONT_BOLD,
                                text_color=theme.ACCENT_2).pack(side="left")
        customtkinter.CTkButton(header, text="✕", width=28, command=lambda: self._delete_block(index),
                                 **theme.danger_button()).pack(side="right")
        customtkinter.CTkButton(header, text="▼", width=28, command=lambda: self._move_block(index, 1),
                                 **theme.secondary_button()).pack(side="right", padx=2)
        customtkinter.CTkButton(header, text="▲", width=28, command=lambda: self._move_block(index, -1),
                                 **theme.secondary_button()).pack(side="right", padx=2)

        content = customtkinter.CTkFrame(row, fg_color="transparent")
        content.pack(fill="x", padx=8, pady=(4, 8))

        widgets: dict = {}
        block_type = block["type"]
        if block_type in ("h1", "h2"):
            entry = customtkinter.CTkEntry(content, font=theme.FONT, fg_color=theme.BG,
                                            text_color=theme.TEXT, border_color=theme.BORDER)
            entry.insert(0, block.get("text", ""))
            entry.pack(fill="x")
            widgets["entry"] = entry
        elif block_type == "steps":
            customtkinter.CTkLabel(content, text="Каждый шаг — отдельная строка",
                                    text_color=theme.TEXT_DIM, font=theme.FONT_SMALL,
                                    anchor="w").pack(fill="x")
            textbox = customtkinter.CTkTextbox(content, height=100, font=theme.FONT,
                                                fg_color=theme.BG, text_color=theme.TEXT)
            textbox.insert("1.0", block.get("text", ""))
            textbox.pack(fill="x", pady=(2, 0))
            widgets["textbox"] = textbox
        elif block_type in ("p", "warn", "danger"):
            textbox = customtkinter.CTkTextbox(content, height=70, font=theme.FONT,
                                                fg_color=theme.BG, text_color=theme.TEXT)
            textbox.insert("1.0", block.get("text", ""))
            textbox.pack(fill="x")
            widgets["textbox"] = textbox
        elif block_type == "photo":
            photo_row = customtkinter.CTkFrame(content, fg_color="transparent")
            photo_row.pack(fill="x")
            customtkinter.CTkButton(photo_row, text="Выбрать фото...",
                                     command=lambda: self._pick_photo(index),
                                     **theme.secondary_button()).pack(side="left")
            path = block.get("path", "")
            file_label = Path(path).name if path else "(не выбрано)"
            customtkinter.CTkLabel(photo_row, text=file_label, text_color=theme.TEXT_DIM,
                                    font=theme.FONT_SMALL).pack(side="left", padx=(8, 0))

            caption_entry = customtkinter.CTkEntry(
                content, font=theme.FONT, fg_color=theme.BG, text_color=theme.TEXT,
                border_color=theme.BORDER, placeholder_text="Подпись под фото (необязательно)")
            caption_entry.insert(0, block.get("caption", ""))
            caption_entry.pack(fill="x", pady=(6, 0))
            widgets["caption_entry"] = caption_entry

        return widgets

    # ------------------------------------------------------------------
    def _refresh_preview(self):
        self._sync_from_widgets()
        blocks_with_photos = [b for b in self._blocks if b.get("type") != "photo" or b.get("path")]
        html_text = instruction_html.render_preview(blocks_with_photos)
        theme.load_dark_html(self.preview_frame, html_text)

    def _save(self):
        self._sync_from_widgets()
        self.on_save(self._blocks)
        self.destroy()
