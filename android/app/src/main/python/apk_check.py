"""Файл, который не встанет на магнитолу никаким способом установки (владелец, 2026-10-01: «конечно нужно сразу
сообщать вместо перебора способов»). Раньше программа перебирала все способы и показывала их сырые ошибки: свой
«Spotify … signed.apk» не APK (лог №1986), .xapk вместо APK (№2099), сборка под x86 (№522, №1942), приложение под
Android новее магнитолы (№2087), конфликт подписи с уже установленным приложением (№2583, №2597), подмена
системного приложения без подписи прошивки и обновление постоянного системного приложения (№2673, №2755).

file_problem — проверка самого файла ДО заливки на магнитолу; rejection_message — отказ магнитолы, который значит
«не годится сам файл» (или он не уживается с тем, что уже стоит): остальные способы упрутся в то же самое. Такое
приложение пропускается с понятной причиной, остальные из списка ставятся дальше.

Новые такие отказы можно добавлять без выпуска программы — правилами "apk_rejections" в настройках с сервера
(client_config.py, content/config/client.json): они проверяются ПЕРВЫМИ, встроенные ниже остаются запасными.

Две одинаковые копии: app/apk_check.py (ПК) и android/app/src/main/python/apk_check.py (Android, через Chaquopy) —
строки журнала на обеих платформах одни и те же. Только стандартная библиотека."""
from __future__ import annotations

import os
import re
import zipfile

try:
    from . import client_config  # ПК: модуль пакета app
except ImportError:  # Android (Chaquopy): модули верхнего уровня
    import client_config

_BUNDLE_SUFFIXES = (".xapk", ".apks", ".apkm")
_SDK_RE = re.compile(r"Requires newer sdk version #?(\d+) \(current version is #?(\d+)\)", re.I)
_BROKEN_RE = re.compile(r"INSTALL_PARSE_FAILED_(?:NOT_APK|BAD_MANIFEST|MANIFEST_MALFORMED|MANIFEST_EMPTY)"
                        r"|INSTALL_PARSE_FAILED_UNEXPECTED_EXCEPTION[^\n]*AndroidManifest"
                        r"|Failed to parse APK file", re.I)
_DUPLICATE_PERMISSION_RE = re.compile(
    r"attempting to redeclare permission (?:group )?([\w.]+) already owned by ([\w.]+)", re.I)
_SHARED_USER_RE = re.compile(r"Package ([\w.]+) has no signatures that match those in shared user ([\w.]+)", re.I)
_PERSISTENT_RE = re.compile(r"Package ([\w.]+) is a persistent app", re.I)


def file_problem(path, name: str | None = None) -> str | None:
    """Почему этот файл не APK; None — с виду APK (архив ZIP с AndroidManifest.xml в корне) или файла нет вовсе
    (не скачан — об этом у программы своё сообщение)."""
    name = name or os.path.basename(str(path))
    if not os.path.isfile(path):
        return None
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (zipfile.BadZipFile, OSError, ValueError):
        return f"«{name}» — не APK: файл повреждён или недокачан (не открывается как архив). Скачайте его заново."
    if "AndroidManifest.xml" in names:
        return None
    if name.lower().endswith(_BUNDLE_SUFFIXES) or any(entry.lower().endswith(".apk") for entry in names):
        return (f"«{name}» — не APK, а пакет из нескольких частей (XAPK/APKS) — программа ставит только обычные APK. "
                "Найдите это приложение одним файлом .apk.")
    return f"«{name}» — не APK: внутри нет AndroidManifest.xml. Скачайте приложение заново."


def _remote_rejection(name: str, text: str) -> str | None:
    """Правила "apk_rejections" с сервера (см. client_config.py): первое подошедшее даёт текст. Любая неисправность
    правила (нет текста, битый регэксп) — правило пропускается, до встроенных дело дойдёт как раньше."""
    for rule in client_config.rules("apk_rejections"):
        message = rule.get("message")
        if not isinstance(message, str) or not message.strip():
            continue
        values = client_config.match_rule(rule, text)
        if values is not None:
            return client_config.fill(message, {**values, "name": name})
    return None


