# -*- mode: python ; coding: utf-8 -*-
# Windows 7 (x86)-сборка — см. installer_win7_x86.iss за полным обоснованием
# (почему вообще отдельная сборка: WebView2, и даже официальный инсталлятор
# WebView2 Runtime от Microsoft, больше не работает на настоящей Windows 7 —
# не наша логика чинить, проверено на живой машине).
#
# PyQt5 (не PySide2!) — единственный рабочий движок для этой сборки. PySide2
# был первой попыткой (Qt5 — последняя версия Qt, ещё поддерживающая
# Windows 7), но у pywebview 6.2.1 обнаружился настоящий баг именно с
# PySide2/QtWebEngine: JS-мост (QWebChannel) либо вообще не подключается,
# либо подключается, но window.pywebview.api остаётся пустым/с неправильным
# содержимым — проверено и в заморозке, и из исходников, и на нескольких
# версиях pywebview (4.4.1/5.3.2/6.2.1 — у каждой своя вариация того же
# бага). PyQt5 — та же самая Qt5 (SIP-биндинг вместо Shiboken2) — с той же
# версией pywebview работает верно (window.pywebview.api полностью
# наполняется методами, проверено через прямое подключение к DevTools
# Protocol работающего процесса). GPL-лицензия PyQt5 (в отличие от LGPL у
# PySide2) годится именно потому, что сам проект выкладывается в открытый
# доступ.
#
# Собирается ОБЯЗАТЕЛЬНО 32-битным Python 3.8 (.venv-win7, py -3.8-32) —
# последняя версия CPython с официальной поддержкой Windows 7 (Python 3.9+
# требует Windows 8.1+, см. PEP 11). Настройка venv:
#   py -3.8-32 -m venv .venv-win7
#   .venv-win7\Scripts\python -m pip install -r requirements.txt PyQt5==5.15.4 PyQtWebEngine==5.15.4 qtpy
from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ['main_web_win7.py'],
    pathex=[],
    datas=[('app/web/frontend', 'app/web/frontend')],
    # PyQt5/qtpy — pywebview грузит Qt-бэкенд динамически (importlib, только
    # если gui='qt' реально запрошен), поэтому статический анализ PyInstaller
    # его сам не найдёт — hiddenimports обязателен. QtPrintSupport — жёсткая
    # рантайм-зависимость QtWebEngine (без него импорт Qt-бэкенда молча
    # падает и pywebview тихо откатывается на другой бэкенд — тот же баг,
    # что уже ловили на PySide6/x64, см. историю).
    hiddenimports=collect_submodules('serial') + [
        'qtpy', 'PyQt5', 'PyQt5.QtCore', 'PyQt5.QtGui', 'PyQt5.QtWidgets',
        'PyQt5.QtWebEngineWidgets', 'PyQt5.QtWebEngineCore', 'PyQt5.QtWebChannel',
        'PyQt5.QtPrintSupport',
    ],
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
