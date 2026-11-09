"""Подробный лог для разработчика — кнопка «Собрать подробный лог» в «Дополнительных действиях» моделей, где установка
не проходит непонятно почему («#py diagnostics.collect»; владелец, 18.10.2026: «может мы можем давать в установщике
кнопку которая бы снимала дополнительные более подробные логи и отправляла нам?»). Повод — Omoda C7 с магнитолой
lajolla: все способы установки отказали с INSTALL_FAILED_INTERNAL_ERROR (логи №7400, №7405), а почему — видно только в
системном журнале магнитолы.

Почти только чтение: прошивка, место, права adb, ограничения установки, установщики и магазины производителя, службы
спецвозможностей и спецдоступы сторонних приложений, системный журнал про установку. В конце — пробная установка
крошечного тестового приложения (installtest.apk, сразу удаляется) и системный журнал ровно с этого момента: причина
отказа прошивки видна, даже если кнопку нажали не сразу (lajolla/Omoda C7, №14285: к нажатию журнал про установку уже
вытеснили, раздел был пуст; разбор 09.11.2026). Всё пишется в журнал установки, а он и так уходит на сервер — отдельно отправлять ничего не
нужно. Серийные номера, MAC-адреса, VIN и имена пользователей не собираются. Нажимать лучше сразу после неудачной
установки: тогда её причина ещё лежит в системном журнале магнитолы. Общий для ПК и Android (android/.../py_runner.py).
"""
from __future__ import annotations

import re
from pathlib import Path

HEADER = "=== Подробный лог для разработчика ==="
LINE_CHARS = 300  # длиннее — обрезаем: в системном журнале бывают строки по несколько килобайт

# getprop — прошивка и отладка (как в «О системе») плюс любые ключи производителя про установку (белые списки,
# запреты, магазин); ключи с идентификаторами устройства отбрасываем.
_PROP_KEYS = {
    "ro.product.brand", "ro.product.manufacturer", "ro.product.model", "ro.product.name", "ro.product.device",
    "ro.product.board", "ro.product.cpu.abilist", "ro.product.vendor.brand", "ro.product.vendor.manufacturer",
    "ro.product.vendor.model", "ro.product.vendor.name", "ro.product.vendor.device", "ro.board.platform",
    "ro.hardware", "ro.boot.hardware", "ro.build.fingerprint", "ro.build.display.id", "ro.build.description",
    "ro.build.version.release", "ro.build.version.sdk", "ro.build.version.incremental",
    "ro.build.version.security_patch", "ro.build.type", "ro.build.tags", "ro.build.date", "ro.vendor.build.fingerprint",
    "ro.vendor.build.date", "ro.debuggable", "ro.secure", "ro.adb.secure", "ro.crypto.state", "sys.usb.config",
    "persist.sys.usb.config", "persist.sys.locale", "persist.sys.timezone",
}
_PROP_INSTALL = re.compile(r"install|white|black|forbid|appstore|app_store|verif", re.IGNORECASE)
_PROP_DROP = re.compile(r"serial|\bmac|imei|meid|bdaddr|wifi|wlan|ssid|uuid|\bvin\b|android_id", re.IGNORECASE)
_USER_NAME = re.compile(r"(UserInfo\{\d+):[^:}]*:")
# Системный журнал: строки установщиков по их тегу (PackageManager, PackageInstallerSession, installd, теги
# производителя со словом Install…) и любые с INSTALL_FAILED / белым списком / запретом.
_INSTALL_LOG = (r" [VDIWEF]/[A-Za-z0-9_.$-]*(PackageManager|Package[A-Za-z]*Install|[Ii]nstall)[A-Za-z0-9_.$-]*\("
                r"|INSTALL_FAILED|[Ww]hite_?[Ll]ist|[Ff]orbid")

A11Y_TITLE = "Спецвозможности и спецдоступы"
# Длинные списки служб — по одной на строку: «a/b:c/d» (settings) и «{…}, {…}» (dumpsys accessibility).
_SERVICE_SPLIT = re.compile(r"(?<=\}), (?=\{)|(?<=[\w.$]):(?=[A-Za-z][\w.]*/)")

