"""Автообновление программы: проверка новой версии при каждом запуске (см.
app.js: checkForUpdate) и, если пользователь соглашается в диалоге, — тихая
переустановка через тот же .exe, что публикуется по релизному процессу из
server/README.md §9.

Два независимых источника версии (свой сервер и GitHub Releases,
см. _check_own_server/_check_github) — GitHub периодически недоступен в РФ,
поэтому его нельзя считать единственным источником проверки для этой
аудитории (см. память project_github_ru_unreliable). Опрашиваются
ПАРАЛЛЕЛЬНО (см. check()), берётся тот, где версия новее.

Скачивание — тот же chunked-подход через .part -> replace, что и в
app/content_sync.py:download_file, но без зависимости от него (тот модуль
завязан на server.json/content_config.py и свой протокол листинга, тут
источники — GitHub API и простой version.json).

Win7-сборка (is_win7=True, см. main_web_win7.py) ищет свой ассет
(MagicSQD_Setup_Win7.exe) в том же GitHub-релизе и НЕ опрашивает свой
сервер — тот зеркалирует только обычный x64-инсталлятор (см. UpdateApi.
__init__). Установка тем же /VERYSILENT-путём — оба инсталлятора собраны
Inno Setup и одинаково понимают эти флаги."""
from __future__ import annotations
import concurrent.futures
import json
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..events import event_bridge
from ...content_config import get_download_base_url
from ...version import APP_VERSION

GITHUB_API_URL = "https://api.github.com/repos/torisar93/magic_sqd/releases"
ASSET_NAME = "MagicSQD_Setup.exe"
ASSET_NAME_WIN7 = "MagicSQD_Setup_Win7.exe"
REQUEST_TIMEOUT_SECONDS = 8
DOWNLOAD_TIMEOUT_SECONDS = 60


def _parse_version(tag: str) -> tuple[int, ...]:
    """"v0.3.6-alpha" -> (0, 3, 6). Мусор в числовой части -> 0 для этого
    сегмента, не падаем — сравнение версий не должно ронять проверку
    обновлений из-за неожиданного формата тега."""
    tag = tag.strip().lstrip("vV")
    main_part = tag.split("-", 1)[0]
    parts = []
    for chunk in main_part.split("."):
        match = re.match(r"\d+", chunk)
        parts.append(int(match.group()) if match else 0)
    return tuple(parts) or (0,)


