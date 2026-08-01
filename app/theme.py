"""Тёмная тема приложения на customtkinter — в тех же тонах, что сайт/
админка/сплеш (см. server/site/index.html, server/admin/*.html). Один
источник цветов и стандартных пресетов кнопок для всего: главного окна,
мастеров и панели HTML-инструкции.

customtkinter не имеет аналогов для ttk.Panedwindow (перетаскиваемый
разделитель) — это единственный оставшийся в проекте ttk-виджет, стилизуем
его отдельно через ttk.Style/clam. Внутрь его панелей кладём обычные
CTk-виджеты — официально поддерживаемое смешение."""
import ctypes
import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

import customtkinter

BG = "#0f1420"
BG_CARD = "#171f30"
TEXT = "#e8ecf4"
TEXT_DIM = "#9aa4b8"
ACCENT = "#5b8cff"
ACCENT_HOVER = "#4a76d9"
ACCENT_2 = "#7ee0c0"
BORDER = "#2a3448"
DANGER = "#e0605b"
DANGER_HOVER = "#c94842"

CORNER_RADIUS = 8

CARD_PADX = 12
CARD_PADY = 10

# Заполняются в apply_theme() — CTkFont требует уже созданный корень Tk,
# поэтому не могут быть модульными константами, вычисленными при импорте.
FONT: "customtkinter.CTkFont | None" = None
FONT_BOLD: "customtkinter.CTkFont | None" = None
FONT_SMALL: "customtkinter.CTkFont | None" = None
FONT_MONO: "customtkinter.CTkFont | None" = None  # для логов/ADB-команд — тот же размер, что FONT


_BASE_DIR: Path | None = None  # задаётся apply_theme() — нужен style_toplevel() для иконки


def _patch_dwm_ctypes_signatures() -> None:
    """ВАЖНО: без явных restype/argtypes на 64-битной Windows GetParent()
    по умолчанию трактуется как возвращающая 32-битный int — реальный
    (64-битный) HWND обрезается, и DwmSetWindowAttribute получает мусорный
    указатель. Это не ловится как Python-исключение (падает на уровне ОС,
    весь процесс, без traceback) — судя по всему, именно так выглядело
    "окно мелькнуло и пропало" на реальной машине. ctypes кеширует объект
    функции per-DLL (windll.user32.GetParent — всегда один и тот же
    объект), поэтому restype/argtypes, выставленные здесь один раз, чинят
    заодно и точно такой же непропатченный внутренний вызов внутри самого
    customtkinter (CTk/CTkToplevel._windows_set_titlebar_color, см.
    ctk_tk.py/ctk_toplevel.py) — он использует те же самые кешированные
    объекты ctypes.windll.user32/dwmapi."""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        user32.GetParent.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
        dwmapi.DwmSetWindowAttribute.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        user32.SetWindowPos.restype = ctypes.c_int
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    except AttributeError:
        pass


def _try_dark_titlebar(win) -> None:
    """Best-effort тёмный заголовок окна на Windows 10 1809+/11 (DWM).
    Молча ничего не делает на более старых Windows или другой ОС.

    Проверено через DwmGetWindowAttribute: сам атрибут DwmSetWindowAttribute
    ставит корректно (restype/argtypes уже пропатчены, см.
    _patch_dwm_ctypes_signatures) даже для только что созданного
    CTkToplevel — но реальную перерисовку УЖЕ показанной рамки это не
    гарантирует: значение сохраняется в DWM, а видимый заголовок остаётся
    белым до следующего перемещения/ресайза окна пользователем. Поэтому
    вызывающий (style_toplevel/apply_theme) должен дёргать ЭТУ функцию
    ещё раз с задержкой через after() — уже после того, как окно реально
    показано (свой withdraw()+deiconify() у CTkToplevel завершится), чтобы
    SetWindowPos(..., SWP_FRAMECHANGED) форсировал перерисовку видимой
    рамки, а не уже скрытой."""
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        dwmapi = ctypes.windll.dwmapi
        win.update_idletasks()
        hwnd = user32.GetParent(win.winfo_id())
        if not hwnd:
            return
        value = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (20 — новый, 19 — старый билд)
            dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(value), ctypes.sizeof(value))

        SWP_NOMOVE, SWP_NOSIZE, SWP_NOZORDER = 0x0002, 0x0001, 0x0004
        SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0010, 0x0020
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
    except (AttributeError, OSError, tk.TclError):
        pass


