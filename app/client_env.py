"""Признаки системы для правила доступа по региону (владелец, 12.10.2026: бесплатно — наши языки и часовые пояса,
остальным — по подписке; «чтобы VPN не помогал»; server/access_policy.py). Уходят в запрос токена сборки
(app_token.AppToken env_provider) и вшиваются сервером в подписанный токен.

{"ui": [языки интерфейса системы], "region": региональный формат, "kbd": [языки раскладок клавиатуры],
 "tz": минуты к UTC, "os": "windows" | "macos" | "linux"}. Раскладки важны: у многих техников английская Windows, но
русская раскладка стоит почти у всех. Ничего не определилось — пустые списки (сервер тогда судит по адресу, как у
старых версий). Тот же выбор решает и язык интерфейса программы (ui_language).

"dev" — постоянный номер компьютера (хеш MachineGuid Windows / IOPlatformUUID Mac / machine-id Linux): владелец
14.10.2026 — устройство, хоть раз пришедшее из Алжира или Саудовской Аравии, сервер запоминает, и после переустановки
программы (новый client_id) его узнаёт по этому хешу. Сам номер не уходит — только хеш с солью программы."""
from __future__ import annotations

import hashlib
import locale
import re
import subprocess
import sys
import time

# Наши языки — интерфейс программы на русском (server/access_policy.DEFAULT_FREE_LANGS — те же коды).
RUSSIAN_UI_LANGS = frozenset({"ru", "uk", "be", "kk", "uz", "ky", "tg", "tk", "hy", "az", "ka", "ro", "mo"})

# Раскладки macOS («KeyboardLayout Name» в com.apple.HIToolbox) → код языка.
_MAC_LAYOUTS = (
    ("russian", "ru"), ("ukrainian", "uk"), ("byelorussian", "be"), ("belarusian", "be"), ("kazakh", "kk"),
    ("uzbek", "uz"), ("kyrgyz", "ky"), ("kirghiz", "ky"), ("tajik", "tg"), ("turkmen", "tk"), ("armenian", "hy"),
    ("azeri", "az"), ("azerbaijani", "az"), ("georgian", "ka"), ("romanian", "ro"), ("arabic", "ar"),
    ("french", "fr"), ("german", "de"), ("spanish", "es"), ("italian", "it"), ("portuguese", "pt"), ("polish", "pl"),
    ("turkish", "tr"), ("chinese", "zh"), ("pinyin", "zh"), ("japanese", "ja"), ("korean", "ko"), ("hebrew", "he"),
    ("persian", "fa"), ("hindi", "hi"), ("british", "en"), ("u.s.", "en"), ("abc", "en"), ("australian", "en"),
    ("canadian", "en"), ("irish", "en"),
)