def rejection_message(name: str, reason: str) -> str | None:
    """Понятный текст, если магнитола отказала из-за самого файла; None — обычный отказ способа установки."""
    text = reason or ""
    remote = _remote_rejection(name, text)
    if remote:
        return remote
    if "INSTALL_FAILED_NO_MATCHING_ABIS" in text.upper():
        # Процессор бывает и x86 (старые Haval на платформе Harman, лог №2404), поэтому без «нужна arm64».
        return (f"«{name}» собран не под процессор этой магнитолы (в APK нет библиотек под её архитектуру) — не встанет "
                "никаким способом. Нужна другая сборка приложения: universal или под процессор магнитолы.")
    sdk = _SDK_RE.search(text)
    if sdk or "INSTALL_FAILED_OLDER_SDK" in text.upper():
        need = f" (нужен API {sdk.group(1)}, на магнитоле {sdk.group(2)})" if sdk else ""
        return (f"«{name}» требует Android новее, чем на магнитоле{need} — не встанет никаким способом. "
                "Нужна более старая версия приложения.")
    if "INSTALL_FAILED_MISSING_SPLIT" in text.upper():
        return (f"«{name}» — только часть приложения (нужны ещё split-файлы) — один этот файл не встанет. "
                "Найдите версию одним APK.")
    if "INSTALL_PARSE_FAILED_NO_CERTIFICATES" in text.upper():
        # Так же отвечает и старый Android на APK, подписанный только новой схемой (v2/v3, лог №2404: YT Morphe на
        # магнитоле Harman — «Attempt to get length of null array»), — не только неподписанный.
        return (f"«{name}»: магнитола не принимает подпись этого APK — он не подписан или подписан новой схемой, "
                "которую её старый Android не понимает. Не встанет никаким способом; нужна другая сборка приложения.")
    if "INSTALL_FAILED_DUPLICATE_PERMISSION" in text.upper():
        # Разрешение уже объявило другое установленное приложение с другой подписью — PackageManager откажет при любом
        # способе установки (лог №2583: мод Навигатора и штатный yandex.auto.auth на Tank 300; №2597: Яндекс Музыка и
        # мод Навигатора на Haval Jolion — приложения Яндекса из разных источников).
        dup = _DUPLICATE_PERMISSION_RE.search(text)
        if dup:
            permission, owner = dup.group(1).rstrip("."), dup.group(2).rstrip(".")
            return (f"«{name}» конфликтует с уже установленным {owner}: оба объявляют разрешение {permission}, а "
                    "подписаны разными ключами — не встанет никаким способом. Нужна сборка с той же подписью, что у "
                    f"{owner}, или удалите {owner}, если это не штатное приложение магнитолы.")
        return (f"«{name}» конфликтует с уже установленным приложением: оба объявляют одно и то же разрешение, а "
                "подписаны разными ключами — не встанет никаким способом. Нужна сборка с той же подписью или удалите "
                "то приложение, если оно не штатное.")
    if "INSTALL_FAILED_SHARED_USER_INCOMPATIBLE" in text.upper():
        # Подмена системного приложения (общий системный идентификатор вроде android.uid.system) встаёт только с подписью
        # прошивки именно этой магнитолы — остальные способы упрутся в то же самое (логи №2673, №2678, №2755: Settings.apk
        # из каталога Geely Cityray на Geely Preface FS11; после отказа связь ещё и рвалась на каждом следующем способе).
        shared = _SHARED_USER_RE.search(text)
        what = f"системное приложение {shared.group(1)}" if shared else "системное приложение"
        return (f"«{name}» подменяет {what} — такие ставятся только с подписью прошивки именно этой магнитолы. "
                "Не встанет никаким способом; нужна сборка под прошивку этой магнитолы.")
    persistent = _PERSISTENT_RE.search(text)
    if persistent or "PERSISTENT APPS ARE NOT UPDATEABLE" in text.upper():
        # Постоянное (persistent) системное приложение прошивка не обновляет ни через какой установщик (№2673: Time_Zone.apk
        # поверх com.autolink.timesync.service на Geely Preface).
        what = f"постоянное системное приложение {persistent.group(1)}" if persistent else "постоянное системное приложение"
        return f"«{name}» обновляет {what} — прошивка не даёт обновлять такие приложения. Не встанет никаким способом."
    if _BROKEN_RE.search(text):
        return f"«{name}» магнитола не может прочитать — файл повреждён или это не обычный APK. Скачайте его заново."
    return None
