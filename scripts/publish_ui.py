#!/usr/bin/env python3
"""Выложить правку интерфейса БЕЗ выпуска программы (app/ui_bundle.py; владелец, 2026-10-06: «чтобы обновлений
приложения стало поменьше»).

Бандл интерфейса — файлы app/web/frontend/ (ПК) и android/app/src/main/assets/ (Android) из git, подписанные ключом
разработчика (~/.magicsqd/ui_signing_ed25519.key — не в git и не на сервере; на другой компьютер переносить вручную).
Программа версии X скачивает бандл для X при запуске, проверяет подпись и показывает его со следующего запуска.

Бандл годится, только если после выпуска (тег vX) менялся ТОЛЬКО интерфейс: программа X не знает новых методов моста
Python/Kotlin. Скрипт сверяет git diff vX..<ref> и отказывается, если для платформы менялось что-то ещё. Тогда правку
интерфейса делают в ветке от тега: git switch -c ui/X vX, git cherry-pick <коммит>, --ref ui/X.

  python scripts/publish_ui.py                      # что будет выложено — ничего не меняет
  python scripts/publish_ui.py --yes                # выложить для последнего выпуска (тег от HEAD)
  python scripts/publish_ui.py --version 1.0.62 --ref ui/1.0.62 --yes
  python scripts/publish_ui.py --platform android --disable --yes   # выключить: со следующего запуска встроенный
  python scripts/publish_ui.py --status             # что сейчас на сервере

Нужны git и ssh-ключ ~/.ssh/magicsqd_deploy (как у server/deploy.sh). Сервер: content/ui/ (раздаёт nginx как
/content/ui/), манифест контента пересобирать не нужно — ui/ в него не входит."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import ed25519, ui_bundle  # noqa: E402

HOST = "root@94.102.89.93"
SSH_KEY = Path.home() / ".ssh" / "magicsqd_deploy"
REMOTE_DIR = "/opt/magicsqd/content/ui"
PUBLIC_URL = "https://magicsqd.ru/content"
KEY_FILE = Path(os.environ.get("MAGICSQD_UI_KEY_FILE") or Path.home() / ".magicsqd" / "ui_signing_ed25519.key")

UI_DIRS = {"desktop": "app/web/frontend/", "android": "android/app/src/main/assets/"}
# Текстовые файлы интерфейса кладутся в бандл все (лёгкие, и бандл не зависит от того, из какого дерева собран
# выпуск); картинки и прочее — только изменённые после выпуска.
ALWAYS_SUFFIXES = {".html", ".js", ".css", ".json", ".svg", ".txt"}
# Не влияет на собранную программу ни одной платформы.
NON_RUNTIME_PREFIXES = ("tests/", "research/", "scripts/", ".github/", "cars/", "helpers/", "apk/",
                        "android/adbdiag/", "server/")
NON_RUNTIME_FILES = {".gitignore", "android/.gitignore", "requirements-dev.txt"}
TEXT_DOC_SUFFIXES = (".md",)


class PublishError(Exception):
    pass


# --- git ---------------------------------------------------------------------------------------------------------


def git(*args: str, binary: bool = False):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True)
    if result.returncode != 0:
        raise PublishError(f"git {' '.join(args)}: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout if binary else result.stdout.decode("utf-8")


def latest_tag(ref: str) -> str:
    return git("describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", ref).strip()


def changed_paths(tag: str, ref: str) -> list:
    return [p for p in git("diff", "--name-only", "-z", tag, ref).split("\0") if p]


def classify(paths) -> dict:
    """Изменения после выпуска: интерфейс каждой платформы и то, что требует новой сборки этой платформы."""
    out = {"desktop_ui": [], "android_ui": [], "desktop_native": [], "android_native": []}
    for path in paths:
        if path.startswith(UI_DIRS["desktop"]):
            out["desktop_ui"].append(path)
        elif path.startswith(UI_DIRS["android"]):
            out["android_ui"].append(path)
        elif (path.startswith(NON_RUNTIME_PREFIXES) or path in NON_RUNTIME_FILES
              or path.lower().endswith(TEXT_DOC_SUFFIXES)):
            continue
        elif path.startswith("android/"):
            out["android_native"].append(path)
        else:
            out["desktop_native"].append(path)
    return out


def tree_files(ref: str, prefix: str) -> dict:
    """Все файлы папки интерфейса в git на ref: путь от папки → содержимое."""
    # Без перевода строк под Windows (core.autocrlf): один и тот же интерфейс с Mac и ПК — один и тот же архив.
    data = git("-c", "core.autocrlf=false", "archive", "--format=tar", ref, prefix.rstrip("/"), binary=True)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            if member.isfile() and member.name.startswith(prefix):
                files[member.name[len(prefix):]] = archive.extractfile(member).read()
    return files


def tag_constant(tag: str, path: str, pattern: str):
    try:
        source = git("show", f"{tag}:{path}")
    except PublishError:
        return None
    match = re.search(pattern, source)
    return match.group(1) if match else None


# --- бандл -------------------------------------------------------------------------------------------------------


def select_files(files: dict, changed: set) -> dict:
    """Что положить в бандл: все текстовые файлы интерфейса + изменённые после выпуска. Изменённый файл чужого типа
    программа не примет (ui_bundle.ALLOWED_SUFFIXES) — ошибка; неизменённые такие просто не кладём."""
    chosen = {}
    for name, data in sorted(files.items()):
        suffix = PurePosixPath(name).suffix.lower()
        if suffix not in ui_bundle.ALLOWED_SUFFIXES:
            if name in changed:
                raise PublishError(f"{name}: такой тип файла программа из бандла не примет")
            continue
        if suffix in ALWAYS_SUFFIXES or name in changed:
            if len(data) > ui_bundle.MAX_FILE_BYTES:
                raise PublishError(f"{name}: больше {ui_bundle.MAX_FILE_BYTES // (1024 * 1024)} МБ")
            chosen[name] = data
    return chosen


def build_zip(files: dict) -> bytes:
    """Одинаковое содержимое — байт-в-байт одинаковый архив (повторный запуск не плодит выпуски)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, files[name], compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    data = buf.getvalue()
    if len(data) > ui_bundle.MAX_BUNDLE_BYTES:
        raise PublishError(f"бандл {len(data)} байт — больше предела программы")
    return data


