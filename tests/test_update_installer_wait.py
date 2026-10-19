"""Windows-автообновление не оставляет программу сломанной. Скриншот техника (Honor на Windows 11, 30.09): после
обновления — «Cannot find win-arm64», чистая переустановка помогла. .bat запускал установщик через 3 с, а программа
после окна ещё останавливает adb (до 15 с) и досылает журнал: [InstallDelete] стирал _internal, занятые файлы
оставались, копирование спотыкалось, /SUPPRESSMSGBOXES отвечал «Прервать». Теперь .bat ждёт выхода процесса, а
установщик закрывает держащих файлы принудительно; не запустилась всё-таки — понятное окно (app/startup_errors.py)."""
from __future__ import annotations
import re
import subprocess
import sys
from pathlib import Path

import main_web
from app.startup_errors import broken_install_message
from app.web.api import update_api
from app.web.api.update_api import UPDATE_WAIT_SECONDS, UpdateApi, update_bat_text

ROOT = Path(__file__).resolve().parents[1]


def test_bat_waits_for_the_program_to_exit_before_the_installer():
    bat = update_bat_text(4321, Path(r"C:\Users\E106~1\AppData\Local\Temp\MagicSQD_Setup_1.0.51.exe")).split("\r\n")
    check = next(i for i, line in enumerate(bat) if "tasklist.exe" in line)
    assert bat[check] == (r'%SystemRoot%\System32\tasklist.exe /FI "PID eq 4321" /NH | '
                          r'%SystemRoot%\System32\find.exe "4321" >nul')
    assert bat[check + 1] == "if errorlevel 1 goto run"  # процесса нет — ставим
    assert f"if %n% geq {UPDATE_WAIT_SECONDS} goto kill" in bat and bat[bat.index(":kill") + 1].startswith(
        r"%SystemRoot%\System32\taskkill.exe /F /PID 4321")  # не закрылась за минуту — принудительно
    run = bat.index(":run")
    assert bat[run + 1] == (r'start "" "C:\Users\E106~1\AppData\Local\Temp\MagicSQD_Setup_1.0.51.exe" '
                            "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART")
    assert bat.index(":wait") < check < bat.index(":kill") < run  # установщик — только после ожидания
    assert bat[1].endswith("ping.exe -n 4 127.0.0.1 >nul")  # прежние 3 с — если проверка не сработает


