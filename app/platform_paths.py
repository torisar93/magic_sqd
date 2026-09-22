"""Где искать tools/tools_mac (adb/fastboot/aapt/минимальный JRE) —
единая точка правды для app/adb_utils.py, app/apk_icons.py, app/apk_signer.py.

На macOS внутри собранного .app подложенные бинарники переезжают в
Contents/Resources/, а НЕ лежат рядом с base_dir=get_base_dir() (Contents/
MacOS/) — codesign не может запечатать бандл, если среди файлов НАПРЯМУЮ
внутри Contents/MacOS/ есть хоть один обычный (не Mach-O) файл: реальный
случай — техник получил "приложение повреждено" при попытке открыть
установленную из DMG копию (codesign --verify: "a sealed resource is
missing or invalid", воспроизведено и на минимальном тестовом .app с одним
plain.txt прямо в Contents/MacOS/). Resources/ как раз и предназначена для
данных, а не только для кода — та же папка, куда PyInstaller BUNDLE() сам
кладёт datas= из .spec (см. main_web.py:get_frontend_dir)."""
from __future__ import annotations
import sys
from pathlib import Path


def bundled_tools_root(base_dir: Path) -> Path:
    """Родительская папка, где искать tools/ (Windows) или tools_mac/
    (macOS) — на Windows это всегда base_dir (рядом с exe, как раньше), при
    запуске из исходников (sys.frozen нет) — тоже base_dir.

    На macOS внутри собранного .app — Contents/Resources/, но вычисляем это
    НЕ через sys._MEIPASS (как было раньше), а напрямую от sys.executable
    (Contents/MacOS/<имя> → на уровень выше → Resources/). Реальный
    найденный баг (2026-09-22, см. память project_macos_finder_launch_adb_bug):
    для актуальной версии PyInstaller (6.x, onedir + BUNDLE) sys._MEIPASS на
    этой платформе указывает на Contents/Frameworks/, а НЕ Contents/
    Resources/ — сам PyInstaller туда кладёт симлинки почти на всё (app/,
    certifi/, base_library.zip и т.п., см. `ls -la Contents/Frameworks/`),
    поэтому большинство путей через _MEIPASS СЛУЧАЙНО продолжало работать
    (символическая ссылка прозрачно ведёт в Resources/). Но tools_mac/
    добавляется в бандл ОТДЕЛЬНЫМ шагом ПОСЛЕ самой сборки PyInstaller (см.
    build-release.yml: "Restore tools_mac/", scripts/build_intel_dmg.sh) —
    никогда не попадает в этот автоматический список симлинков, поэтому
    bundled_tools_root(...)/"tools_mac" через _MEIPASS указывал в
    Frameworks/tools_mac, которого никогда не существовало. find_adb_path
    поэтому ВСЕГДА тихо откатывался на голое "adb" из PATH — а на
    macOS-технике без Android SDK/другой копии adb в PATH такого пути
    просто нет: программа никогда не находила ни одного подключённого по
    USB устройства, на любой сборке, при любом способе запуска (не только
    из Finder — это было первое ложное впечатление, см. память). Прямой
    путь от sys.executable не зависит от того, что именно PyInstaller
    решит считать _MEIPASS в той или иной версии — только от неизменной
    структуры .app (Contents/{MacOS,Resources,Frameworks})."""
    if sys.platform == "darwin" and getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent / "Resources"
    return base_dir
