"""Настройки и правила с сервера (владелец, 2026-10-06: «чтобы обновлений приложения стало поменьше»).

`content/config/client.json` на сервере — то, что раньше было зашито в программу и менялось только выпуском новой
версии: отказы магнитолы «файл не годится» (apk_check.rejection_message — выпуски 1.0.55/1.0.57/1.0.59 состояли
почти только из них), окна «что сделать» (js/user_errors.js), тексты, настройки и флажки. Программа скачивает файл
при запуске и держит копию рядом с собой: при подключении по Wi-Fi ADB интернета обычно нет. Нет файла, битый JSON,
схема новее, чем понимает эта версия, — работают только встроенные правила, как раньше.

Серверные правила проверяются ПЕРВЫМИ, встроенные остаются запасными: так можно и добавить новое правило, и поправить
встроенное (у правил окон «что сделать» — тот же id). Правило можно ограничить версиями и платформой:
`"min_app": "1.0.62"`, `"max_app": "1.0.70"`, `"platforms": ["pc"]` (или "android").

Формат файла (схема 1):
    {"schema": 1,
     "apk_rejections": [{"id": "…", "contains": "INSTALL_FAILED_…", "regex": "…(?P<pkg>[\\w.]+)…",
                         "message": "«{name}» … {pkg} …"}],
     "user_errors": [{"id": "…", "title": "…", "match": ["регэксп", …], "unless": […], "text": "…" | {"pc": …,
                      "android": …}, "steps": […] | {"pc": […], "android": […]}, "icon": "…", "keepMessage": false}],
     "settings": {"имя": значение}, "flags": {"имя": true}, "texts": {"ключ": "текст"}}
В тексте `{name}` — имя файла, `{g1}`, `{g2}`… — группы регэкспа по номеру, `{имя}` — именованные группы. Подстановка
своя (не str.format): никаких обращений к атрибутам, неизвестный ключ остаётся как есть.

Файл ОДИНАКОВЫЙ на ПК (app/client_config.py) и Android (android/app/src/main/python/client_config.py) — правьте оба
сразу (tests/test_client_config.py сверяет копии). Только стандартная библиотека."""
from __future__ import annotations

import json
import os
import re
import threading
import urllib.error
import urllib.request

try:
    from . import content_sync  # ПК: пакет app
except ImportError:  # pragma: no cover
    import content_sync  # Android

SCHEMA = 1  # самая новая схема файла, которую понимает эта версия программы
REMOTE_PATH = "config/client.json"  # от content/ (base_url сервера уже заканчивается на /content)
CACHE_NAME = "client_config.json"
_PLATFORMS = ("pc", "android")

_lock = threading.Lock()
_path: str | None = None
_app_version = ""
_platform = "pc"
_data: dict = {}
_loaded_mtime: float | None = None


def configure(cache_path, app_version: str = "", platform: str = "pc") -> None:
    """Где лежит копия файла и кто спрашивает (версия программы и платформа — для ограничений у правил)."""
    global _path, _app_version, _platform, _loaded_mtime
    with _lock:
        _path = str(cache_path) if cache_path else None
        _app_version = str(app_version or "")
        _platform = platform if platform in _PLATFORMS else "pc"
        _loaded_mtime = None  # перечитать с нового места


def _valid(data) -> bool:
    if not isinstance(data, dict):
        return False
    schema = data.get("schema")
    return isinstance(schema, int) and not isinstance(schema, bool) and 1 <= schema <= SCHEMA


def data() -> dict:
    """Текущая копия (перечитывается, если файл на диске поменялся); нет или не годится — {}."""
    global _data, _loaded_mtime
    with _lock:
        path = _path
        if not path:
            return {}
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            _data, _loaded_mtime = {}, None
            return {}
        if mtime != _loaded_mtime:
            try:
                with open(path, encoding="utf-8") as f:
                    loaded = json.load(f)
            except (OSError, ValueError):
                loaded = None
            _data = loaded if _valid(loaded) else {}
            _loaded_mtime = mtime
        return _data


