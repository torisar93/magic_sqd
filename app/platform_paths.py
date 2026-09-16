"""Где искать tools/tools_mac (adb/fastboot/aapt/минимальный JRE) —
единая точка правды для app/adb_utils.py, app/apk_icons.py, app/apk_signer.py.

На macOS внутри собранного .app подложенные бинарники переезжают в
Contents/Resources/, а НЕ лежат рядом с base_dir=get_base_dir() (Contents/
MacOS/) — codesign не может запечатать бандл, если среди файлов НАПРЯМУЮ
внутри Contents/MacOS/ есть хоть один обычный (не Mach-O) файл: реальный
случай — техник получил "приложение повреждено" при попытке открыть
установленную из DMG копию (codesign --verify: "a sealed resource is
missing or invalid", воспроизведено и на минимальном тестовом .app с одним
plain.txt прямо в Contents/MacOS/). Та же папка (Contents/Resources/), куда
PyInstaller BUNDLE() сам кладёт datas= из .spec (см. main_web.py:
get_frontend_dir, sys._MEIPASS) — там подпись всегда проходила чисто,
Resources/ как раз и предназначена для данных, а не только для кода."""
from __future__ import annotations
import sys
from pathlib import Path


def bundled_tools_root(base_dir: Path) -> Path:
    """Родительская папка, где искать tools/ (Windows) или tools_mac/
    (macOS) — на Windows это всегда base_dir (рядом с exe, как раньше), на
    macOS внутри собранного .app — sys._MEIPASS (Contents/Resources/), при
    запуске из исходников (sys._MEIPASS нет) — тоже base_dir, как раньше."""
    if sys.platform == "darwin":
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
    return base_dir
