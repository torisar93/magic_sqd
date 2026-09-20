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
Inno Setup и одинаково понимают эти флаги.

macOS: если программа запущена из собственного .app и в его папку можно
писать — обновляется сама (_worker_mac: скачать .dmg, проверить, положить
новый .app рядом и подменить после закрытия, данные пользователя внутри
Contents/MacOS переносятся, при сбое — откат); иначе открывается .dmg в
браузере, как раньше."""
from __future__ import annotations
import concurrent.futures
import json
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

from ..events import event_bridge
from ...content_config import get_download_base_url
from ...version import APP_VERSION

GITHUB_API_URL = "https://api.github.com/repos/torisar93/magic_sqd/releases"
# С версии 0.8.0 сами файлы инсталляторов версионируются в имени (см.
# installer.iss/installer_win7_x86.iss: OutputBaseFilename) — на GitHub
# у каждого релиза свой уникальный ассет, точное имя заранее не известно,
# поэтому ищем по регэкспу вместо точного совпадения (_ASSET_RE ниже).
# Свой сервер (см. server/backend.py:_handle_exe_upload) от версии
# файла независим — ВСЕГДА перезаписывает под фиксированным именем
# OWN_SERVER_ASSET_NAME, для него версионировать нечего.
_ASSET_RE = re.compile(r"^MagicSQD_Setup_\d+\.\d+\.\d+\.exe$", re.IGNORECASE)
_ASSET_RE_WIN7 = re.compile(r"^MagicSQD_Setup_Win7_\d+\.\d+\.\d+\.exe$", re.IGNORECASE)
# Имена из macos-arm64 job в .github/workflows/build-release.yml (см. её же
# комментарий про то, что x86_64 собирается вручную, а не в CI) —
# соответствующий .dmg подбирается по реальной архитектуре машины техника
# (platform.machine() в __init__), не по одному фиксированному имени.
_ASSET_RE_MAC_ARM64 = re.compile(r"^MagicSQD_\d+\.\d+\.\d+_arm64\.dmg$", re.IGNORECASE)
_ASSET_RE_MAC_X64 = re.compile(r"^MagicSQD_\d+\.\d+\.\d+_x86_64\.dmg$", re.IGNORECASE)
OWN_SERVER_ASSET_NAME = "MagicSQD_Setup.exe"
MAC_BUNDLE_ID = "ru.magicsqd.desktop"  # см. CFBundleIdentifier в magic_sqd_mac.spec
MAC_EXE_NAME = "magic_sqd"
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


def current_app_bundle() -> Path | None:
    """.app, из которого запущена собранная программа на macOS (или None при
    запуске из исходников/не из бандла): sys.executable — это
    <X>.app/Contents/MacOS/magic_sqd."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    macos_dir, contents_dir, bundle = exe.parent, exe.parent.parent, exe.parent.parent.parent
    if macos_dir.name == "MacOS" and contents_dir.name == "Contents" and bundle.suffix == ".app":
        return bundle
    return None


def can_replace_bundle(bundle: Path) -> bool:
    """Подмена бандла — это переименование папки .app, для него нужны права на
    запись в родительскую папку (обычно /Applications у администратора). Из
    образа .dmg, из Downloads под карантином (AppTranslocation — путь
    случайный и только для чтения) заменить нечего — тогда остаётся ручной
    путь через браузер."""
    if "/AppTranslocation/" in str(bundle):
        return False
    return os.access(bundle.parent, os.W_OK) and os.access(bundle, os.W_OK)