# (заголовок, команда на магнитоле, сколько строк оставить). Команды — только чтение; каждая со своим ограничением,
# чтобы весь лог был в пределах пары сотен килобайт.
SECTIONS = (
    ("Прошивка (getprop)", "getprop", 80),
    ("Время на магнитоле", "date", 2),
    ("Место на магнитоле", "df -h /data /sdcard /data/local/tmp 2>/dev/null || df /data /sdcard 2>/dev/null", 12),
    ("Права adb и SELinux", "id; getenforce 2>/dev/null", 4),
    ("Пользователи и ограничения",
     "pm list users 2>/dev/null; "
     "dumpsys user 2>/dev/null | grep -iE 'restriction|disallow|no_install|no_debugging' | head -n 30; "
     "dumpsys device_policy 2>/dev/null | grep -iE 'device owner|profile owner|admin|disallow|restrict' | head -n 30", 70),
    ("Проверка приложений при установке",
     "for k in package_verifier_enable verifier_verify_adb_installs package_verifier_user_consent "
     "install_non_market_apps; do echo \"global $k=$(settings get global $k 2>/dev/null)\"; done; "
     "echo \"secure install_non_market_apps=$(settings get secure install_non_market_apps 2>/dev/null)\"; "
     "settings list global 2>/dev/null | grep -iE 'install|verif|package' | head -n 20; "
     "settings list system 2>/dev/null | grep -iE 'install|white|black|forbid' | head -n 20", 50),
    ("Установщики и магазины на магнитоле",
     "pm list packages -s 2>/dev/null | grep -iE 'store|market|install|appmanager|appmgr|upgrade|update|white|mega' "
     "| sort | head -n 40", 40),
    ("Сторонние приложения", "pm list packages -3 2>/dev/null | sort | head -n 80", 80),
    # Выдача разрешений после установки ответы магнитолы не проверяет: включилась ли служба на самом деле (Bound —
    # запущена) и что стоит у спецдоступов, видно только здесь (Cityray, Back Button; владелец, 02.11.2026: «у
    # человека не выдаются разрешения на nu.back.button как мне кажется»).
    (A11Y_TITLE,
     "echo \"accessibility_enabled=$(settings get secure accessibility_enabled 2>/dev/null)\"; "
     "echo \"enabled_accessibility_services=$(settings get secure enabled_accessibility_services 2>/dev/null)\"; "
     "dumpsys accessibility 2>/dev/null | grep -iE 'bound services|enabled services|binding services|crashed services' "
     "| head -n 8; "
     # Окно выбора приложения (Back Button «Launch other application», ярлыки) — ActivityPicker «Настроек»; в прошивке
     # Cityray его нет, пока не поставлен Settings.apk из комплекта: без него «Can't open application chooser».
     "echo \"PICK_ACTIVITY: $(cmd package resolve-activity --brief -a android.intent.action.PICK_ACTIVITY 2>/dev/null "
     "| tail -n 1)\"; "
     "for p in $(pm list packages -3 2>/dev/null | cut -d: -f2 | sort | head -n 80); do "
     "echo \"$p: $(appops get $p SYSTEM_ALERT_WINDOW 2>/dev/null | head -n 1 | cut -d';' -f1);"
     " $(appops get $p WRITE_SETTINGS 2>/dev/null | head -n 1 | cut -d';' -f1)\"; done", 200),
    ("Системный журнал про установку",
     f"logcat -d -v time -t 4000 2>/dev/null | grep -E '{_INSTALL_LOG}' | tail -n 200", 200),
    # Отказ установщика производителя может прийти под любым тегом — последние ошибки журнала целиком.
    ("Ошибки в системном журнале", "logcat -d -v time -t 3000 '*:E' 2>/dev/null | tail -n 60", 60),
)


def _output(result) -> str:
    return ((getattr(result, "stdout", "") or "") + (getattr(result, "stderr", "") or "")).strip()


def _clean(title: str, text: str) -> list:
    """Строки раздела: без пустых, идентификаторы устройства и имена пользователей убраны."""
    lines = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        if title.startswith("Прошивка"):
            key = line.split("]", 1)[0].lstrip("[")
            if key not in _PROP_KEYS and not _PROP_INSTALL.search(key) or _PROP_DROP.search(key):
                continue
        line = _USER_NAME.sub(r"\1:…:", line)
        pieces = _SERVICE_SPLIT.split(line) if title == A11Y_TITLE else [line]
        for n, piece in enumerate(pieces):
            piece = ("    " if n else "") + piece
            lines.append(piece[:LINE_CHARS] + ("…" if len(piece) > LINE_CHARS else ""))
    return lines