def _set_window_icon(win) -> None:
    """customtkinter сам через after(200, ...) принудительно ставит СВОЮ
    иконку (свой синий квадрат с белым контуром) — но только если ни разу
    не вызывался iconbitmap() (см. ctk_tk.py/ctk_toplevel.py:
    self._iconbitmap_method_called). Это относится к КАЖДОМУ окну отдельно
    (и CTk, и каждому CTkToplevel), поэтому вызывается и из apply_theme()
    для корневого окна, и из style_toplevel() для каждого диалога — без
    этого у дочерних окон подменяется иконка ровно так же, как раньше было
    у главного. iconbitmap() обязателен (не просто "на выбор" с
    iconphoto) — именно он снимает подмену; iconphoto() добавляем следом
    же как более качественный источник для панели задач/Пуска."""
    if _BASE_DIR is None:
        return
    icon_path = _BASE_DIR / "assets" / "icon.ico"
    if icon_path.exists():
        try:
            win.iconbitmap(default=str(icon_path))
        except tk.TclError:
            pass

    icon_path = _BASE_DIR / "assets" / "icon.png"
    if icon_path.exists():
        try:
            image = tk.PhotoImage(file=str(icon_path))
            win._magicsqd_icon_image = image  # держим ссылку на окне — иначе GC
            win.iconphoto(True, image)
        except tk.TclError:
            pass


def style_toplevel(win) -> None:
    """Фон окна + попытка тёмного заголовка/правильной иконки — для
    каждого отдельного CTkToplevel (у каждого своя рамка ОС, apply_theme()
    красит только корневое окно). Тёмный заголовок дёргается дважды:
    сразу (пока сам CTkToplevel ещё не показал окно — withdraw()+
    deiconify() внутри него) и ещё раз с задержкой ПОСЛЕ этого показа —
    см. _try_dark_titlebar."""
    win.configure(fg_color=BG)
    _try_dark_titlebar(win)
    _set_window_icon(win)
    win.after(150, lambda: _try_dark_titlebar(win))


def apply_theme(root, base_dir: Path | None = None) -> None:
    global FONT, FONT_BOLD, FONT_SMALL, FONT_MONO, _BASE_DIR
    customtkinter.set_appearance_mode("dark")
    _BASE_DIR = base_dir
    _patch_dwm_ctypes_signatures()

    FONT = customtkinter.CTkFont(family="Segoe UI", size=15)
    FONT_BOLD = customtkinter.CTkFont(family="Segoe UI", size=15, weight="bold")
    FONT_SMALL = customtkinter.CTkFont(family="Segoe UI", size=13)
    FONT_MONO = customtkinter.CTkFont(family="Consolas", size=15)

    root.configure(fg_color=BG)
    _try_dark_titlebar(root)
    _set_window_icon(root)
    root.after(150, lambda: _try_dark_titlebar(root))

    # Единственный оставшийся в проекте ttk-виджет — Panedwindow (нет
    # аналога в customtkinter, а нужен реальный drag-resize панелей).
    # "." (корневой стиль) настраиваем тоже — иначе разделитель/рамка
    # Panedwindow у темы clam берёт цвета из невыставленного дефолта
    # ("classic" светло-серый), а не из "TPanedwindow" — отсюда серые
    # полосы по краям панелей в окне установки.
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, bordercolor=BORDER,
                     lightcolor=BG, darkcolor=BG, troughcolor=BG)
    style.configure("TPanedwindow", background=BG)


# ----------------------------------------------------------------------
# Пресеты кнопок — раскладываются в конструктор: CTkButton(parent,
# text=..., command=..., **theme.accent_button()). Функции, а не готовые
# словари: должны подхватывать FONT_BOLD, назначаемый в apply_theme() уже
# после импорта модуля.
# ----------------------------------------------------------------------
def accent_button() -> dict:
    """Главное действие экрана ("Установка", "Начать этап", "Создать")."""
    return dict(fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#ffffff",
                corner_radius=CORNER_RADIUS, font=FONT_BOLD)