def _tz_minutes() -> int:
    return int(time.localtime().tm_gmtoff // 60)


def _windows() -> dict:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    ui: list[str] = []
    num, size = wintypes.ULONG(0), wintypes.ULONG(0)
    if kernel32.GetUserPreferredUILanguages(0x8, ctypes.byref(num), None, ctypes.byref(size)) and size.value:
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.GetUserPreferredUILanguages(0x8, ctypes.byref(num), buf, ctypes.byref(size)):
            ui = [part for part in ctypes.wstring_at(buf, size.value).split("\x00") if part]
    name = ctypes.create_unicode_buffer(85)
    region = name.value if kernel32.GetUserDefaultLocaleName(name, 85) else ""
    kbd: list[str] = []
    count = user32.GetKeyboardLayoutList(0, None)
    if count > 0:
        layouts = (ctypes.c_void_p * count)()
        count = user32.GetKeyboardLayoutList(count, layouts)
        for handle in layouts[:count]:
            lang_id = (handle or 0) & 0xFFFF
            buf = ctypes.create_unicode_buffer(85)
            if lang_id and kernel32.LCIDToLocaleName(lang_id, buf, 85, 0) and buf.value not in kbd:
                kbd.append(buf.value)
    return {"ui": ui, "region": region, "kbd": kbd}


def _defaults(*args: str) -> str:
    try:
        result = subprocess.run(["/usr/bin/defaults", "read", *args], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout if result.returncode == 0 else ""


def parse_mac_list(text: str) -> list[str]:
    """Вывод `defaults read -g AppleLanguages` — «( "ru-RU", en )» → ["ru-RU", "en"]."""
    items = []
    for raw in re.findall(r'"([^"]+)"|([A-Za-z][\w-]*)', text or ""):
        item = raw[0] or raw[1]
        if item and item not in items:
            items.append(item)
    return items


def parse_mac_layouts(text: str) -> list[str]:
    """Вывод `defaults read com.apple.HIToolbox AppleEnabledInputSources` → коды языков раскладок."""
    codes = []
    for name in re.findall(r'"?KeyboardLayout Name"?\s*=\s*"?([^";\n]+)"?;', text or ""):
        lower = name.strip().lower()
        code = next((code for word, code in _MAC_LAYOUTS if word in lower), None)
        if code and code not in codes:
            codes.append(code)
    for mode in re.findall(r'"?Input Mode"?\s*=\s*"?([^";\n]+)"?;', text or ""):
        lower = mode.lower()  # методы ввода: com.apple.inputmethod.SCIM.ITABC → китайский и т. п.
        code = next((code for word, code in _MAC_LAYOUTS if word in lower), None)
        if code and code not in codes:
            codes.append(code)
    return codes


def _macos() -> dict:
    return {"ui": parse_mac_list(_defaults("-g", "AppleLanguages")),
            "region": _defaults("-g", "AppleLocale").strip(),
            "kbd": parse_mac_layouts(_defaults("com.apple.HIToolbox", "AppleEnabledInputSources"))}


def _other() -> dict:
    tag = locale.getlocale()[0] or ""
    return {"ui": [tag] if tag else [], "region": tag, "kbd": []}


def device_hash(raw: str) -> str:
    """Хеш постоянного номера устройства — так же считает Android (ClientEnv.kt): «magicsqd-device|номер»."""
    return hashlib.sha256(("magicsqd-device|" + raw.strip().lower()).encode("utf-8")).hexdigest()[:32]


def _machine_id() -> str:
    """Постоянный номер компьютера: переживает переустановку программы, меняется с переустановкой системы."""
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            return str(winreg.QueryValueEx(key, "MachineGuid")[0])
    if sys.platform == "darwin":
        out = subprocess.run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True,
                             text=True, timeout=3).stdout
        match = re.search(r'"IOPlatformUUID"\s*=\s*"([^"]+)"', out or "")
        return match.group(1) if match else ""
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path, encoding="ascii") as fh:
                return fh.read().strip()
        except OSError:
            continue
    return ""


_static: dict | None = None  # языки и раскладки за время работы программы не меняются — читаем один раз


def collect() -> dict:
    """Признаки этой системы (для app_token и языка интерфейса). Сбой определения — пустые списки, часовой пояс
    всё равно. Языки читаются один раз за запуск (на macOS это три вызова defaults), часовой пояс — каждый раз."""
    global _static
    if _static is None:
        if sys.platform == "win32":
            os_name, reader = "windows", _windows
        elif sys.platform == "darwin":
            os_name, reader = "macos", _macos
        else:
            os_name, reader = "linux", _other
        try:
            found = reader()
        except Exception:  # noqa: BLE001 — признаки не обязательны: сервер тогда судит по адресу
            found = {"ui": [], "region": "", "kbd": []}
        try:
            raw_id = _machine_id()
        except Exception:  # noqa: BLE001 — без номера устройства сервер узнаёт программу по client_id
            raw_id = ""
        _static = {"ui": list(found.get("ui") or [])[:16], "region": str(found.get("region") or "")[:32],
                   "kbd": list(found.get("kbd") or [])[:16], "os": os_name,
                   "dev": device_hash(raw_id) if raw_id.strip() else ""}
    env = {"ui": list(_static["ui"]), "region": _static["region"], "kbd": list(_static["kbd"]),
           "tz": _tz_minutes(), "os": _static["os"]}
    if _static["dev"]:
        env["dev"] = _static["dev"]
    return env


def _code(tag: str) -> str:
    return re.split(r"[-_@.]", str(tag or "").strip(), maxsplit=1)[0].lower()


def ui_language(env: dict | None = None) -> str:
    """Язык интерфейса программы: есть наш язык в системе (интерфейс, формат или раскладка) — русский, иначе
    английский. Ничего не определилось — русский, как было всегда (основные пользователи — наши)."""
    env = env if env is not None else collect()
    codes = {_code(tag) for tag in (env.get("ui") or []) + (env.get("kbd") or []) + [env.get("region") or ""]} - {""}
    if not codes:
        return "ru"
    return "ru" if codes & RUSSIAN_UI_LANGS else "en"
