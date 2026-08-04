# -*- mode: python ; coding: utf-8 -*-
# Админ-сборка (см. admin_main_web.py) — тот же app/web/, отдельный .exe/
# папка, чтобы не путаться с обычной сборкой для техников (magic_sqd.spec).
# См. комментарии там же про datas/hiddenimports — причина та же.
a = Analysis(
    ['admin_main_web.py'],
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
    name='magic_sqd_admin',
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
    name='magic_sqd_admin',
)