def test_spawn_writes_the_bat_with_our_pid(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(update_api.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(update_api.subprocess, "Popen", lambda cmd, **kw: calls.append(cmd))
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)  # есть только на Windows
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(update_api.os, "getpid", lambda: 777)
    UpdateApi._spawn_installer(tmp_path / "MagicSQD_Setup_1.0.51.exe")
    bat = tmp_path / "magicsqd_update.bat"
    assert calls == [["cmd", "/c", str(bat)]]
    assert 'tasklist.exe /FI "PID eq 777"' in bat.read_text(encoding="utf-8")


def test_installers_force_close_whatever_holds_program_files():
    for name in ("installer.iss",):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert re.search(r"^CloseApplications=force$", text, re.M), name
        assert re.search(r"^CloseApplicationsFilter=\*\.exe,\*\.dll,\*\.pyd,\*\.chm$", text, re.M), name
        assert re.search(r"^SetupLogging=yes$", text, re.M), name  # журнал и без /LOG


def test_internal_is_swapped_not_wiped():
    """Владелец 19.10.2026: 1.1.11 → 1.1.12 — «No module named 'collections.abc'», программа не запускается.
    [InstallDelete] стирал _internal до копирования, а оборвавшееся копирование откат не возвращал. Теперь _internal
    переименовывается до установки (не вышло — закрыть процессы из папки программы и повторить, иначе не начинать),
    удаляется после удачной установки и возвращается при сбое."""
    text = (ROOT / "installer.iss").read_text(encoding="utf-8")

    def section(name):  # заголовок раздела — в начале строки ([InstallDelete] встречается и в комментариях)
        start = re.search(rf"^\[{name}\]$", text, re.M).end()
        nxt = re.compile(r"^\[[A-Za-z]+\]$", re.M).search(text, start)
        return text[start:nxt.start() if nxt else len(text)]

    delete = section("InstallDelete")
    assert not re.search(r'^Type: filesandordirs; Name: "\{app\}\\_internal"', delete, re.M), "стирать заранее нельзя"
    assert re.search(r'^Type: filesandordirs; Name: "\{app\}\\app"', delete, re.M)  # старая плоская раскладка
    code = section("Code")

    def body(signature):
        start = code.index(signature)
        return code[start:code.index("\nend;", start)]

    prepare = body("function PrepareToInstall(")
    assert "RenameFile(InternalDir(), OldDir)" in prepare and "KillAppProcesses()" in prepare
    assert "Result := CustomMessage('FilesInUse')" in prepare  # не вышло — установка не начинается
    assert "DelTree(OldDir" in body("procedure CurStepChanged(") and "ssPostInstall" in body("procedure CurStepChanged(")
    restore = body("procedure DeinitializeSetup(")
    assert "RenameFile(OldDir, InternalDir())" in restore and "if InstallFinished then" in restore
    kill = body("procedure KillAppProcesses(")
    assert "ExecutablePath.StartsWith(" in kill and "Stop-Process" in kill  # только запущенное из папки программы
    for lang in ("russian", "english"):
        assert re.search(rf"^{lang}\.FilesInUse=.+", text, re.M), lang
    # Маска «_internal.*» совпадает и с самой _internal — уборка остатков обязана проверять имя (иначе стирала
    # только что установленную папку — поймано в ВМ 19.10.2026).
    cleanup = body("procedure CleanupLeftovers(")
    assert "Pos('_internal.old', Name) = 1" in cleanup and "Pos('_internal.failed', Name) = 1" in cleanup


def test_server_json_is_installed_before_the_program():
    """Новый exe со старым server.json (без app_build_secret) остаётся без ключа закрытого каталога — всё 403, а
    флешка писалась пустой (лог №4505, ПК 1.1.1: программу запустили посреди тихого обновления). Поэтому файлы
    настроек ставятся раньше файлов программы."""
    text = (ROOT / "installer.iss").read_text(encoding="utf-8")
    files = text[text.index("[Files]"):text.index("[Icons]")]
    program = files.index('Source: "dist\\magic_sqd\\*"')
    for name in ("server.json", "submit.json", "admin.json"):
        assert files.index(f'Source: "{name}"') < program, name


def test_broken_install_gets_a_plain_message():
    text = broken_install_message(FileNotFoundError("Cannot find win-arm64"))
    assert text.startswith("Файлы программы повреждены") and "удалять программу не нужно" in text
    assert text.endswith("Подробности: Cannot find win-arm64")
    assert broken_install_message(ModuleNotFoundError("No module named 'app.x'"))

    class WebViewException(Exception):
        pass

    assert broken_install_message(WebViewException("You must have pythonnet installed in order to use pywebview."))
    assert broken_install_message(WebViewException("something else")) is None
    assert broken_install_message(RuntimeError("boom")) is None


def test_startup_crash_shows_the_plain_message_on_windows(monkeypatch, tmp_path):
    shown = []

    def broken_start(*args):
        raise FileNotFoundError("Cannot find win-arm64")

    monkeypatch.setattr(main_web, "run", broken_start)
    monkeypatch.setattr(main_web, "get_base_dir", lambda: tmp_path)
    monkeypatch.setattr(main_web, "_log_step", lambda text: None)
    monkeypatch.setattr(main_web.sys, "platform", "win32")

    class User32:
        @staticmethod
        def MessageBoxW(hwnd, text, caption, flags):
            shown.append((text, caption, flags))

    import ctypes
    monkeypatch.setattr(ctypes, "windll", type("W", (), {"user32": User32})(), raising=False)
    try:
        main_web._run_with_crash_log(False, "", "Magic SQD")
    except SystemExit as exit_:
        assert exit_.code == 1
    else:
        raise AssertionError("должен выйти после окна, без второго окна с трассировкой")
    assert shown and shown[0][0].startswith("Файлы программы повреждены") and shown[0][2] == 0x10
    assert "Cannot find win-arm64" in (tmp_path / "crash.log").read_text(encoding="utf-8")