def load_secret() -> bytes:
    try:
        secret = bytes.fromhex(KEY_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        raise PublishError(f"нет ключа подписи {KEY_FILE} ({exc}) — он только на компьютерах разработчика") from exc
    if len(secret) != 32:
        raise PublishError(f"{KEY_FILE}: ожидался 32-байтовый ключ Ed25519 (64 hex-символа)")
    return secret


def make_entry(platform: str, version: str, rev: int, data: bytes, secret: bytes) -> dict:
    sha = hashlib.sha256(data).hexdigest()
    sig = ed25519.sign(secret, ui_bundle.signed_message(platform, version, rev, sha)).hex()
    return {"rev": rev, "file": f"{platform}-{version}-r{rev}.zip", "size": len(data), "sha256": sha, "sig": sig}


def last_rev(entry) -> int:
    if not isinstance(entry, dict):
        return 0
    values = [entry.get("rev"), entry.get("last_rev")]
    return max([v for v in values if isinstance(v, int) and not isinstance(v, bool)] or [0])


def self_check(platform: str, version: str, entry: dict, data: bytes, public: bytes) -> None:
    """Тот же разбор, что сделает программа: подпись открытым ключом ВЫПУСКА, безопасные пути, index.html."""
    saved = ui_bundle.PUBLIC_KEY
    ui_bundle.PUBLIC_KEY = public
    try:
        problem = ui_bundle.verify_entry(entry, platform, version, data)
    finally:
        ui_bundle.PUBLIC_KEY = saved
    if problem:
        raise PublishError(f"самопроверка: {problem}")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = {info.filename for info in ui_bundle._safe_members(archive)}
    if not ui_bundle._valid_entry(entry):
        raise PublishError("самопроверка: запись манифеста не годится")
    if "index.html" not in names:
        raise PublishError("самопроверка: в бандле нет index.html")


# --- сервер ------------------------------------------------------------------------------------------------------


def ssh(command: str, stdin: bytes = b"") -> bytes:
    exe = shutil.which("ssh") or "ssh"
    result = subprocess.run([exe, "-i", str(SSH_KEY), "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", HOST,
                             command], input=stdin, capture_output=True)
    if result.returncode != 0:
        raise PublishError(f"ssh: {result.stderr.decode('utf-8', 'replace').strip() or result.returncode}")
    return result.stdout


def fetch_manifest() -> tuple:
    """Манифест с сервера и его sha256 (пустая строка — файла ещё нет)."""
    raw = ssh(f"cat {REMOTE_DIR}/manifest.json 2>/dev/null || true")
    if not raw:
        return {"schema": ui_bundle.SCHEMA}, ""
    manifest = json.loads(raw.decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != ui_bundle.SCHEMA:
        raise PublishError("на сервере ui/manifest.json неизвестного формата — разберитесь вручную")
    return manifest, hashlib.sha256(raw).hexdigest()


def upload_file(name: str, data: bytes) -> None:
    tmp = f"/tmp/.magicsqd-ui-{os.getpid()}-{name}"
    ssh(f"set -e; install -d -o magicsqd -g magicsqd -m 755 {REMOTE_DIR}; cat > {tmp}; "
        f"install -o magicsqd -g magicsqd -m 644 {tmp} {REMOTE_DIR}/{name}; rm -f {tmp}", data)


def write_manifest(manifest: dict, expected_sha: str) -> None:
    """Атомарно и только если манифест на сервере не поменялся с момента чтения (параллельная публикация)."""
    body = (json.dumps(manifest, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    ssh("set -e; d=" + REMOTE_DIR + "; install -d -o magicsqd -g magicsqd -m 755 $d; "
        "cur=$([ -s $d/manifest.json ] && sha256sum $d/manifest.json | cut -d' ' -f1 || true); "
        f"[ \"$cur\" = \"{expected_sha}\" ] || {{ echo 'ui/manifest.json изменился — запустите снова' >&2; exit 3; }}; "
        "cat > $d/.manifest.json.new; chown magicsqd:magicsqd $d/.manifest.json.new; chmod 644 $d/.manifest.json.new; "
        "mv -f $d/.manifest.json.new $d/manifest.json", body)


def remove_old_zips(manifest: dict) -> None:
    """Архивы, на которые манифест больше не ссылается (кроме предыдущего выпуска — его могут докачивать)."""
    keep = set()
    for platform in ui_bundle.PLATFORMS:
        for version, entry in (manifest.get(platform) or {}).items():
            rev = last_rev(entry)
            for r in (rev, rev - 1):
                if r > 0:
                    keep.add(f"{platform}-{version}-r{r}.zip")
    listing = ssh(f"ls -1 {REMOTE_DIR} 2>/dev/null || true").decode("utf-8").split()
    stale = [n for n in listing if n.endswith(".zip") and re.fullmatch(r"[A-Za-z0-9._-]+", n) and n not in keep]
    if stale:
        ssh("cd " + REMOTE_DIR + " && rm -f " + " ".join(stale))
        print(f"  убраны старые архивы: {', '.join(stale)}")


def verify_public(platform: str, version: str, public: bytes) -> str:
    """Как увидит программа: манифест и архив по HTTPS, проверка открытым ключом выпуска."""
    with urllib.request.urlopen(f"{PUBLIC_URL}/{ui_bundle.MANIFEST_PATH}", timeout=30) as resp:
        manifest = json.loads(resp.read().decode("utf-8"))
    entry = ui_bundle._entry(manifest, platform, version)
    if entry is None or entry.get("rev") == 0:
        return "выключен"
    with urllib.request.urlopen(f"{PUBLIC_URL}/ui/{entry['file']}", timeout=60) as resp:
        data = resp.read()
    saved = ui_bundle.PUBLIC_KEY
    ui_bundle.PUBLIC_KEY = public
    try:
        problem = ui_bundle.verify_entry(entry, platform, version, data)
    finally:
        ui_bundle.PUBLIC_KEY = saved
    return problem or f"выпуск r{entry['rev']} проверен по HTTPS"


# --- main --------------------------------------------------------------------------------------------------------


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", help="версия программы (по умолчанию — последний тег vX от --ref)")
    parser.add_argument("--ref", default="HEAD", help="откуда брать интерфейс (коммит/ветка), по умолчанию HEAD")
    parser.add_argument("--platform", choices=["desktop", "android", "both"], default="both")
    parser.add_argument("--yes", action="store_true", help="действительно выложить (без него — только показать)")
    parser.add_argument("--force", action="store_true",
                        help="выложить, хотя после выпуска менялось не только интерфейс (на свой страх)")
    parser.add_argument("--disable", action="store_true", help="выключить бандл: со следующего запуска встроенный")
    parser.add_argument("--status", action="store_true", help="показать, что сейчас на сервере")
    args = parser.parse_args(argv)

    if args.status:
        manifest, _ = fetch_manifest()
        print(json.dumps(manifest, ensure_ascii=False, indent=1))
        return 0

    tag = f"v{args.version}" if args.version else latest_tag(args.ref)
    version = tag[1:]
    platforms = ["desktop", "android"] if args.platform == "both" else [args.platform]
    versions = {
        "desktop": tag_constant(tag, "app/version.py", r'APP_VERSION\s*=\s*"([^"]+)"'),
        "android": tag_constant(tag, "android/app/build.gradle.kts", r'versionName\s*=\s*"([^"]+)"'),
    }
    public_hex = tag_constant(tag, "app/ui_bundle.py", r'PUBLIC_KEY\s*=\s*bytes\.fromhex\("([0-9a-f]{64})"\)')
    if public_hex is None:
        raise PublishError(f"в выпуске {tag} нет интерфейса с сервера (app/ui_bundle.py) — бандл он не скачает")
    public = bytes.fromhex(public_hex)
    for platform in platforms:
        if versions[platform] != version:
            raise PublishError(f"{platform}: версия в {tag} — {versions[platform]!r}, а не {version!r}")

    manifest, manifest_sha = fetch_manifest()
    if args.disable:
        for platform in platforms:
            entry = (manifest.get(platform) or {}).get(version)
            print(f"{platform} {version}: сейчас r{last_rev(entry)} {'(выключен)' if not entry or entry.get('rev') == 0 else ''}")
            manifest.setdefault(platform, {})[version] = {"rev": 0, "last_rev": last_rev(entry)}
        if not args.yes:
            print("\nЧтобы выключить, добавьте --yes.")
            return 0
        write_manifest(manifest, manifest_sha)
        print("Выключено: со следующего запуска программы — встроенный интерфейс.")
        return 0

    changes = classify(changed_paths(tag, args.ref))
    status = git("status", "--porcelain", "--", *[UI_DIRS[p] for p in platforms])
    if status.strip():
        print("ВНИМАНИЕ: в интерфейсе есть незакоммиченные правки — в бандл они НЕ войдут (берётся git, --ref).")
    secret = load_secret() if args.yes else None
    if secret is not None and ed25519.public_key(secret) != public:
        raise PublishError(f"ключ {KEY_FILE} не подходит к открытому ключу в выпуске {tag}")

    planned = []
    for platform in platforms:
        ui_changed, native = changes[f"{platform}_ui"], changes[f"{platform}_native"]
        print(f"\n== {platform}, программа {version} ({tag}..{args.ref})")
        if not ui_changed:
            print("  интерфейс не менялся после выпуска — выкладывать нечего")
            continue
        print(f"  изменено в интерфейсе: {len(ui_changed)} файл(ов)")
        for path in ui_changed[:15]:
            print(f"    {path}")
        if native:
            print(f"  после выпуска менялось и НЕ только интерфейс ({len(native)}):")
            for path in native[:15]:
                print(f"    {path}")
            if not args.force:
                print("  → программа этой версии может не знать новых методов. Сделайте ветку от тега "
                      f"(git switch -c ui/{version} {tag}), перенесите туда правку интерфейса и --ref ui/{version}. "
                      "Или --force, если уверены.")
                continue
            print("  → --force: выкладываю всё равно")
        prefix = UI_DIRS[platform]
        files = select_files(tree_files(args.ref, prefix), {p[len(prefix):] for p in ui_changed})
        data = build_zip(files)
        current = (manifest.get(platform) or {}).get(version)
        if isinstance(current, dict) and current.get("rev") and current.get("sha256") == hashlib.sha256(data).hexdigest():
            print(f"  такой бандл уже выложен (r{current['rev']}) — пропускаю")
            continue
        rev = last_rev(current) + 1
        print(f"  бандл r{rev}: {len(files)} файл(ов), {len(data) // 1024} КБ")
        planned.append((platform, rev, data))

    if not planned:
        print("\nНечего выкладывать.")
        return 1 if any(changes[f"{p}_native"] for p in platforms) and not args.force else 0
    if not args.yes:
        print("\nЧтобы выложить, добавьте --yes.")
        return 0
    for platform, rev, data in planned:
        entry = make_entry(platform, version, rev, data, secret)
        self_check(platform, version, entry, data, public)
        upload_file(entry["file"], data)
        manifest.setdefault(platform, {})[version] = entry
    write_manifest(manifest, manifest_sha)
    remove_old_zips(manifest)
    for platform, rev, _ in planned:
        print(f"{platform}: {verify_public(platform, version, public)}")
    print("Готово: программы скачают бандл при запуске и покажут его со следующего запуска.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PublishError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(2)