def danger_button() -> dict:
    """Стоп/удаление ("Стоп", "Удалить этап", "Убрать выбранное")."""
    return dict(fg_color=DANGER, hover_color=DANGER_HOVER, text_color="#ffffff",
                corner_radius=CORNER_RADIUS, font=FONT)


def secondary_button() -> dict:
    """Второстепенное действие — умолчание для большинства кнопок."""
    return dict(fg_color=BG_CARD, hover_color=BORDER, text_color=TEXT,
                border_width=1, border_color=BORDER,
                corner_radius=CORNER_RADIUS, font=FONT)


def card_kwargs() -> dict:
    """Скруглённая "карточка" — замена ttk.LabelFrame (сочетается с
    отдельным CTkLabel-заголовком над ней, см. build_card())."""
    return dict(fg_color=BG_CARD, corner_radius=CORNER_RADIUS,
                border_width=1, border_color=BORDER)


def build_card(parent, title: str):
    """"Карточка" с заголовком над ней — замена ttk.LabelFrame (нет
    аналога в customtkinter). Возвращает (wrapper, content): wrapper
    кладётся в Panedwindow/pack, content — куда класть содержимое (уже с
    отступом от скруглённой рамки card — без него содержимое утыкается
    прямо в границу и обрезается по скруглённым углам)."""
    wrapper = customtkinter.CTkFrame(parent, fg_color=BG, corner_radius=0)
    customtkinter.CTkLabel(wrapper, text=title, font=FONT_BOLD, text_color=TEXT_DIM,
                            anchor="w").pack(fill="x", pady=(0, 4))
    card = customtkinter.CTkFrame(wrapper, **card_kwargs())
    card.pack(fill="both", expand=True)
    content = customtkinter.CTkFrame(card, fg_color="transparent")
    content.pack(fill="both", expand=True, padx=CARD_PADX, pady=CARD_PADY)
    return wrapper, content


# ----------------------------------------------------------------------
# Тёмная панель HTML-инструкции (tkinterweb.HtmlFrame) — не завязана на
# toolkit виджетов, без изменений с прошлой перекраски.
# ----------------------------------------------------------------------
DARK_HTML_CSS = f"""<style>
html, body, p, div, span, li, ul, ol, td, th, small, strong, em, b, i,
h1, h2, h3, h4, h5, h6 {{ background: {BG_CARD} !important; color: {TEXT} !important; }}
a, a:visited {{ color: {ACCENT} !important; }}
code, pre {{ background: {BG} !important; color: {ACCENT_2} !important; }}
table, th, td, hr {{ border-color: {BORDER} !important; }}
</style>"""

_HEAD_RE = re.compile(r"<head[^>]*>", re.IGNORECASE)
_HTML_RE = re.compile(r"<html[^>]*>", re.IGNORECASE)


def _inject_dark_css(html: str) -> str:
    """Защитный минимум для НЕаудированного HTML — гарантирует читаемый
    текст на тёмном фоне даже для инструкции, которую разработчик ещё не
    открывал и не подгонял руками. Кастомные классы автора (".warn"/
    ".danger" и т.п.) не трогает — только базовые теги, чтобы не спорить
    с их собственным фоном."""
    match = _HEAD_RE.search(html)
    if match:
        pos = match.end()
        return html[:pos] + DARK_HTML_CSS + html[pos:]
    match = _HTML_RE.search(html)
    if match:
        pos = match.end()
        return html[:pos] + DARK_HTML_CSS + html[pos:]
    return DARK_HTML_CSS + html


def load_dark_html(html_frame, html: str, base_url: str | None = None) -> None:
    html_frame.load_html(_inject_dark_css(html), base_url=base_url)


def load_dark_html_file(html_frame, path: Path) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    base_url = path.parent.resolve().as_uri() + "/"
    html_frame.load_html(_inject_dark_css(text), base_url=base_url)