PROBE_APK = "installtest.apk"
PROBE_PACKAGE = "ru.magicsqd.installtest"
PROBE_REMOTE = "/data/local/tmp/msqd_installtest.apk"
PROBE_TITLE = "Пробная установка тестового приложения"
# Журнал после пробы: установщики, исключения и строки стека — по ним видно, где прошивка отказала.
_PROBE_LOG = _INSTALL_LOG + r"|Exception|Error|^\s+at |\tat "


def _probe_apk(ctx):
    shared = getattr(ctx, "shared_dir", None)
    local = (Path(shared) if shared else Path(__file__).resolve().parent) / PROBE_APK
    return local if local.is_file() else None


def probe_install(ctx) -> list:
    """Поставить и сразу удалить installtest.apk, снять системный журнал с момента установки. Строки раздела."""
    local = _probe_apk(ctx)
    if local is None:
        return [f"(тестового {PROBE_APK} на этом устройстве нет — каталог ещё не скачан)"]
    since = _output(ctx.shell("date +'%m-%d %H:%M:%S.000'", check=False, timeout=20)).splitlines()
    since = since[-1].strip() if since else ""
    try:
        ctx.push(str(local), PROBE_REMOTE)
    except Exception as exc:  # noqa: BLE001 — разные классы на ПК и Android
        if type(exc).__name__ == "InstallCancelled":
            raise
        return [f"не записался на магнитолу: {exc}"]
    answer = _output(ctx.shell(f"pm install -r -t {PROBE_REMOTE}", check=False, timeout=120)) or "(пусто)"
    lines = [f"pm install: {line}" for line in answer.splitlines()[:5]]
    if "Success" in answer:
        ctx.shell(f"pm uninstall {PROBE_PACKAGE}", check=False, timeout=60)
        lines.append("встало — тестовое приложение удалено")
    ctx.shell(f"rm -f {PROBE_REMOTE}", check=False, timeout=20)
    window = f"-T '{since}'" if re.fullmatch(r"\d\d-\d\d \d\d:\d\d:\d\d\.000", since) else "-t 1500"
    log = _output(ctx.shell(f"logcat -d -v time {window} 2>/dev/null | grep -E '{_PROBE_LOG}' | head -n 150",
                            check=False, timeout=60))
    lines += _clean(PROBE_TITLE, log) or ["(системный журнал про установку пуст)"]
    return lines


def collect(ctx) -> int:
    """Пишет подробный лог в журнал установки; возвращает число собранных строк."""
    ctx.log(f"{HEADER}\nСобираю сведения о магнитоле для разработчика. На магнитоле ничего не меняется — только в конце "
            "ставлю и сразу удаляю крошечное тестовое приложение, чтобы увидеть, как прошивка отвечает на установку.")
    total, failed_in_row = 0, 0
    for title, command, limit in SECTIONS:
        try:
            lines = _clean(title, _output(ctx.shell(command, check=False, timeout=60)))
        except Exception as exc:  # noqa: BLE001 — классы ошибок разные на ПК и Android
            if type(exc).__name__ == "InstallCancelled":
                raise
            failed_in_row += 1
            ctx.log(f"[{title}] не получилось: {exc}")
            if failed_in_row >= 2:
                raise RuntimeError("Связь с магнитолой потеряна — подробный лог собран не полностью. Подключите "
                                   "магнитолу и нажмите кнопку ещё раз.") from exc
            continue
        failed_in_row = 0
        shown = lines[-limit:] if "журнал" in title else lines[:limit]
        more = f" (ещё {len(lines) - len(shown)} строк не показано)" if len(lines) > len(shown) else ""
        body = "\n".join(f"  {line}" for line in shown) if shown else "  (пусто)"
        ctx.log(f"[{title}]{more}\n{body}")
        total += len(shown)
    try:
        probe = probe_install(ctx)
    except Exception as exc:  # noqa: BLE001 — проба необязательна
        if type(exc).__name__ == "InstallCancelled":
            raise
        probe = [f"не получилось: {exc}"]
    ctx.log(f"[{PROBE_TITLE}]\n" + "\n".join(f"  {line}" for line in probe[:160]))
    total += len(probe)
    ctx.log(f"=== Подробный лог собран: {total} строк. Он уйдёт разработчику вместе с журналом установки. ===")
    return total