def refresh(base_url: str, timeout: float = 10) -> bool:
    """Скачать свежий файл и заменить копию. True — копия поменялась. Любой сбой — остаётся прежняя."""
    path = _path
    if not base_url or not path:
        return False
    try:
        with content_sync.open_url(f"{base_url.rstrip('/')}/{REMOTE_PATH}", timeout) as resp:
            raw = resp.read()
        parsed = json.loads(raw.decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False
    if not _valid(parsed):
        return False
    try:
        with open(path, "rb") as f:
            if f.read() == raw:
                return False  # не поменялся
    except OSError:
        pass
    tmp = path + ".part"
    try:
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, path)
    except OSError:
        return False
    return True


def _version_tuple(text: str) -> tuple:
    parts = []
    for chunk in str(text).strip().lstrip("vV").split("."):
        match = re.match(r"\d+", chunk)
        parts.append(int(match.group()) if match else 0)
    return tuple(parts) or (0,)


def applies(rule: dict) -> bool:
    """Правило для этой версии и платформы? Без ограничений — для всех."""
    if not isinstance(rule, dict):
        return False
    platforms = rule.get("platforms")
    if isinstance(platforms, list) and platforms and _platform not in platforms:
        return False
    version = _version_tuple(_app_version) if _app_version else None
    if version is not None:
        if isinstance(rule.get("min_app"), str) and version < _version_tuple(rule["min_app"]):
            return False
        if isinstance(rule.get("max_app"), str) and version > _version_tuple(rule["max_app"]):
            return False
    return True


def rules(name: str) -> list:
    """Список правил раздела (только подходящие этой версии и платформе)."""
    items = data().get(name)
    return [rule for rule in items if applies(rule)] if isinstance(items, list) else []


def _typed(section: str, key: str, default):
    values = data().get(section)
    if not isinstance(values, dict) or key not in values:
        return default
    value = values[key]
    if default is not None and type(value) is not type(default):
        # Число в JSON может прийти целым, когда по умолчанию дробное, — это годится; остальное — нет.
        if isinstance(default, float) and isinstance(value, int) and not isinstance(value, bool):
            return float(value)
        return default
    return value


def setting(key: str, default):
    """Значение из "settings" того же типа, что default; иначе default."""
    return _typed("settings", key, default)


def flag(key: str, default: bool = False) -> bool:
    return bool(_typed("flags", key, bool(default)))


def text(key: str, default: str) -> str:
    return _typed("texts", key, default)


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def fill(template: str, values: dict) -> str:
    """Подставить {ключ} из values; неизвестный ключ остаётся как есть."""
    return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


def match_rule(rule: dict, text: str):
    """Подходит ли текст ошибки под правило ("contains" — подстрока без учёта регистра, "regex" — поиск без учёта
    регистра). Возвращает словарь групп для подстановки или None. Битый регэксп — правило не подходит."""
    contains, regex = rule.get("contains"), rule.get("regex")
    if not isinstance(contains, str) and not isinstance(regex, str):
        return None
    if isinstance(contains, str) and contains and contains.upper() not in text.upper():
        return None
    values: dict = {}
    if isinstance(regex, str) and regex:
        try:
            found = re.search(regex, text, re.IGNORECASE)
        except re.error:
            return None
        if not found:
            return None
        values.update({f"g{i}": group for i, group in enumerate(found.groups(), start=1) if group is not None})
        values.update({key: group for key, group in found.groupdict().items() if group is not None})
    return values


def view() -> dict:
    """Копия для интерфейса: списки правил — только подходящие этой версии и платформе."""
    current = data()
    return {key: ([rule for rule in value if applies(rule)] if isinstance(value, list) else value)
            for key, value in current.items()}


def as_json() -> str:
    return json.dumps(view(), ensure_ascii=False)
