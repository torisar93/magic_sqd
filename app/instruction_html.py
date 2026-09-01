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
from __future__ import annotations
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
# Инлайновые SVG-иконки для .warn/.danger вместо эмодзи-как-иконки (см.
# ui-ux-pro-max: "Emoji as icons" — анти-паттерн, разный вид в разных ОС/
# шрифтах) — тот же geometric outline-стиль, что и остальной интерфейс (см.
# app/web/frontend/js — SVG иконки прямо в разметке). "#" в hex-цвете
# экранируем как %23 — иначе часть data-URI после него отрезается, как
# фрагмент URL.
def _svg_icon(path_d: str, color: str, viewbox: str = "0 0 24 24") -> str:
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}" fill="none" '
        f'stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{path_d}</svg>'
    )
    return "data:image/svg+xml," + svg.replace("#", "%23").replace('"', "'")


_WARN_ICON = _svg_icon(
    '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h16.9a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/>'
    '<path d="M12 9v4M12 17h.01"/>',
    theme.WARN_TEXT,
)
_DANGER_ICON = _svg_icon(
    '<circle cx="12" cy="12" r="9"/><path d="M12 8v5M12 16h.01"/>',
    theme.DANGER_TEXT,
)

# Вынесено отдельно от INSTRUCTION_CSS, потому что на Android этот блок
# может понадобиться ВТОРОЙ раз — вставленным через JS в документ верхнего
# окна, не в этот iframe (см. LIGHTBOX_SCRIPT: promoted-to-top-window режим
# ниже) — тот же текст, без дублирования цветов отдельным литералом.
_LIGHTBOX_CSS = f"""
  #magicsqd-lightbox {{
    position: fixed; inset: 0; background: rgba(8, 9, 12, .88);
    display: flex; align-items: center; justify-content: center; padding: 28px;
    opacity: 0; pointer-events: none; transition: opacity .18s ease; z-index: 1000;
    touch-action: none;
  }}
  #magicsqd-lightbox.is-open {{ opacity: 1; pointer-events: auto; }}
  #magicsqd-lightbox img {{
    max-width: 100%; max-height: 100%; border-radius: 8px; cursor: zoom-out;
    box-shadow: 0 12px 40px rgba(0, 0, 0, .55); will-change: transform;
    touch-action: none; user-select: none; -webkit-user-drag: none;
  }}
"""

