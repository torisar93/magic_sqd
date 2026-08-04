"""Цветовые константы палитры "Диагностика" — вынесены из app/theme.py
(customtkinter-специфичного, presentation-слоя старого интерфейса) в чистый
модуль без зависимостей, потому что app/instruction_html.py (бизнес-логика,
переживает переход на pywebview — см. план миграции) использует только сами
значения цветов для генерации CSS в instruction.html, без единого
tkinter-виджета. Значения совпадают с app/web/frontend/css/tokens.css —
один и тот же источник палитры, продублированный в двух местах (Python для
instruction.html, CSS custom properties для остального фронтенда), потому
что первый генерирует статический HTML-файл на диск, а не рендерится внутри
самого pywebview-окна."""

BG_CARD = "#191d24"
TEXT = "#e9ecf1"
TEXT_DIM = "#9aa2b0"
ACCENT_2 = "#d98a3d"
BORDER = "#2b303a"

WARN_BG = "#4a3f1a"
WARN_BORDER = "#8a6d1f"
WARN_TEXT = "#f3e3a8"
DANGER_BG = "#4a2222"
DANGER_BORDER = "#8a3a3a"
DANGER_TEXT = "#f3c6c6"


def _mix_hex(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = round(r1 + (r2 - r1) * t)
    g = round(g1 + (g2 - g1) * t)
    b = round(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


BG_ELEVATED = _mix_hex(BG_CARD, BORDER, 0.55)
