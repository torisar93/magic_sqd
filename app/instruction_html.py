"""Генерация/разбор instruction.html из простого списка "блоков" — общий
шаблон оформления (тот же, что и в cars/Demo/Test Model X1 и
cars/Geely/Atlas New — заголовки, нумерованные шаги, жёлтая/красная
плашки, скриншоты), чтобы инструкции разных моделей выглядели одинаково,
даже если их пишут разные люди в редакторе (см. app/instruction_editor.py).

Блок — обычный dict, без отдельного класса (его же кладём как есть в JSON,
встроенный в сам instruction.html — см. EDITOR_MARKER/render_document —
это и позволяет открыть уже сохранённую инструкцию заново в редакторе):
    {"type": "h1" | "h2" | "p" | "warn" | "danger", "text": "..."}
    {"type": "steps", "text": "шаг 1\\nшаг 2\\n..."}
    {"type": "photo", "path": "<абсолютный путь на диске>", "caption": "..."}
    {"type": "html", "text": "<произвольная HTML-разметка>"} — вставляется
        как есть, БЕЗ экранирования (единственный такой тип блока) — для
        готовой разметки/виджетов на JS, которые не выразить остальными
        типами блоков (например калькулятор кода инженерного меню по
        текущей дате в freetuga-моделях — раньше такое можно было вписать
        только вручную прямо в уже сгенерированный instruction.html, в
        обход редактора, и следующее сохранение через редактор стирало
        вставку).

Поле "path" фото-блока в памяти редактора — ВСЕГДА абсолютный путь
(к ещё не скопированному исходнику или уже скопированному файлу внутри
model_dir/images/); относительным ("images/имя.jpg") оно становится только
на диске — и в src=, и в блоках, встроенных в сам HTML (см.
save_instruction/parse_blocks) — так инструкция остаётся переносимой сама
по себе."""
import html
import json
import re
import shutil
from pathlib import Path

from . import colors as theme

EDITOR_MARKER = "magicsqd-instruction-editor:v1"

BLOCK_TYPE_LABELS = {
    "h1": "Заголовок",
    "h2": "Подзаголовок",
    "p": "Текст",
    "steps": "Шаги",
    "warn": "Важно",
    "danger": "Осторожно",
    "photo": "Фото",
    "html": "HTML-код",
}

# Та же палитра/классы, что уже вручную писались в существующих
# instruction.html (.warn/.danger/.path/img.screenshot) — теперь взята из
# app/theme.py (единый источник цветов), а не задублирована собственными
# литералами — иначе правка палитры в theme.py не долетает до сгенерированных
# инструкций, и они визуально расходятся с остальным приложением.
INSTRUCTION_CSS = f"""
  body {{ font-family: "Segoe UI", Arial, sans-serif; margin: 16px; background: {theme.BG_CARD}; color: {theme.TEXT}; }}
  h1 {{ font-size: 18px; color: {theme.ACCENT_2}; }}
  h2 {{ font-size: 15px; margin-top: 20px; color: {theme.ACCENT_2}; }}
  ol {{ padding-left: 22px; }}
  li {{ margin-bottom: 8px; line-height: 1.4; }}
  p {{ line-height: 1.4; }}
  .warn {{ background: {theme.WARN_BG}; border: 1px solid {theme.WARN_BORDER}; padding: 8px 12px; border-radius: 4px; color: {theme.WARN_TEXT}; }}
  .danger {{ background: {theme.DANGER_BG}; border: 1px solid {theme.DANGER_BORDER}; padding: 8px 12px; border-radius: 4px; color: {theme.DANGER_TEXT}; }}
  .path {{ background: {theme.BG_ELEVATED}; padding: 2px 6px; border-radius: 3px; font-family: Consolas, monospace; color: {theme.ACCENT_2}; }}
  .caption {{ color: {theme.TEXT_DIM}; font-size: 12px; margin-top: -4px; }}
  img.screenshot {{ max-width: 100%; border: 1px solid {theme.BORDER}; border-radius: 4px; margin: 6px 0; }}
  /* Инструкция рендерится в собственном <iframe> (см. srcdoc в
     stage_wizard.js/instruction_editor.js) — это отдельный документ, общий
     скроллбар из css/tokens.css сюда не долетает, поэтому дублируем его
     тут же, чтобы скроллбар не был белым дефолтным на тёмном фоне. */
  ::-webkit-scrollbar {{ width: 12px; height: 12px; }}
  ::-webkit-scrollbar-track {{ background: {theme.BG_CARD}; }}
  ::-webkit-scrollbar-thumb {{ background: {theme.BORDER}; border-radius: 8px; border: 3px solid {theme.BG_CARD}; }}
  ::-webkit-scrollbar-thumb:hover {{ background: {theme.BG_ELEVATED}; }}
  ::-webkit-scrollbar-corner {{ background: {theme.BG_CARD}; }}
"""