INSTRUCTION_CSS = f"""
  body {{
    font-family: "Segoe UI", Arial, sans-serif; margin: 20px 16px; max-width: 760px;
    margin-inline: auto; background: {theme.BG_CARD}; color: {theme.TEXT}; font-size: 15.5px;
  }}
  h1 {{
    font-size: 21px; font-weight: 700; letter-spacing: -.2px; color: {theme.TEXT};
    margin: 0 0 18px; padding-bottom: 10px; border-bottom: 2px solid {theme.BORDER};
  }}
  /* Левая акцентная полоса вместо простого цветного текста — из общего
     потока текста секция читается как отдельный блок ещё до чтения
     заголовка (см. ui-ux-pro-max: visual hierarchy / scannability). */
  h2 {{
    font-size: 15.5px; font-weight: 600; color: {theme.ACCENT_2};
    margin: 26px 0 12px; padding: 3px 0 3px 12px; border-left: 3px solid {theme.ACCENT_2};
  }}
  p {{ line-height: 1.6; margin: 10px 0; }}
  /* Нумерованные шаги — раньше обычный браузерный список (мелкая точка с
     цифрой), теперь кружки-бейджи слева от текста: длинный список из
     8-10 шагов инженерного меню читается по шагам, а не сплошным блоком —
     каждый шаг физически отделён своим номером-чипом. */
  ol {{ list-style: none; counter-reset: step; padding-left: 0; margin: 14px 0; }}
  li {{
    position: relative; counter-increment: step; padding-left: 38px;
    margin-bottom: 14px; line-height: 1.55;
  }}
  li::before {{
    content: counter(step); position: absolute; left: 0; top: -1px;
    width: 25px; height: 25px; border-radius: 50%; background: {theme.ACCENT_2};
    color: {theme.BG_CARD}; font-size: 12.5px; font-weight: 700;
    display: flex; align-items: center; justify-content: center;
  }}
  .warn, .danger {{
    display: flex; gap: 10px; align-items: flex-start; padding: 10px 14px;
    border-radius: 8px; margin: 14px 0; line-height: 1.5;
  }}
  .warn {{ background: {theme.WARN_BG}; border-left: 3px solid {theme.WARN_BORDER}; color: {theme.WARN_TEXT}; }}
  .danger {{ background: {theme.DANGER_BG}; border-left: 3px solid {theme.DANGER_BORDER}; color: {theme.DANGER_TEXT}; }}
  .warn::before, .danger::before {{
    content: ""; flex: none; width: 18px; height: 18px; margin-top: 1px;
    background-repeat: no-repeat; background-size: contain;
  }}
  .warn::before {{ background-image: url("{_WARN_ICON}"); }}
  .danger::before {{ background-image: url("{_DANGER_ICON}"); }}
  .path {{ background: {theme.BG_ELEVATED}; padding: 2px 6px; border-radius: 3px; font-family: Consolas, monospace; color: {theme.ACCENT_2}; }}
  .caption {{ color: {theme.TEXT_DIM}; font-size: 12px; margin-top: -4px; text-align: center; }}
  /* Раньше просто <img style="max-width:100%"> — фото разных размеров и
     пропорций (скриншот телефона рядом со скриншотом магнитолы) смотрелись
     вразнобой; пробовали единить их рамкой (border+padding+подложка), но
     ЛЮБАЯ рамка — это видимая кайма другого цвета по краям фото независимо
     от пропорций (см. фидбэк — "тонкие рамки слева и справа, выглядит
     некрасиво", не ушло даже после фикса высоты/object-fit). Теперь без
     рамки вовсе — только скругление уголков самого фото и лёгкая тень,
     фото занимает всю ширину колонки естественной пропорцией, без каймы.
     Клик — на весь экран, см. LIGHTBOX_SCRIPT ниже. */
  img.screenshot {{
    display: block; box-sizing: border-box; width: 100%; max-width: 460px;
    height: auto; margin: 14px auto; border-radius: 8px; cursor: zoom-in;
    box-shadow: 0 1px 3px rgba(0, 0, 0, .3);
    transition: transform .15s ease, box-shadow .15s ease;
  }}
  img.screenshot:hover {{
    transform: translateY(-1px);
    box-shadow: 0 6px 16px rgba(0, 0, 0, .35);
  }}
  {_LIGHTBOX_CSS}
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

# Открытие фото на весь экран по клику. Делегирование через document, а не
# отдельный listener на каждый <img> — картинки могут появляться и в
# "html"-блоках (см. докстринг модуля), не только через штатный photo-блок.
#
# Фото "вылетает" из своей рамки в центр экрана (transform: translate+scale
# от прямоугольника миниатюры до её итогового места), а не просто
# появляется через fade — сохраняет пространственную связь клика с
# результатом (см. ui-ux-pro-max: spatial continuity). Анимируем только
# transform/opacity (не width/height — see Performance: layout thrashing).
# Открытие чуть медленнее закрытия (220мс/160мс, ease-out на входе, ease-in
# на выходе) — см. ui-ux-pro-max: "exit faster than enter". prefers-reduced-
# motion полностью отключает transform-анимацию, оставляя только сам факт
# открытия/закрытия.
#
# ГДЕ ЖИВЁТ overlay — не всегда в этом же iframe:
# - Десктоп (см. stage_wizard.js: buildInstructionBlock) — iframe с
#   sandbox="allow-scripts allow-popups..." БЕЗ allow-same-origin, у него
#   opaque origin: window.top недоступен вообще (кидает исключение), и
#   сама инструкция скроллится ВНУТРИ iframe (fullPage: flex:1) — значит
#   его CSS-viewport и есть видимая область, обычный position:fixed внутри
#   него корректно накрывает ровно то, что видно. Оставляем как раньше —
#   self-contained.
# - Android (см. android/.../app.js: instruction-этап) — iframe БЕЗ
#   sandbox вовсе (srcdoc наследует origin родителя — доступ к window.top
#   есть), и высота iframe синхронизируется с высотой содержимого вместо
#   собственного скролла — скроллится объемлющий контейнер СНАРУЖИ. Из-за
#   этого position:fixed ВНУТРИ iframe считает "экраном" весь документ
#   инструкции целиком (например 3000px), а не реально видимую часть
#   телефонного экрана — оверлей рисуется, но фото центрируется где-то по
#   середине всей инструкции и визуально "теряется" за пределами того, что
#   сейчас видно. Поэтому здесь, когда window.top доступен и это
#   действительно другое окно, overlay создаётся/переиспользуется ПРЯМО В
#   ВЕРХНЕМ документе (WebView), чей viewport — это уже настоящий экран.
#   Единственное, что при этом нужно пересчитывать — экранные координаты
#   миниатюры (getBoundingClientRect() внутри iframe меряет от верха ВСЕЙ
#   инструкции, а не от текущей видимой части) — прибавляем к ним текущее
#   положение самого iframe в верхнем окне (window.frameElement,
#   доступен благодаря тому же отсутствию sandbox), которое как раз и
#   меняется при скролле объемлющего контейнера.
LIGHTBOX_SCRIPT = f"""
<script>
(function () {{
  var LIGHTBOX_CSS = {json.dumps(_LIGHTBOX_CSS)};
  var reduceMotion = window.matchMedia
    && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var topWin = null;
  try {{
    if (window.top && window.top !== window && window.frameElement && window.top.document) topWin = window.top;
  }} catch (e) {{ topWin = null; }}
  var hostDoc = topWin ? topWin.document : document;

  var overlay = hostDoc.getElementById("magicsqd-lightbox");
  var img;
  if (overlay) {{
    img = overlay.querySelector("img");
  }} else {{
    if (topWin && !hostDoc.getElementById("magicsqd-lightbox-style")) {{
      var style = hostDoc.createElement("style");
      style.id = "magicsqd-lightbox-style";
      style.textContent = LIGHTBOX_CSS;
      hostDoc.head.appendChild(style);
    }}
    overlay = hostDoc.createElement("div");
    overlay.id = "magicsqd-lightbox";
    img = hostDoc.createElement("img");
    overlay.appendChild(img);
    hostDoc.body.appendChild(overlay);
  }}

  function screenRect(el) {{
    var r = el.getBoundingClientRect();
    if (!topWin) return r;
    var frameRect = window.frameElement.getBoundingClientRect();
    return {{ left: frameRect.left + r.left, top: frameRect.top + r.top, width: r.width, height: r.height }};
  }}

  function flyTransform(rect) {{
    var imgRect = img.getBoundingClientRect();
    if (!imgRect.width || !imgRect.height) return null;
    var dx = (rect.left + rect.width / 2) - (imgRect.left + imgRect.width / 2);
    var dy = (rect.top + rect.height / 2) - (imgRect.top + imgRect.height / 2);
    var sx = rect.width / imgRect.width;
    var sy = rect.height / imgRect.height;
    return "translate(" + dx + "px," + dy + "px) scale(" + sx + "," + sy + ")";
  }}

  var currentTrigger = null;

  // Зум/пан жестами после открытия — щипок двумя пальцами, перетаскивание
  // одним пальцем, когда уже приближено, двойное касание сбрасывает/
  // приближает (тот же жест, что в галереях телефона). MAX_ZOOM подобран
  // так, чтобы читать мелкий текст на скриншоте инженерного меню, но не
  // размывать фото в кашу.
  var MAX_ZOOM = 4;
  var zoomScale = 1, panX = 0, panY = 0;
  var pinchStartDist = 0, pinchStartScale = 1;
  var panStartX = 0, panStartY = 0, panStartPanX = 0, panStartPanY = 0;
  var lastTapTime = 0, lastTapX = 0, lastTapY = 0;
  var gestureMoved = false;

  function clampPan(scale, x, y) {{
    var imgRect = img.getBoundingClientRect();
    // getBoundingClientRect уже включает текущий transform — считаем от
    // "естественного" размера (без масштаба), чтобы не накапливать ошибку.
    var natW = imgRect.width / zoomScale, natH = imgRect.height / zoomScale;
    var overflowX = Math.max(0, (natW * scale - natW) / 2);
    var overflowY = Math.max(0, (natH * scale - natH) / 2);
    return {{
      x: Math.max(-overflowX, Math.min(overflowX, x)),
      y: Math.max(-overflowY, Math.min(overflowY, y)),
    }};
  }}

  function setTransform(animate) {{
    img.style.transition = animate
      ? "transform .18s cubic-bezier(.2,.8,.2,1)"
      : "none";
    img.style.transform = "translate(" + panX + "px," + panY + "px) scale(" + zoomScale + ")";
  }}

  function resetZoom(animate) {{
    zoomScale = 1; panX = 0; panY = 0;
    setTransform(animate);
  }}

  function toggleZoom(clientX, clientY) {{
    if (zoomScale > 1.01) {{
      resetZoom(true);
      return;
    }}
    var imgRect = img.getBoundingClientRect();
    // Приближаем к точке двойного касания, а не к центру — иначе после
    // зума палец оказывается совсем не там, где рассматривали фото.
    var offX = (imgRect.left + imgRect.width / 2 - clientX);
    var offY = (imgRect.top + imgRect.height / 2 - clientY);
    zoomScale = 2.4;
    var clamped = clampPan(zoomScale, offX * (zoomScale - 1) / 1, offY * (zoomScale - 1) / 1);
    panX = clamped.x; panY = clamped.y;
    setTransform(true);
  }}

  function open(trigger) {{
    currentTrigger = trigger;
    var rect = screenRect(trigger);
    img.src = trigger.src;
    img.alt = trigger.alt || "";
    resetZoom(false);
    overlay.classList.add("is-open");
    if (reduceMotion) return;
    var run = function () {{
      var transform = flyTransform(rect);
      if (!transform) return;
      img.style.transition = "none";
      img.style.transform = transform;
      img.style.opacity = "0";
      // Форсируем применение стартового transform ДО того, как повесим
      // transition — иначе браузер объединит оба шага в один кадр, и
      // "полёта" из миниатюры видно не будет, фото просто появится сразу
      // в центре.
      void img.offsetWidth;
      img.style.transition = "transform .22s cubic-bezier(.2,.8,.2,1), opacity .18s ease";
      img.style.transform = "translate(0,0) scale(1,1)";
      img.style.opacity = "1";
    }};
    if (img.complete && img.naturalWidth) requestAnimationFrame(run);
    else img.onload = function () {{ requestAnimationFrame(run); }};
  }}

  function close() {{
    var trigger = currentTrigger;
    currentTrigger = null;
    var transform = (!reduceMotion && trigger) ? flyTransform(screenRect(trigger)) : null;
    overlay.classList.remove("is-open");
    if (transform) {{
      img.style.transition = "transform .16s cubic-bezier(.4,0,1,1), opacity .16s ease";
      img.style.transform = transform;
      img.style.opacity = "0";
    }}
    setTimeout(function () {{
      img.src = ""; img.style.transition = ""; img.style.transform = ""; img.style.opacity = "";
      zoomScale = 1; panX = 0; panY = 0;
    }}, transform ? 160 : 0);
  }}

  function touchDist(t0, t1) {{
    var dx = t0.clientX - t1.clientX, dy = t0.clientY - t1.clientY;
    return Math.sqrt(dx * dx + dy * dy);
  }}

  overlay.addEventListener("touchstart", function (e) {{
    if (e.touches.length === 2) {{
      pinchStartDist = touchDist(e.touches[0], e.touches[1]);
      pinchStartScale = zoomScale;
      gestureMoved = true;
    }} else if (e.touches.length === 1) {{
      panStartX = e.touches[0].clientX; panStartY = e.touches[0].clientY;
      panStartPanX = panX; panStartPanY = panY;
      gestureMoved = false;
    }}
  }}, {{ passive: true }});

  overlay.addEventListener("touchmove", function (e) {{
    if (e.touches.length === 2 && pinchStartDist > 0) {{
      e.preventDefault();
      var dist = touchDist(e.touches[0], e.touches[1]);
      zoomScale = Math.max(1, Math.min(MAX_ZOOM, pinchStartScale * (dist / pinchStartDist)));
      var clamped = clampPan(zoomScale, panX, panY);
      panX = clamped.x; panY = clamped.y;
      setTransform(false);
    }} else if (e.touches.length === 1 && zoomScale > 1.01) {{
      e.preventDefault();
      var dx = e.touches[0].clientX - panStartX, dy = e.touches[0].clientY - panStartY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) gestureMoved = true;
      var clamped2 = clampPan(zoomScale, panStartPanX + dx, panStartPanY + dy);
      panX = clamped2.x; panY = clamped2.y;
      setTransform(false);
    }}
  }}, {{ passive: false }});

  overlay.addEventListener("touchend", function (e) {{
    if (e.touches.length > 0) return;
    // Подавляем синтетический click после тапа — закрытие/зум по тапу
    // обрабатываем целиком сами ниже (иначе на телефоне click от ПЕРВОГО
    // тапа двойного тапа закрыл бы лайтбокс раньше, чем долетит второй).
    e.preventDefault();
    pinchStartDist = 0;
    if (gestureMoved) return;
    var t = e.changedTouches[0];
    var now = Date.now();
    if (now - lastTapTime < 300 && Math.abs(t.clientX - lastTapX) < 30 && Math.abs(t.clientY - lastTapY) < 30) {{
      // Двойной тап — приблизить/сбросить.
      lastTapTime = 0;
      toggleZoom(t.clientX, t.clientY);
      return;
    }}
    lastTapTime = now; lastTapX = t.clientX; lastTapY = t.clientY;
    var tapTime = now;
    setTimeout(function () {{
      // Второй тап за это время не пришёл (иначе lastTapTime уже был бы
      // сброшен в 0 выше) — это одиночный тап, закрываем, только если
      // фото не приближено.
      if (lastTapTime === tapTime && zoomScale <= 1.01) close();
    }}, 300);
  }}, {{ passive: false }});

  overlay.addEventListener("wheel", function (e) {{
    e.preventDefault();
    var delta = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    var newScale = Math.max(1, Math.min(MAX_ZOOM, zoomScale * delta));
    if (newScale === zoomScale) return;
    zoomScale = newScale;
    var clamped = clampPan(zoomScale, panX, panY);
    panX = clamped.x; panY = clamped.y;
    setTransform(false);
  }}, {{ passive: false }});

  // Мышь: обычный клик закрывает лайтбокс, двойной — приближает/сбрасывает
  // (та же логика, что для тача выше — двойной клик рождает ДВА click перед
  // dblclick, поэтому закрытие по первому клику откладываем на время,
  // достаточное отличить его от начала двойного, и отменяем, если dblclick
  // всё же случился).
  var mouseClickTimer = null;
  overlay.addEventListener("click", function () {{
    if (zoomScale > 1.01) return;
    clearTimeout(mouseClickTimer);
    mouseClickTimer = setTimeout(close, 220);
  }});
  overlay.addEventListener("dblclick", function (e) {{
    clearTimeout(mouseClickTimer);
    toggleZoom(e.clientX, e.clientY);
  }});
  hostDoc.addEventListener("keydown", function (e) {{ if (e.key === "Escape") close(); }});
  document.addEventListener("click", function (e) {{
    var target = e.target;
    if (target && target.tagName === "IMG" && target.classList.contains("screenshot")) open(target);
  }});
}})();
</script>
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
        f"{LIGHTBOX_SCRIPT}"
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