class UpdateApi:
    def __init__(self, base_dir: Path, is_win7: bool = False):
        self.base_dir = base_dir
        self.is_win7 = is_win7
        # Win7-сборка (см. installer_win7_x86.iss) публикует свой собственный
        # инсталлятор в том же GitHub-релизе, что и обычная сборка (см.
        # server/README.md §9 — один тег на цикл, три ассета). Свой сервер
        # (магазин content_config.get_download_base_url) зеркалирует ТОЛЬКО
        # MagicSQD_Setup.exe (см. server/backend.py:_handle_exe_upload) — для
        # Win7 своего зеркала нет, поэтому этот источник ниже пропускается.
        self.asset_name = ASSET_NAME_WIN7 if is_win7 else ASSET_NAME
        self._installing = False

    # -- проверка -----------------------------------------------------------
    def check(self) -> dict:
        """Молча возвращает {"available": False} при любой сетевой ошибке
        (или если оба источника не настроены/недоступны) — сбой проверки
        обновлений не должен мешать обычной работе программы."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            own_future = pool.submit(self._check_own_server)
            github_future = pool.submit(self._check_github)
            results = [r for r in (own_future.result(), github_future.result()) if r]

        if not results:
            return {"available": False}
        best = max(results, key=lambda r: _parse_version(r["version"]))
        return best

    def _check_own_server(self) -> dict | None:
        if self.is_win7:
            return None
        url = get_download_base_url(self.base_dir)
        if not url:
            return None
        try:
            req = urllib.request.Request(f"{url}/version.json")
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, UnicodeDecodeError):
            return None
        version = str(data.get("version") or "")
        if not version or _parse_version(version) <= _parse_version(APP_VERSION):
            return None
        return {
            "available": True,
            "version": version,
            "changelog": str(data.get("changelog") or "").strip(),
            "download_url": f"{url}/{self.asset_name}",
        }

    def _check_github(self) -> dict | None:
        try:
            req = urllib.request.Request(
                GITHUB_API_URL, headers={"Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
                releases = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError,
                json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not releases:
            return None
        # releases[0] (не /releases/latest) — включает prerelease, проект в
        # альфе (см. server/site/index.html и server/README.md §9 — то же
        # обоснование).
        latest = releases[0]
        version = str(latest.get("tag_name") or "")
        if not version or _parse_version(version) <= _parse_version(APP_VERSION):
            return None
        asset = next((a for a in latest.get("assets", []) if a.get("name") == self.asset_name), None)
        if not asset:
            return None
        return {
            "available": True,
            "version": version,
            "changelog": str(latest.get("body") or "").strip(),
            "download_url": asset["browser_download_url"],
        }

    # -- установка -----------------------------------------------------------
    def install(self, download_url: str) -> dict:
        if self._installing:
            return {"ok": False, "error": "Обновление уже выполняется."}
        self._installing = True
        threading.Thread(target=self._worker, args=(download_url,), daemon=True).start()
        return {"ok": True}

    def _log(self, message) -> None:
        event_bridge.push({"kind": "update_log", "text": str(message)})

    def _progress(self, done: int, total: int) -> None:
        event_bridge.push({"kind": "update_progress", "done": done, "total": total})

    def _finished(self, success: bool, message: str = "") -> None:
        event_bridge.push({"kind": "update_finished", "success": success, "message": message})

    def _worker(self, download_url: str) -> None:
        try:
            installer_path = Path(tempfile.gettempdir()) / self.asset_name
            self._log("Скачивание обновления...")
            self._download(download_url, installer_path, on_progress=self._progress)
            self._log("Обновление скачано. Программа сейчас закроется для установки...")
            self._spawn_installer(installer_path)
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку
            self._installing = False
            self._finished(False, f"Не удалось установить обновление: {exc}")
            return
        self._finished(True)
        time.sleep(1)  # даём JS показать финальную строку лога, прежде чем окно закроется
        self._close_app()

    @staticmethod
    def _download(url: str, dest: Path, on_progress=None) -> None:
        tmp = dest.with_name(dest.name + ".part")
        try:
            with urllib.request.urlopen(url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as resp, \
                    open(tmp, "wb") as f:
                # Content-Length почти всегда есть и у GitHub Releases, и у
                # своего сервера (см. server/backend.py — статическая раздача
                # файлов) — но не гарантирован нигде, поэтому total может
                # остаться 0 (см. UpdateDialog.setProgress на стороне JS —
                # там это отдельно обрабатывается, а не считается ошибкой).
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if on_progress:
                        on_progress(downloaded, total)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(dest)

    @staticmethod
    def _spawn_installer(installer_path: Path) -> None:
        """Спавнит ОТДЕЛЬНЫЙ detached-процесс, который сам ждёт пару секунд
        и только потом запускает инсталлятор — задержка обязана жить в этом
        независимом OS-процессе, а не в текущем daemon-потоке: наш процесс
        скоро сам завершится (см. _close_app), и daemon-потоки умрут вместе
        с ним, не успев ничего доспавнить. /VERYSILENT — без окон мастера
        (installer.iss:CloseApplications=yes — подстраховка на Restart
        Manager, если наш процесс всё же не успеет закрыться первым).

        Команда пишется во ВРЕМЕННЫЙ .bat-файл, а не передаётся строкой в
        Popen(["cmd", "/c", "...с кавычками и &..."]) — раньше было именно
        так, и subprocess на Windows заново экранирует ЦЕЛИКОМ через
        list2cmdline() любой элемент списка со спецсимволами, из-за чего
        cmd.exe получал ДВАЖДЫ проэкранированную строку и разваливал путь к
        инсталлятору (реальный случай — окно "Windows не удается найти
        "\\""). Содержимое .bat-файла не подвергается этой повторной
        обёртке в кавычки — cmd.exe читает его как обычный текстовый скрипт
        построчно, а не как один аргумент командной строки."""
        bat_path = Path(tempfile.gettempdir()) / "magicsqd_update.bat"
        bat_path.write_text(
            "@echo off\r\n"
            "timeout /t 3 /nobreak >nul\r\n"
            f'start "" "{installer_path}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART\r\n',
            encoding="utf-8",
        )
        subprocess.Popen(
            ["cmd", "/c", str(bat_path)],
            creationflags=subprocess.DETACHED_PROCESS,
            close_fds=True,
        )

    @staticmethod
    def _close_app() -> None:
        import webview
        if webview.windows:
            webview.windows[0].destroy()
