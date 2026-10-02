"""Файл, который не встанет на магнитолу никаким способом установки (владелец, 2026-10-01: «конечно нужно сразу
сообщать вместо перебора способов»). Раньше программа перебирала все способы и показывала их сырые ошибки: свой
«Spotify … signed.apk» не APK (лог №1986), .xapk вместо APK (№2099), сборка под x86 (№522, №1942), приложение под
Android новее магнитолы (№2087).

file_problem — проверка самого файла ДО заливки на магнитолу; rejection_message — отказ магнитолы, который значит
«не годится сам файл»: остальные способы упрутся в то же самое. Такое приложение пропускается с понятной причиной,
остальные из списка ставятся дальше.

Две одинаковые копии: app/apk_check.py (ПК) и android/app/src/main/python/apk_check.py (Android, через Chaquopy) —
строки журнала на обеих платформах одни и те же. Только стандартная библиотека."""
from __future__ import annotations

import os
import re
import zipfile

_BUNDLE_SUFFIXES = (".xapk", ".apks", ".apkm")
_SDK_RE = re.compile(r"Requires newer sdk version #?(\d+) \(current version is #?(\d+)\)", re.I)
_BROKEN_RE = re.compile(r"INSTALL_PARSE_FAILED_(?:NOT_APK|BAD_MANIFEST|MANIFEST_MALFORMED|MANIFEST_EMPTY)"
                        r"|INSTALL_PARSE_FAILED_UNEXPECTED_EXCEPTION[^\n]*AndroidManifest"
                        r"|Failed to parse APK file", re.I)


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


def rejection_message(name: str, reason: str) -> str | None:
    """Понятный текст, если магнитола отказала из-за самого файла; None — обычный отказ способа установки."""
    text = reason or ""
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
    if _BROKEN_RE.search(text):
        return f"«{name}» магнитола не может прочитать — файл повреждён или это не обычный APK. Скачайте его заново."
    return None
