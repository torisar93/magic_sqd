"""Политика shell-команд ИИ-мастера на стороне программы — вторая линия защиты после сервера (server/ai_master.py).

Сервер решает, выполнит ли программа команду сама (только читает) или покажет карточку «Выполнить»; программа перед
выполнением проверяет ещё раз тем же списком: «сама» — только если команда и здесь только читает, команды adb уровня
компьютера и разрушительные — никогда, даже после «Выполнить».

POLICY — копия server/ai_shell_policy.json без поля about (tests/test_ai_policy.py сверяет). Файл ОДИНАКОВЫЙ на ПК
(app/ai_policy.py) и Android (android/app/src/main/python/ai_policy.py). Только стандартная библиотека."""
from __future__ import annotations

import re

POLICY = {
 "version": 1,
 "pc_commands": [
  "adb",
  "kill-server",
  "start-server",
  "disconnect",
  "connect",
  "reconnect",
  "tcpip",
  "usb",
  "root",
  "unroot",
  "remount",
  "sideload",
  "pair",
  "forward",
  "reverse",
  "install",
  "install-multiple",
  "uninstall",
  "push",
  "pull",
  "wait-for-device",
  "devices",
  "get-state",
  "get-serialno",
  "reboot-bootloader"
 ],
 "forbidden": [
  "\\brm\\s+(-[a-zA-Z]*\\s+)*/(\\s|\\*|$)",
  "\\brm\\s+(-[a-zA-Z]*\\s+)*/(system|vendor|product|data|sdcard|storage)/?(\\s|\\*|$)",
  "\\bmkfs",
  "\\bdd\\s",
  "\\bflash_image\\b",
  "\\bwipe\\b",
  "\\breboot\\s+(recovery|bootloader|edl|fastboot)",
  "\\bsetprop\\s+(persist\\.)?sys\\.usb",
  "\\bsvc\\s+usb\\b",
  "\\bpm\\s+(clear|uninstall)\\s+(android|com\\.android\\.(settings|systemui|phone|shell|providers\\.\\w+))\\b"
 ],
 "readonly": [
  "^getprop( [\\w.\\-]+)?$",
  "^(id|whoami|date|uptime|getenforce)$",
  "^uname( -[a-z]+)?$",
  "^free( -[hmkb])?$",
  "^df( -[hk])?( \\S+)*$",
  "^ps( -[A-Za-z]+)*( \\S+)*$",
  "^(ls|cat|head|tail|wc|stat|md5sum|sha1sum|sha256sum|du|file|readlink|which|echo|grep|egrep|sort|uniq|cut|tr)( \\S+)*$",
  "^find( \\S+)*$",
  "^pm (list|path)( \\S+)*$",
  "^cmd package (list|resolve-activity|query-activities|dump)( \\S+)*$",
  "^dumpsys( -l)?$",
  "^dumpsys [\\w.]+( (-a|-c|-h|--checkin|windows|displays|tokens|policy|activities|services|providers|recents|processes|broadcasts|packages|permissions|top|lastanr|[a-z][\\w]*(\\.[\\w]+)+))*$",
  "^settings (get|list) (system|secure|global)( [\\w.\\-]+)?$",
  "^(appops|cmd appops) get( \\S+)*$",
  "^wm (size|density)$",
  "^service (list|check( [\\w.\\-]+)?)$",
  "^ip( -[\\w]+)* (a|addr|address|r|route|l|link|n|neigh)( show( dev [\\w.\\-]+)?)?$",
  "^ifconfig( [\\w.\\-]+)?$",
  "^netstat( -[A-Za-z]+)*$",
  "^ping -c [1-9]( -W [0-9]+)? [\\w.:\\-]+$",
  "^logcat -d( -[A-Za-z]+( [\\w:*.\\-]+)?)*$",
  "^top -n ?1( -b)?( -m [0-9]+)?$",
  "^cmd (wifi|connectivity) status$"
 ],
 "forbidden_tokens": {
  "find": [
   "-delete",
   "-exec",
   "-execdir",
   "-ok",
   "-okdir",
   "-fprint",
   "-fprint0",
   "-fprintf",
   "-fls"
  ],
  "logcat": [
   "-c",
   "--clear",
   "-G",
   "-P",
   "--prune",
   "-f",
   "--file"
  ]
 },
 "write_chars": [
  ">",
  "<",
  "`",
  "$(",
  "${"
 ]
}

_READONLY = [re.compile(p) for p in POLICY["readonly"]]
_FORBIDDEN = [re.compile(p) for p in POLICY["forbidden"]]
_PART_SPLIT = re.compile(r"\s*(?:\|\||&&|;|\|)\s*")


def normalize(cmd: str) -> str:
    """Пробелы схлопнуты, «shell » в начале снят — то, что уйдёт в shell магнитолы."""
    text = " ".join(str(cmd or "").split())
    if text.startswith("shell "):
        text = text[len("shell "):]
    return text


def classify_command(cmd: str) -> tuple[str, str]:
    """("readonly"|"confirm"|"forbidden", пояснение) — так же, как server/ai_master.classify_command."""
    text = " ".join(str(cmd or "").split())
    if not text:
        return "forbidden", "пустая команда"
    words = text.split(" ")
    if words[0] == "shell" and len(words) > 1:
        text = " ".join(words[1:])
        words = words[1:]
    if words[0] in POLICY["pc_commands"]:
        return "forbidden", f"«{words[0]}» — команда adb на компьютере, ИИ их не выполняет"
    for pattern in _FORBIDDEN:
        if pattern.search(text):
            return "forbidden", "команда может сломать магнитолу"
    if any(chars in text for chars in POLICY["write_chars"]):
        return "confirm", "пишет в файл или подставляет вывод"
    for part in _PART_SPLIT.split(text):
        if not part:
            return "confirm", "непонятная команда"
        tokens = part.split(" ")
        banned = POLICY["forbidden_tokens"].get(tokens[0], [])
        if any(token in banned for token in tokens[1:]):
            return "confirm", f"«{tokens[0]}» с таким ключом меняет магнитолу"
        if not any(pattern.match(part) for pattern in _READONLY):
            return "confirm", "команда меняет магнитолу или неизвестна"
    return "readonly", "только читает"


def may_run(cmd: str, mode: str, busy: bool) -> tuple[bool, str]:
    """Можно ли программе выполнить команду ИИ. mode — "auto" (сама) или "confirm" (техник нажал «Выполнить")."""
    if busy:
        return False, "идёт установка — команды ИИ сейчас не выполняются"
    kind, why = classify_command(cmd)
    if kind == "forbidden":
        return False, why
    if mode == "auto" and kind != "readonly":
        return False, "программа выполняет сама только читающие команды — нужна карточка «Выполнить»"
    return True, ""
