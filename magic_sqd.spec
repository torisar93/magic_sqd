# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files

# customtkinter хранит темы/шрифты как файлы данных (.json/.otf) рядом со
# своим кодом — PyInstaller их не подхватывает автоматически без этого.
# Официально для customtkinter поддерживается только --onedir (не
# --onefile): в --onefile данные распаковываются во временную папку при
# каждом запуске, и это ненадёжно (мигающее окно, которое тут же
# закрывается, — типичный симптом, который мы и получили на реальной
# машине). Поэтому здесь COLLECT (--onedir) вместо прежнего EXE-only.
customtkinter_datas = collect_data_files("customtkinter")

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=customtkinter_datas,
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