_BLOCKS_RE = re.compile(
    r'<script type="application/json" id="magicsqd-blocks">(.*?)</script>', re.DOTALL)

# Ссылки на источники ("Источники: drive2.ru/l/..., t.me/...") в блоках
# набираются просто текстом (см. instruction_editor.py — там нет отдельного
# поля/кнопки "вставить ссылку"), поэтому кликабельными их делает только
# сам рендер — распознаём URL/голые домены прямо в тексте и заворачиваем в
# <a>. Список доменных зон — по факту уже встречающимся в текстах инструкций
# проекта (drive2.ru, t.me, 4pda.to, github.com и т.п.), не общий белый
# список TLD — так словосочетания вроде "install.bat"/"GeelyTool.exe" не
# принимаются за домен.
_URL_RE = re.compile(
    r'(?:https?://)?'
    r'(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+'
    r'(?:ru|com|to|me|su|net|org|io|by)'
    r'(?:/[^\s,]*)?',
    re.IGNORECASE,
)
_URL_TRAILING_PUNCT = '.,;:)'


def _linkify(text: str) -> str:
    """html.escape(text), но с настоящими <a target="_blank"> вместо
    голого текста там, где похоже на URL/домен (см. _URL_RE). target=_blank
    открывается в системном браузере, а не подменяет саму инструкцию в
    iframe — см. sandbox="allow-popups allow-popups-to-escape-sandbox" в
    stage_wizard.js/instruction_editor.js/index.html (без allow-popups
    клик по такой ссылке в песочнице iframe просто ничего не делает)."""
    parts = []
    pos = 0
    for m in _URL_RE.finditer(text):
        start, end = m.span()
        url = m.group(0)
        trail = ""
        while url and url[-1] in _URL_TRAILING_PUNCT:
            trail = url[-1] + trail
            url = url[:-1]
            end -= 1
        if not url:
            continue
        parts.append(html.escape(text[pos:start]))
        href = url if url.lower().startswith(("http://", "https://")) else f"https://{url}"
        parts.append(
            f'<a href="{html.escape(href)}" target="_blank" rel="noopener noreferrer">'
            f"{html.escape(url)}</a>{html.escape(trail)}"
        )
        pos = end
    parts.append(html.escape(text[pos:]))
    return "".join(parts)


def default_blocks(brand: str, model: str) -> list[dict]:
    title = f"{brand} {model}".strip() or "Новая модель"
    return [
        {"type": "h1", "text": f"{title} — инструкция по установке"},
        {"type": "p", "text": "Опишите здесь, как получить доступ к магнитоле "
                               "(инженерное меню, включение ADB и т.д.)."},
    ]


def _escape_multiline(text: str) -> str:
    return _linkify(text).replace("\n", "<br>")


def _render_block(block: dict, href_fn) -> str:
    block_type = block.get("type")
    if block_type == "h1":
        return f"<h1>{html.escape(block.get('text', ''))}</h1>"
    if block_type == "h2":
        return f"<h2>{html.escape(block.get('text', ''))}</h2>"
    if block_type == "p":
        return f"<p>{_escape_multiline(block.get('text', ''))}</p>"
    if block_type == "steps":
        items = [line.strip() for line in block.get("text", "").splitlines() if line.strip()]
        if not items:
            return ""
        lis = "".join(f"<li>{_linkify(line)}</li>" for line in items)
        return f"<ol>{lis}</ol>"
    if block_type == "warn":
        return f'<p class="warn">{_escape_multiline(block.get("text", ""))}</p>'
    if block_type == "danger":
        return f'<p class="danger">{_escape_multiline(block.get("text", ""))}</p>'
    if block_type == "html":
        # Без html.escape/_linkify — техник сам отвечает за то, что здесь
        # написано (см. докстринг модуля). Пусто, если текст пуст, чтобы
        # не плодить пустой абзац в документе.
        return block.get("text", "").strip()
    if block_type == "photo" and block.get("path"):
        href = href_fn(block)
        caption = block.get("caption", "").strip()
        alt = html.escape(caption or "фото")
        parts = [f'<img class="screenshot" alt="{alt}" src="{href}">']
        if caption:
            parts.append(f'<p class="caption">{html.escape(caption)}</p>')
        return "\n".join(parts)
    return ""


