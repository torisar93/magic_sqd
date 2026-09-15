# dmgbuild settings — see https://dmgbuild.readthedocs.io/en/latest/settings.html
# Использование: dmgbuild -s scripts/dmg_settings.py -Dapp="dist/Magic SQD.app" "Magic SQD" out.dmg

import os

app = defines.get("app", "dist/Magic SQD.app")
background_path = os.path.join(os.getcwd(), "assets", "dmg_background.png")

files = [app]
symlinks = {"Applications": "/Applications"}

badge_icon = None
icon_size = 80

background = background_path

# window_rect: ((x, y), (w, h)) — x,y это позиция ЛЕВОГО НИЖНЕГО угла окна на
# экране, отсчёт y снизу вверх (Quartz/PDF-style), а не как в Finder AppleScript.
window_rect = ((200, 200), (720, 484))

# icon_locations: ЭМПИРИЧЕСКИ координаты здесь используют ТУ ЖЕ систему
# отсчёта, что и пиксели фоновой картинки (сверху вниз, без переворота) —
# сдвиг на высоту заголовка окна Finder применяется ОДИНАКОВО что к фону,
# что к иконкам, поэтому относительно друг друга их можно ставить как есть,
# без дополнительной коррекции. Проверено скриншотом реального окна.
icon_locations = {
    os.path.basename(app): (208, 222),
    "Applications": (508, 222),
}