# Ждёт выхода программы, подменяет бандл и запускает новый. Отдельный процесс
# (sh) — наш живёт ровно до закрытия окна. Данные пользователя (cars/, apk/,
# сохранённый вход, client_id, логи…) лежат ВНУТРИ старого бандла, в
# Contents/MacOS/ рядом с исполняемым файлом (см. main_web.py:get_base_dir), —
# при простой подмене они пропали бы, поэтому переносим всё, кроме самого
# исполняемого файла, в новый бандл (mv в пределах одного тома — мгновенно, без
# копирования гигабайт скачанного контента). Любой сбой — откат на старую версию.
_MAC_SWAP_SCRIPT = r"""#!/bin/sh
PID="$1"; APP="$2"; NEW="$3"; BAK="$APP.old-update"
echo "$(date) swap: pid=$PID app=$APP new=$NEW"
i=0
while kill -0 "$PID" 2>/dev/null && [ "$i" -lt 120 ]; do sleep 0.5; i=$((i+1)); done
if kill -0 "$PID" 2>/dev/null; then echo "программа не закрылась — отмена"; rm -rf "$NEW"; exit 1; fi
rollback() {
  echo "откат: $1"
  if [ -d "$BAK" ]; then
    for item in "$NEW"/Contents/MacOS/* "$NEW"/Contents/MacOS/.[!.]*; do
      [ -e "$item" ] || [ -L "$item" ] || continue
      name=$(basename "$item")
      [ "$name" = "__EXE__" ] && continue
      [ -e "$BAK/Contents/MacOS/$name" ] || mv "$item" "$BAK/Contents/MacOS/$name"
    done
    rm -rf "$APP"
    mv "$BAK" "$APP"
  fi
  rm -rf "$NEW"
  open "$APP"
  exit 1
}
rm -rf "$BAK"
mv "$APP" "$BAK" || { echo "не удалось убрать старую версию"; rm -rf "$NEW"; open "$APP"; exit 1; }
for item in "$BAK"/Contents/MacOS/* "$BAK"/Contents/MacOS/.[!.]*; do
  [ -e "$item" ] || [ -L "$item" ] || continue
  name=$(basename "$item")
  [ "$name" = "__EXE__" ] && continue
  [ -e "$NEW/Contents/MacOS/$name" ] && continue
  mv "$item" "$NEW/Contents/MacOS/$name" || rollback "перенос $name"
done
mv "$NEW" "$APP" || rollback "подмена бандла"
open "$APP"
rm -rf "$BAK"
echo "$(date) swap: готово"
""".replace("__EXE__", MAC_EXE_NAME)