def render_document(blocks: list[dict], href_fn) -> str:
    """href_fn(photo_block) -> строка для src= — вызывающий решает, что там:
    абсолютный file:// URI для живого предпросмотра (файл ещё может не
    лежать внутри model_dir) или относительный "images/..." для сохранения
    на диск (см. save_instruction)."""
    title = next((b.get("text", "") for b in blocks if b.get("type") == "h1"), "") or "Инструкция"
    body_html = "\n".join(part for b in blocks if (part := _render_block(b, href_fn)))
    # JSON-блоки встраиваем как есть — на этом основан повторный разбор
    # уже сохранённой инструкции (см. parse_blocks). "</" экранируем, чтобы
    # текст пользователя не мог случайно преждевременно закрыть <script>.
    blocks_json = json.dumps(blocks, ensure_ascii=False).replace("</", "<\\/")
    return (
        "<!DOCTYPE html>\n"
        f'<html lang="ru"><head><meta charset="utf-8"><title>{html.escape(title)}</title>\n'
        f"<style>{INSTRUCTION_CSS}</style>\n"
        f"<!-- {EDITOR_MARKER} -->\n"
        f'<script type="application/json" id="magicsqd-blocks">{blocks_json}</script>\n'
        "</head>\n<body>\n"
        f"{body_html}\n"
        "</body></html>\n"
    )


def render_preview(blocks: list[dict]) -> str:
    """Живой предпросмотр в редакторе — фото ещё не скопированы в
    model_dir/images (модели может и не существовать, если машина ещё
    создаётся), поэтому ссылаемся прямо на исходный файл на диске."""
    return render_document(blocks, lambda b: Path(b["path"]).resolve().as_uri())


def parse_blocks(html_text: str, model_dir: Path) -> list[dict] | None:
    """Разбирает уже сохранённый instruction.html обратно в блоки — только
    если он был создан этим редактором (см. EDITOR_MARKER); для руками
    написанных инструкций возвращает None (см. add_car_dialog.py — в этом
    случае редактор открывается с пустым шаблоном, а не пытается угадать
    структуру произвольного HTML). Относительные пути фото-блоков
    разворачивает в абсолютные (см. модульный docstring)."""
    if EDITOR_MARKER not in html_text:
        return None
    match = _BLOCKS_RE.search(html_text)
    if not match:
        return None
    try:
        blocks = json.loads(match.group(1).replace("<\\/", "</"))
    except json.JSONDecodeError:
        return None
    resolved = []
    for block in blocks:
        if block.get("type") == "photo" and block.get("path"):
            block = {**block, "path": str((model_dir / block["path"]).resolve())}
        resolved.append(block)
    return resolved


def save_instruction(model_dir: Path, blocks: list[dict]) -> None:
    """Копирует фото блоков в model_dir/images/ (кроме уже лежащих там —
    см. модульный docstring) и пишет model_dir/instruction.html. Не трогает
    список blocks вызывающего — работает с копиями."""
    images_dir = (model_dir / "images").resolve()
    model_dir_resolved = model_dir.resolve()
    resolved_blocks = []
    for block in blocks:
        if block.get("type") != "photo" or not block.get("path"):
            resolved_blocks.append(block)
            continue
        source_path = Path(block["path"]).resolve()
        if images_dir in source_path.parents:
            rel = source_path.relative_to(model_dir_resolved).as_posix()
        else:
            images_dir.mkdir(parents=True, exist_ok=True)
            dest = images_dir / source_path.name
            counter = 1
            while dest.exists():
                dest = images_dir / f"{source_path.stem}_{counter}{source_path.suffix}"
                counter += 1
            shutil.copy2(source_path, dest)
            rel = dest.relative_to(model_dir_resolved).as_posix()
        resolved_blocks.append({**block, "path": rel})

    # Убираем фото, которые раньше были скопированы сюда, но в текущем
    # наборе блоков на них больше никто не ссылается (убрали блок/сменили
    # фото) — тот же принцип, что и для files/usb_files в car_generator.py.
    if images_dir.is_dir():
        keep_names = {Path(b["path"]).name for b in resolved_blocks if b.get("type") == "photo"}
        for existing in images_dir.iterdir():
            if existing.is_file() and existing.name not in keep_names:
                existing.unlink()
        if not any(images_dir.iterdir()):
            images_dir.rmdir()

    html_text = render_document(resolved_blocks, lambda b: b["path"])
    (model_dir / "instruction.html").write_text(html_text, encoding="utf-8")
