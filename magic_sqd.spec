# -*- mode: python ; coding: utf-8 -*-
# pywebview-сборка (см. main_web.py, app/web/) — заменила customtkinter/
# tkinterweb. datas теперь — статические файлы app/web/frontend/ (HTML/CSS/
# JS), а не customtkinter_datas (тем/шрифтов у pywebview нет, он рисует
# страницу в WebView2). hiddenimports для pythonnet/clr_loader/webview не
# нужны вручную — их даёт готовый hook из pyinstaller-hooks-contrib (см.
# requirements.txt), проверено: hook-webview.py/hook-clr_loader.py уже есть
# в установленном пакете. Onedir (не onefile) сохраняется по прежней
# причине (см. историю этого файла) — не единственная больше, но менять
# нет смысла: onedir проще для докачки cars/apk рядом с exe.
a = Analysis(
    ['main_web.py'],
    pathex=[],
    binaries=[],
    datas=[('app/web/frontend', 'app/web/frontend')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='magic_sqd',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['assets\\icon.ico'],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='magic_sqd',
)
