#!/usr/bin/env python3
"""Закрытый каталог на сервере: включить, выключить, посмотреть (владелец, 2026-10-08: «можем включать защиту»).

    python3 scripts/catalog_protection.py status   — включена ли проверка и как клиенты получают токены сегодня
    python3 scripts/catalog_protection.py on       — включить
    python3 scripts/catalog_protection.py off      — выключить (откат за секунды)

Включение — это строка APP_BUILD_SECRET=<секрет> в /opt/magicsqd/backend.env и перезапуск бэкенда: с ней /content
отдаётся только программе с токеном официальной сборки (server/backend.py: /auth/app-token, /auth/app_check).
Секрет — `app_build_secret` из server.json рядом с программой, тот же, что вшит в сборки (scripts/push_ci_secrets.sh).
Скрипт его не печатает и не кладёт в командную строку: на сервер он идёт через stdin ssh.

`on` по шагам, при любом сбое после включения — сразу выключает обратно:
1. секрет из server.json совпадает с вшитым в выпущенную Android-сборку на сайте (/download) — иначе не включает;
2. копия backend.env → /opt/magicsqd/backups/backend-env-<время>/, строка, перезапуск;
3. проверка как программа: токен выдаётся, каталог с токеном — 200, без токена — 403, значки и обновления открыты;
4. до 2 минут смотрит журнал nginx: настоящие клиенты получают токены (200), а не отказ (403).
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import app_token  # noqa: E402 — то же доказательство, что у программы

HOST = "root@94.102.89.93"
SITE = "https://magicsqd.ru"

# Выполняется на сервере (python3 -c): режим — argv[1], секрет (для on/check-apk) — stdin.
REMOTE = r'''
import datetime, json, os, re, shutil, subprocess, sys, time, urllib.error, urllib.request, zipfile
mode = sys.argv[1]
ENV = "/opt/magicsqd/backend.env"
APK = "/opt/magicsqd/site/download/MagicSQD_Android.apk"
LOG = "/var/log/nginx/access.log"
LINE = re.compile(r'^(\S+) \S+ \S+ \[([^\]]+)\] "POST /auth/app-token[^"]*" (\d{3}) ')

def app_check():
    for _ in range(60):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8787/auth/app_check", timeout=3) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            time.sleep(0.5)
    return None

def token_statuses(pos, since):
    """Ответы /auth/app-token в журнале nginx с позиции pos (не раньше since), отдельно для чужих адресов и для
    адреса, с которого запущен скрипт (там и его самопроверка, и программы этой же сети):
    {"others"|"mine": {код: (запросов, адресов)}}."""
    me = os.environ.get("SSH_CLIENT", "").split(" ")[0]
    stats = {"others": {}, "mine": {}}
    try:
        size = os.path.getsize(LOG)
    except OSError:
        return stats
    with open(LOG, "rb") as f:
        f.seek(pos if pos <= size else 0)
        for raw in f:
            m = LINE.match(raw.decode("utf-8", "replace"))
            if not m:
                continue
            when = datetime.datetime.strptime(m.group(2), "%d/%b/%Y:%H:%M:%S %z").timestamp()
            if when < since:
                continue
            side = stats["mine" if m.group(1) == me else "others"]
            n, ips = side.get(m.group(3), (0, set()))
            ips.add(m.group(1))
            side[m.group(3)] = (n + 1, ips)
    return {who: {code: (n, len(ips)) for code, (n, ips) in side.items()} for who, side in stats.items()}

if mode == "check-apk":
    secret = sys.stdin.read().strip().lower().encode("ascii")
    if not os.path.isfile(APK):
        print("APK_MISSING"); sys.exit(0)
    with zipfile.ZipFile(APK) as z:
        dex = [n for n in z.namelist() if re.fullmatch(r"classes\d*\.dex", n)]
        print("APK_MATCH" if any(secret in z.read(n) for n in dex) else "APK_MISMATCH")
    sys.exit(0)

if mode == "status":
    values = [l.split("=", 1)[1].strip() for l in open(ENV, encoding="utf-8").read().splitlines()
              if l.startswith("APP_BUILD_SECRET=")]
    day = datetime.datetime.now(datetime.timezone.utc).replace(hour=0, minute=0, second=0).timestamp()
    print("STATUS", "set" if any(values) else "empty", app_check(), json.dumps(token_statuses(0, day)))
    sys.exit(0)

if mode == "watch":
    pos, since, limit = int(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
    deadline = time.time() + limit
    while True:
        stats = token_statuses(pos, since)
        ok, bad = stats["others"].get("200", (0, 0)), stats["others"].get("403", (0, 0))
        if ok[1] >= 3 or bad[0] >= 5 or time.time() >= deadline:
            print("WATCH", json.dumps(stats)); sys.exit(0)
        time.sleep(5)

# on / off
secret = sys.stdin.read().strip().lower() if mode == "on" else ""
if mode == "on" and not re.fullmatch(r"[0-9a-f]{64}", secret):
    print("BAD_SECRET"); sys.exit(2)
st = os.stat(ENV)
backup = "/opt/magicsqd/backups/backend-env-" + time.strftime("%Y%m%d_%H%M%S")
os.makedirs(backup, mode=0o700)
shutil.copy2(ENV, os.path.join(backup, "backend.env"))
lines = [l for l in open(ENV, encoding="utf-8").read().splitlines() if not l.startswith("APP_BUILD_SECRET=")]
if mode == "on":
    lines.append("APP_BUILD_SECRET=" + secret)
tmp = ENV + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
os.chown(tmp, st.st_uid, st.st_gid)
os.chmod(tmp, st.st_mode & 0o7777)
os.replace(tmp, ENV)
pos = os.path.getsize(LOG) if os.path.exists(LOG) else 0
started = time.time()
subprocess.run(["systemctl", "restart", "magicsqd-backend"], check=True)
print("DONE", backup, pos, started, app_check())
'''


def remote(mode: str, *args: str, secret: str = "", key: Path) -> str:
    cmd = ["ssh", "-i", str(key), "-o", "BatchMode=yes", HOST,
           " ".join(["python3", "-c", shlex.quote(REMOTE), mode, *map(shlex.quote, args)])]
    done = subprocess.run(cmd, input=secret, capture_output=True, text=True, timeout=300)
    if done.returncode != 0:
        raise RuntimeError(f"сервер ({mode}): {(done.stderr or done.stdout).strip()[-500:]}")
    return done.stdout.strip()


def http(url: str, body: bytes | None = None, headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def read_secret() -> str:
    path = ROOT / "server.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("app_build_secret")
    except (OSError, ValueError) as exc:
        sys.exit(f"Не прочитать {path}: {exc}")
    value = (value or "").strip().lower() if isinstance(value, str) else ""
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        sys.exit(f"В {path} нет app_build_secret из 64 hex-знаков — включать нечем.")
    return value


def self_check(secret: str) -> list[str]:
    """Проверка снаружи, как программа. Возвращает список проблем (пусто — всё хорошо)."""
    problems = []
    client_id = "selfcheck-" + uuid.uuid4().hex[:16]
    status, raw = http(f"{SITE}/auth/app-token", app_token.request_body(bytes.fromhex(secret), client_id),
                       {"Content-Type": "application/json"})
    token = ""
    try:
        token = json.loads(raw.decode("utf-8")).get("token") or ""
    except ValueError:
        pass
    if status != 200 or not token:
        return [f"токен не выдан (сервер ответил {status}) — секрет на сервере не тот"]
    print("  ✓ токен выдан")
    auth = {"X-App-Token": token}
    status, raw = http(f"{SITE}/content/manifest.json", headers=auth)
    icons = []
    if status == 200:
        print("  ✓ каталог с токеном открывается (200)")
        # apk_icons: путь APK → "icons/<sha>.png" (строка; в icons/.index.json на сервере — словари с "icon")
        icons = list((json.loads(raw.decode("utf-8")).get("apk_icons") or {}).values())
    else:
        problems.append(f"каталог с токеном не открылся ({status})")
    status, _ = http(f"{SITE}/content/manifest.json")
    if status == 403:
        print("  ✓ без токена каталог закрыт (403)")
    else:
        problems.append(f"без токена каталог отвечает {status}, а должен 403")
    icon = next((v if isinstance(v, str) else v.get("icon") for v in icons
                 if (isinstance(v, str) and v) or (isinstance(v, dict) and v.get("icon"))), None)
    if icon:
        status, _ = http(f"{SITE}/content/{icon}")
        print(f"  {'✓' if status == 200 else '✗'} значки приложений открыты ({status})")
        if status != 200:
            problems.append(f"значки приложений отвечают {status}")
    status, _ = http(f"{SITE}/download/version.json")
    print(f"  {'✓' if status == 200 else '✗'} обновления программы открыты ({status})")
    if status != 200:
        problems.append(f"/download/version.json отвечает {status}")
    return problems


def describe(stats: dict) -> str:
    if not stats:
        return "запросов не было"
    names = {"200": "выдан", "401": "часы", "403": "отказ", "503": "проверка выключена"}
    return ", ".join(f"{names.get(code, code)} — {n} (устройств {ips})" for code, (n, ips) in sorted(stats.items()))


def cmd_status(key: Path) -> None:
    out = remote("status", key=key).split(" ", 3)
    state, check, stats = out[1], out[2], json.loads(out[3])
    on = state == "set"
    print(f"Проверка сборки: {'ВКЛЮЧЕНА' if on else 'выключена'} (app_check без токена → {check}).")
    print(f"Токены сегодня — другие устройства: {describe(stats['others'])}.")
    if stats["mine"]:
        print(f"С адреса этого компьютера (его программы и проверки): {describe(stats['mine'])}.")


def cmd_off(key: Path) -> None:
    out = remote("off", key=key).split()
    print(f"Выключено. Копия backend.env: {out[1]}. app_check без токена → {out[4]} (204 — каталог снова открыт).")


def cmd_on(key: Path, watch_seconds: int) -> None:
    secret = read_secret()
    print("1. Секрет из server.json есть (не показываю).")
    apk = remote("check-apk", secret=secret, key=key)
    if apk == "APK_MISMATCH":
        sys.exit("   Он НЕ совпадает с вшитым в Android-сборку на сайте — программы его не знают. Не включаю.")
    print("   Совпадает с вшитым в Android-сборку на сайте." if apk == "APK_MATCH"
          else "   Android-сборки на сайте нет — сверить не с чем, проверю по ответам сервера.")
    out = remote("on", secret=secret, key=key).split()
    backup, pos, started, check = out[1], out[2], out[3], out[4]
    print(f"2. Включено. Копия прежнего backend.env: {backup}. app_check без токена → {check}.")
    print("3. Проверка как программа:")
    problems = self_check(secret)
    if not problems:
        print(f"4. Смотрю, получают ли токены настоящие программы (до {watch_seconds} с)…")
        stats = json.loads(remote("watch", pos, started, str(watch_seconds), key=key).split(" ", 1)[1])
        others, mine = stats["others"], stats["mine"]
        print(f"   другие устройства: {describe(others)}")
        if mine.get("200", (0, 0))[0] > 1:  # один токен — самопроверка выше, остальные — программы этой же сети
            print(f"   программы с адреса этого компьютера тоже получают токен: {describe(mine)}")
        ok, bad = others.get("200", (0, 0))[0], others.get("403", (0, 0))[0]
        if bad >= 5 and ok == 0:
            problems.append("настоящие программы получают отказ — секрет в сборках другой")
        elif ok == 0:
            print("   Других программ за это время не было — посмотрите позже: "
                  "python3 scripts/catalog_protection.py status")
    if problems:
        print("Не так: " + "; ".join(problems) + ". Выключаю обратно…")
        cmd_off(key)
        sys.exit(1)
    print("Готово: каталог закрыт, программа 1.1.0 работает. Откат: python3 scripts/catalog_protection.py off")


def main() -> None:
    parser = argparse.ArgumentParser(description="Закрытый каталог на сервере magicsqd.ru")
    parser.add_argument("mode", choices=["status", "on", "off"])
    parser.add_argument("--key", type=Path, default=Path.home() / ".ssh" / "magicsqd_deploy", help="SSH-ключ сервера")
    parser.add_argument("--watch", type=int, default=120, help="сколько секунд смотреть на настоящие программы")
    args = parser.parse_args()
    {"status": lambda: cmd_status(args.key), "off": lambda: cmd_off(args.key),
     "on": lambda: cmd_on(args.key, args.watch)}[args.mode]()


if __name__ == "__main__":
    main()
