#!/usr/bin/env python3
"""Подписать и выложить общий Python-код каталога cars/_shared/*.py (app/code_signing.py; владелец, 2026-10-06:
«чтобы обновлений приложения стало поменьше»).

ПК исполняет эти модули как есть. Android (py_runner.py, команда «#py модуль.функция») — только с подписью
разработчика: рядом с модулем на сервере должен лежать <модуль>.py.sig. Скрипт сверяет cars/_shared/*.py из рабочей
папки с сервером, подписывает ключом разработчика (~/.magicsqd/ui_signing_ed25519.key — тот же, что у бандлов
интерфейса; на сервере его нет) и выкладывает изменённые модули и подписи, потом пересобирает манифест каталога.

  python scripts/publish_shared.py                       # что отличается от сервера — ничего не меняет
  python scripts/publish_shared.py --yes                 # выложить изменённые модули и подписи
  python scripts/publish_shared.py --yes --only adb_permissions.py
  python scripts/publish_shared.py --yes --keep-server-code   # только подписи к тому, что УЖЕ на сервере

Нужен ssh-ключ ~/.ssh/magicsqd_deploy (как у server/deploy.sh)."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import app_token, code_signing, ed25519  # noqa: E402

HOST = "root@94.102.89.93"
SSH_KEY = Path.home() / ".ssh" / "magicsqd_deploy"
REMOTE_SHARED = "/opt/magicsqd/content/cars/_shared"
PUBLIC_URL = "https://magicsqd.ru/content/cars/_shared"
LOCAL_SHARED = ROOT / "cars" / "_shared"
KEY_FILE = Path(os.environ.get("MAGICSQD_UI_KEY_FILE") or Path.home() / ".magicsqd" / "ui_signing_ed25519.key")


class PublishError(Exception):
    pass


def ssh(command: str, stdin: bytes = b"") -> bytes:
    import shutil
    exe = shutil.which("ssh") or "ssh"
    result = subprocess.run([exe, "-i", str(SSH_KEY), "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", HOST, command],
                            input=stdin, capture_output=True)
    if result.returncode != 0:
        raise PublishError(f"ssh: {result.stderr.decode('utf-8', 'replace').strip() or result.returncode}")
    return result.stdout


def remote_hashes() -> dict:
    out = ssh(f"cd {REMOTE_SHARED} && for f in *.py *.py.sig; do [ -f \"$f\" ] && sha256sum \"$f\"; done; true")
    hashes = {}
    for line in out.decode("utf-8", "replace").splitlines():
        digest, _, name = line.partition("  ")
        if digest and name:
            hashes[name.strip()] = digest.strip()
    return hashes


def remote_file(name: str) -> bytes:
    return ssh(f"cat {REMOTE_SHARED}/{shlex.quote(name)}")


def upload(name: str, data: bytes) -> None:
    tmp = f"/tmp/.magicsqd-shared-{os.getpid()}-{name}"
    ssh(f"set -e; cat > {shlex.quote(tmp)}; install -o magicsqd -g magicsqd -m 644 {shlex.quote(tmp)} "
        f"{REMOTE_SHARED}/{shlex.quote(name)}; rm -f {shlex.quote(tmp)}", data)


def load_secret() -> bytes:
    try:
        secret = bytes.fromhex(KEY_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        raise PublishError(f"нет ключа подписи {KEY_FILE} ({exc}) — он только на компьютерах разработчика") from exc
    if len(secret) != 32 or ed25519.public_key(secret) != code_signing.PUBLIC_KEY:
        raise PublishError(f"ключ {KEY_FILE} не подходит к открытому ключу программы")
    return secret


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def plan(local: dict, remote: dict, secret: bytes | None, keep_server_code: bool, fetch=remote_file) -> list:
    """[(имя файла, байты, почему)] — что выложить. Подпись детерминированная (Ed25519): совпала — не трогаем."""
    actions = []
    for name, data in sorted(local.items()):
        code = data
        if keep_server_code:
            if name not in remote:
                print(f"  {name}: на сервере нет — пропускаю (--keep-server-code)")
                continue
            if remote[name] != sha(data):
                code = fetch(name)
                print(f"  {name}: подписываю версию с сервера (в рабочей папке другая)")
        elif remote.get(name) != sha(data):
            actions.append((name, data, "новый модуль" if name not in remote else "модуль изменён"))
        if secret is None:
            continue
        sig = code_signing.sign(secret, name, code).encode("ascii")
        if remote.get(f"{name}{code_signing.SIG_SUFFIX}") != sha(sig):
            actions.append((f"{name}{code_signing.SIG_SUFFIX}", sig, "подпись"))
    return actions


def show_diff(name: str, local: bytes) -> None:
    try:
        server = remote_file(name).decode("utf-8", "replace").splitlines()
    except PublishError:
        return
    diff = list(difflib.unified_diff(server, local.decode("utf-8", "replace").splitlines(), "сервер", "локально",
                                     lineterm="", n=1))
    for line in diff[:40]:
        print(f"    {line}")
    if len(diff) > 40:
        print(f"    … ещё {len(diff) - 40} строк")


def official_headers() -> dict:
    """Токен официальной сборки, как у программы: с 08.10.2026 каталог закрыт — /content без токена отвечает 403
    (scripts/publish_ui.py — то же). Секрет — app_build_secret из server.json рядом с программой; нет его — {}."""
    try:
        secret = json.loads((ROOT / "server.json").read_text(encoding="utf-8")).get("app_build_secret")
    except (OSError, ValueError):
        return {}
    secret = secret.strip().lower() if isinstance(secret, str) else ""
    if not re.fullmatch(r"[0-9a-f]{64}", secret):
        return {}
    return app_token.AppToken(PUBLIC_URL, bytes.fromhex(secret), "publish-shared").header()


def verify_public(name: str) -> str:
    """Как увидит программа: модуль и подпись по HTTPS с токеном сборки."""
    headers = official_headers()
    with urllib.request.urlopen(urllib.request.Request(f"{PUBLIC_URL}/{name}", headers=headers), timeout=30) as resp:
        data = resp.read()
    with urllib.request.urlopen(urllib.request.Request(f"{PUBLIC_URL}/{name}{code_signing.SIG_SUFFIX}",
                                                       headers=headers), timeout=30) as resp:
        sig = resp.read().decode("ascii", "replace")
    return "подпись верна (HTTPS)" if code_signing.verify(name, data, sig) else "ПОДПИСЬ НЕ СХОДИТСЯ"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--yes", action="store_true", help="действительно выложить (без него — только показать)")
    parser.add_argument("--only", action="append", default=[], help="только эти файлы (можно несколько раз)")
    parser.add_argument("--keep-server-code", action="store_true",
                        help="модули не менять — только подписать то, что уже лежит на сервере")
    args = parser.parse_args(argv)

    local = {p.name: p.read_bytes() for p in sorted(LOCAL_SHARED.glob("*.py"))}
    if args.only:
        missing = [n for n in args.only if n not in local]
        if missing:
            raise PublishError(f"нет в cars/_shared: {', '.join(missing)}")
        local = {n: local[n] for n in args.only}
    remote = remote_hashes()
    for name in sorted(n for n in remote if n.endswith(".py") and n not in local and not args.only):
        print(f"  на сервере есть {name}, которого нет в рабочей папке — не трогаю (и не подписываю)")
    secret = load_secret() if args.yes else None
    if secret is None:
        # Без ключа — только сверка модулей; подписи посчитаются при --yes.
        for name, data in sorted(local.items()):
            state = "совпадает" if remote.get(name) == sha(data) else ("новый" if name not in remote else "ИЗМЕНЁН")
            signed = "есть подпись" if f"{name}{code_signing.SIG_SUFFIX}" in remote else "без подписи"
            print(f"  {name}: {state}, на сервере {signed}")
            if state == "ИЗМЕНЁН" and not args.keep_server_code:
                show_diff(name, data)
        print("\nЧтобы подписать и выложить, добавьте --yes.")
        return 0
    actions = plan(local, remote, secret, args.keep_server_code)
    if not actions:
        print("Всё уже на сервере и подписано.")
        return 0
    for name, data, why in actions:
        print(f"  выкладываю {name} ({why}, {len(data)} байт)")
        upload(name, data)
    ssh("cd /opt/magicsqd && sudo -u magicsqd python3 backend.py --rebuild-manifest >/dev/null")
    print("Манифест каталога пересобран.")
    for name in sorted({n[:-len(code_signing.SIG_SUFFIX)] if n.endswith(code_signing.SIG_SUFFIX) else n
                        for n, _, _ in actions}):
        print(f"  {name}: {verify_public(name)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PublishError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        sys.exit(2)