class UpdateApi:
    def __init__(self, base_dir: Path, is_win7: bool = False):
        self.base_dir = base_dir
        self.is_win7 = is_win7
        self.is_mac = sys.platform == "darwin"
        # Win7-сборка (см. installer_win7_x86.iss) публикует свой собственный
        # инсталлятор в том же GitHub-релизе, что и обычная сборка (см.
        # server/README.md §9 — один тег на цикл, три ассета). Свой сервер
        # (магазин content_config.get_download_base_url) зеркалирует ТОЛЬКО
        # обычную x64-сборку под фиксированным OWN_SERVER_ASSET_NAME (см.
        # server/backend.py:_handle_exe_upload) — для Win7 своего зеркала
        # нет, поэтому этот источник ниже пропускается. macOS — та же idea:
        # свой сервер зеркалирует только Windows x64, поэтому здесь тоже
        # только GitHub (см. _check_own_server), а нужный .dmg выбирается по
        # архитектуре ЭТОГО процесса — platform.machine() отражает то, ПОД
        # ЧЕМ он реально исполняется прямо сейчас (для x86_64-сборки,
        # запущенной под Rosetta на Apple Silicon, вернёт именно "x86_64",
        # а не архитектуру самого железа) — то есть даёт ровно тот .dmg,
        # который совместим с уже установленной копией.
        if is_win7:
            self._asset_re = _ASSET_RE_WIN7
        elif self.is_mac:
            self._asset_re = _ASSET_RE_MAC_X64 if platform.machine() == "x86_64" else _ASSET_RE_MAC_ARM64
        else:
            self._asset_re = _ASSET_RE
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

    def _own_server_asset_key(self) -> str:
        """Ключ в version.json["assets"] для ЭТОЙ сборки (см. scripts/mirror_release.sh)."""
        if self.is_win7:
            return "win7"
        if self.is_mac:
            return "macos_x86_64" if platform.machine() == "x86_64" else "macos_arm64"
        return "windows"

    def _check_own_server(self) -> dict | None:
        """Своё зеркало magicsqd.ru/download/ — с 1.0.17 не только Windows x64: version.json несёт карту
        assets {"windows","win7","macos_arm64","macos_x86_64","android"} → имя файла на зеркале (кладёт
        scripts/mirror_release.sh). Нет карты (version.json записан старой карточкой «Загрузить на сервер»
        в админке) — как раньше, только Windows x64 под фиксированным OWN_SERVER_ASSET_NAME."""
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
        if not isinstance(data, dict):
            return None
        assets = data.get("assets") if isinstance(data.get("assets"), dict) else {}
        key = self._own_server_asset_key()
        asset_name = assets.get(key) or (OWN_SERVER_ASSET_NAME if key == "windows" else None)
        if not isinstance(asset_name, str) or not asset_name or "/" in asset_name:
            return None
        version = str(data.get("version") or "")
        if not version or _parse_version(version) <= _parse_version(APP_VERSION):
            return None
        return {
            "available": True,
            "version": version,
            "changelog": str(data.get("changelog") or "").strip(),
            "download_url": f"{url}/{asset_name}",
            "asset_name": asset_name,
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
        asset = next((a for a in latest.get("assets", []) if self._asset_re.match(a.get("name") or "")), None)
        if not asset:
            return None
        return {
            "available": True,
            "version": version,
            "changelog": str(latest.get("body") or "").strip(),
            "download_url": asset["browser_download_url"],
            "asset_name": asset["name"],
        }

    # -- установка -----------------------------------------------------------
    def install(self, download_url: str) -> dict:
        if self._installing:
            return {"ok": False, "error": "Обновление уже выполняется."}
        if self.is_mac:
            # Автоматическая замена бандла (см. _worker_mac) — только если мы
            # запущены из собственного .app и в его папку можно писать (обычно
            # /Applications у администратора); иначе — как раньше: открываем
            # .dmg в браузере, технику остаётся перетащить приложение вручную.
            bundle = current_app_bundle()
            if bundle is not None and can_replace_bundle(bundle):
                self._installing = True
                threading.Thread(target=self._worker_mac, args=(download_url, bundle), daemon=True).start()
                return {"ok": True}
            try:
                webbrowser.open(download_url)
            except Exception as exc:  # noqa: BLE001 - открытие ссылки не должно ронять программу
                return {"ok": False, "error": f"Не удалось открыть ссылку на скачивание: {exc}"}
            return {"ok": True, "manual": True}
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
            # Имя файла — из самого download_url (версионировано что на
            # GitHub, что на своём сервере), а не фиксированная константа,
            # раз конкретное имя ассета больше не известно заранее.
            asset_name = Path(urllib.parse.urlsplit(download_url).path).name or "MagicSQD_Setup.exe"
            installer_path = Path(tempfile.gettempdir()) / asset_name
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
    def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[-400:]
            raise RuntimeError(f"{cmd[0]} вернул {result.returncode}: {detail}")
        return result

    def _worker_mac(self, download_url: str, bundle: Path, pid: int | None = None,
                    close_app: bool = True) -> None:
        """Скачать .dmg → смонтировать → проверить → скопировать новый .app
        рядом со старым → закрыть программу; дальше подмену делает sh-скрипт
        (_MAC_SWAP_SCRIPT). Файл, скачанный самой программой, не получает
        карантин Gatekeeper — после обновления системного предупреждения
        «неизвестный разработчик» нет."""
        dmg_path = None
        mount = None
        staging = bundle.with_name(f".{bundle.name}.update")
        try:
            asset_name = Path(urllib.parse.urlsplit(download_url).path).name or "MagicSQD.dmg"
            dmg_path = Path(tempfile.gettempdir()) / asset_name
            self._log("Скачивание обновления...")
            self._download(download_url, dmg_path, on_progress=self._progress)
            self._log("Проверяю обновление...")
            mount = Path(tempfile.mkdtemp(prefix="magicsqd_update_mnt_"))
            self._run(["hdiutil", "attach", "-nobrowse", "-noautoopen", "-readonly",
                       "-mountpoint", str(mount), str(dmg_path)])
            new_apps = sorted(mount.glob("*.app"))
            if not new_apps:
                raise RuntimeError("в образе обновления нет приложения")
            source = new_apps[0]
            info = plistlib.loads((source / "Contents" / "Info.plist").read_bytes())
            if info.get("CFBundleIdentifier") != MAC_BUNDLE_ID or not (source / "Contents" / "MacOS" / MAC_EXE_NAME).is_file():
                raise RuntimeError("образ обновления не похож на Magic SQD")
            if staging.exists():
                shutil.rmtree(staging)
            self._log("Копирую новую версию...")
            self._run(["ditto", str(source), str(staging)])
            self._run(["xattr", "-dr", "com.apple.quarantine", str(staging)], check=False)
            # Битую подпись ловим ДО подмены — иначе вместо обновления техник
            # получил бы «приложение повреждено» (см. project_macos_app_damaged_signing_bug).
            self._run(["codesign", "--verify", "--deep", "--strict", str(staging)])
            log_path = Path(tempfile.gettempdir()) / "magicsqd_update.log"
            script_path = Path(tempfile.gettempdir()) / "magicsqd_update_swap.sh"
            script_path.write_text(_MAC_SWAP_SCRIPT, encoding="utf-8")
            script_path.chmod(0o755)
            self._log("Обновление готово. Программа сейчас перезапустится...")
            with open(log_path, "ab") as log_file:
                subprocess.Popen(
                    ["/bin/sh", str(script_path), str(pid or os.getpid()), str(bundle), str(staging)],
                    stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file,
                    start_new_session=True, close_fds=True,
                )
        except Exception as exc:  # noqa: BLE001 - показываем пользователю любую ошибку
            shutil.rmtree(staging, ignore_errors=True)
            self._installing = False
            self._finished(False, f"Не удалось установить обновление: {exc}")
            return
        finally:
            if mount is not None:
                self._run(["hdiutil", "detach", "-force", str(mount)], check=False)
                shutil.rmtree(mount, ignore_errors=True)
            if dmg_path is not None:
                dmg_path.unlink(missing_ok=True)
        self._finished(True)
        time.sleep(1)  # даём JS показать финальную строку лога, прежде чем окно закроется
        if close_app:
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
        # Пауза — через ping, а не timeout: timeout.exe завершается сразу с
        # ошибкой "Input redirection is not supported", если у процесса нет
        # настоящего stdin (у нашего GUI-процесса без консоли его нет), и
        # тогда задержка пропадала бы вовсе. ping от stdin не зависит.
        bat_path.write_text(
            "@echo off\r\n"
            "ping -n 4 127.0.0.1 >nul\r\n"
            f'start "" "{installer_path}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART\r\n',
            encoding="utf-8",
        )
        # CREATE_NO_WINDOW, а не DETACHED_PROCESS: у DETACHED_PROCESS cmd.exe
        # вообще не получает консоли и заводит НОВОЕ ВИДИМОЕ окно под каждую
        # консольную команду внутри .bat (ping/timeout) — именно оно и
        # мелькало на экране с паузой перед перезапуском. CREATE_NO_WINDOW даёт
        # скрытую консоль, общую для всего .bat; процесс при этом всё равно
        # самостоятельный и переживает закрытие программы.
        subprocess.Popen(
            ["cmd", "/c", str(bat_path)],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
        )

    @staticmethod
    def _close_app() -> None:
        import webview
        if webview.windows:
            webview.windows[0].destroy()
