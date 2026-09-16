# -*- mode: python ; coding: utf-8 -*-
# macOS-сборка (см. magic_sqd.spec — Windows-версия, тот же main_web.py).
# Отличия от Windows:
#   - нет assets/python3.dll (это Windows-specific ABI-редирект для
#     резервного Qt-движка, см. magic_sqd.spec — на macOS его нет и не
#     нужно: app/qt_fallback.py вообще не используется, см.
#     main_web.py:_ensure_renderer — на не-Windows он сразу возвращает
#     {"gui": None} и до Qt-fallback дело не доходит).
#   - icon .icns вместо .ico (см. assets/icon.icns, собран из
#     assets/icon_source.png через iconutil).
#   - BUNDLE() — оборачивает COLLECT() в настоящий .app (Info.plist,
#     NSHighResolutionCapable и т.п.) — на Windows этого шага нет вообще,
#     там просто папка с exe.
#   - onedir (не onefile) по той же причине, что и на Windows — тут это
#     означает "весь Contents/MacOS/magic_sqd + всё окружение внутри
#     .app/Contents/Resources", а не единственный исполняемый файл.
import os

from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ['main_web.py'],
    pathex=[],
    binaries=[],
    # server.json/submit.json/admin.json — пишутся в корень репозитория шагом
    # CI "Write local config files from secrets" ДО этого PyInstaller-шага
    # (см. .github/workflows/build-release.yml, macos-arm64 job), но раньше
    # никто их никуда не копировал: на Windows этим занимается installer.iss
    # (кладёт рядом с .exe), а для macOS аналогичного шага не было вовсе —
    # итог: app/content_config.py молча не находил server.json, вся
    # синхронизация с сервером(cars/apk) молча пропускалась, интерфейс
    # запускался с пустым каталогом. Через datas= (как и app/web/frontend
    # выше) они попадают в Contents/Resources/ — то же место, что и
    # tools_mac/ (см. app/platform_paths.py:bundled_tools_root), а НЕ в
    # Contents/MacOS/: подкладывать обычные файлы туда уже после того, как
    # BUNDLE() ниже подпишет .app, ломает codesign-печать (см. память
    # project_macos_app_damaged_signing_bug — "приложение повреждено").
    # Файлы условны (bool([...]) is False, если их нет — например при
    # локальной сборке без секретов) — это тот же принцип "молча
    # пропускаем", что и вся остальная синхронизация с сервером.
    datas=[('app/web/frontend', 'app/web/frontend')] + [
        (name, '.') for name in ('server.json', 'submit.json', 'admin.json')
        if os.path.isfile(name)
    ],
    hiddenimports=collect_submodules('serial'),
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
    icon=['assets/icon.icns'],
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

# BUNDLE — макос-специфичный шаг, даёт настоящий Magic SQD.app вместо
# голой папки. CFBundleShortVersionString — берём из app/version.py, а не
# хардкодим (та же дисциплина, что installer.iss на Windows — версия
# синхронизируется вручную при релизе, см. app/version.py: APP_VERSION).
import sys
sys.path.insert(0, '.')
from app.version import APP_VERSION  # noqa: E402

app = BUNDLE(
    coll,
    name='Magic SQD.app',
    icon='assets/icon.icns',
    bundle_identifier='ru.magicsqd.desktop',
    info_plist={
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,
        'NSHighResolutionCapable': True,
        'LSMinimumSystemVersion': '11.0',
        'NSHumanReadableCopyright': 'Magic SQD',
    },
)
