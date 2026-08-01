"""CTkListbox — минимальная замена tk.Listbox на виджетах customtkinter
(нет прямого аналога в самой библиотеке): строки-CTkButton, скруглённая
"таблетка" на выбранной строке, hover на остальных, внутри
CTkScrollableFrame. Реализует ровно то подмножество API tk.Listbox, что
реально используется в проекте (gui.py, stage_wizard.py, add_car_dialog.py)
— insert/delete/get/curselection/selection_set/selection_clear/see и
виртуальное событие "<<ListboxSelect>>" — так что код, который создаёт и
использует список, не меняется вообще, меняется только конструктор."""
import tkinter as tk

import customtkinter

from . import theme

_ROW_HEIGHT_PX = 30
_CHAR_WIDTH_PX = 7


class CTkListbox(customtkinter.CTkScrollableFrame):
    def __init__(self, master, height: int | None = None, width: int | None = None, **kwargs):
        px_height = height * _ROW_HEIGHT_PX if height else 150
        px_width = width * _CHAR_WIDTH_PX if width else 220
        super().__init__(master, width=px_width, height=px_height,
                          fg_color=theme.BG_CARD, corner_radius=theme.CORNER_RADIUS,
                          label_text="", **kwargs)
        self._items: list[str] = []
        self._rows: list[customtkinter.CTkButton] = []
        self._selection: int | None = None

    # ------------------------------------------------------------------
    def insert(self, index, text: str) -> None:
        pos = len(self._items) if index in (tk.END, "end") else int(index)
        self._items.insert(pos, text)
        self._rebuild()

    def delete(self, first, last=None) -> None:
        first = len(self._items) - 1 if first in (tk.END, "end") else int(first)
        if last is None:
            last = first
        else:
            last = len(self._items) - 1 if last in (tk.END, "end") else int(last)
        del self._items[first:last + 1]
        if self._selection is not None and self._selection >= len(self._items):
            self._selection = None
        self._rebuild()

    def get(self, index) -> str:
        return self._items[int(index)]

    def curselection(self) -> tuple:
        return () if self._selection is None else (self._selection,)

    def selection_clear(self, _first=0, _last=None) -> None:
        self._selection = None
        self._refresh_row_colors()

    def selection_set(self, index) -> None:
        self._selection = int(index)
        self._refresh_row_colors()

    def see(self, _index) -> None:
        # У CTkScrollableFrame нет штатного "проскроллить к строке", а
        # списки в проекте небольшие (десятки строк) — не критично.
        pass

    # ------------------------------------------------------------------
    def _rebuild(self) -> None:
        for row in self._rows:
            row.destroy()
        self._rows = []
        for i, text in enumerate(self._items):
            row = customtkinter.CTkButton(
                self, text=text, anchor="w", corner_radius=theme.CORNER_RADIUS,
                fg_color="transparent", hover_color=theme.BORDER, text_color=theme.TEXT,
                font=theme.FONT, command=lambda i=i: self._on_row_click(i))
            row.pack(fill="x", padx=4, pady=2)
            self._rows.append(row)
        self._refresh_row_colors()

    def _refresh_row_colors(self) -> None:
        for i, row in enumerate(self._rows):
            if i == self._selection:
                row.configure(fg_color=theme.ACCENT, text_color="#ffffff",
                              hover_color=theme.ACCENT_HOVER)
            else:
                row.configure(fg_color="transparent", text_color=theme.TEXT,
                              hover_color=theme.BORDER)

    def _on_row_click(self, index: int) -> None:
        self._selection = index
        self._refresh_row_colors()
        self.event_generate("<<ListboxSelect>>")
