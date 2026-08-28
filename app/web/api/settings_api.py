"""Безопасные настройки обслуживания приложения.

Кэш намеренно уже, чем «все файлы cars/»: сценарии и модели могут быть
созданы локально в редакторе и ещё не опубликованы. Удаляем только payload,
который штатно докачивается с content-сервера, общую библиотеку APK и логи.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from ...content_config import get_base_url
from ...version import APP_VERSION

# Маркер-файл рядом с exe, включающий подробное логирование (см. main_web.py:
# _enable_debug_log_all, app/web/bridge.py: WebApi.debug_mode) — раньше
# ставился только отдельным debug-установщиком (installer_debug.iss,
# убран — эта сборка теперь единственная), включается/выключается прямо из
# "Настроек" (см. set_debug_mode ниже). Читается заново только при следующем
# запуске программы, поэтому переключатель в интерфейсе явно предупреждает
# о перезапуске.
DEBUG_LOG_ALL_MARKER = "DEBUG_LOG_ALL"


class SettingsApi:
    def __init__(self, base_dir: Path, cars_dir: Path, apk_dir: Path, admin_mode: bool = False):
        self.base_dir = base_dir
        self.cars_dir = cars_dir
        self.apk_dir = apk_dir
        self.admin_mode = admin_mode
        self.preferences_path = base_dir / "settings.json"

    def info(self) -> dict:
        cache_bytes = self._cache_size()
        return {
            "app_bytes": self._size_of(self.base_dir),
            "cache_bytes": cache_bytes,
            "server_configured": bool(get_base_url(self.base_dir)),
            "preferences": self._preferences(),
            "app_version": APP_VERSION,
            "debug_mode": (self.base_dir / DEBUG_LOG_ALL_MARKER).exists(),
        }

    def set_debug_mode(self, enabled: bool) -> dict:
        marker = self.base_dir / DEBUG_LOG_ALL_MARKER
        try:
            if enabled:
                marker.touch(exist_ok=True)
            else:
                marker.unlink(missing_ok=True)
        except OSError:
            pass
        return {"debug_mode": marker.exists()}

    def preferences(self) -> dict:
        """Лёгкий вызов для старта: не обходит большие папки с данными."""
        return self._preferences()

    def clear_cache(self) -> dict:
        before = self._cache_size()
        for directory in self._cache_directories():
            if directory == self.apk_dir and self.admin_mode:
                # APK в админ-сборке могут быть ещё не опубликованными.
                continue
            shutil.rmtree(directory, ignore_errors=True)
        for directory in self.cars_dir.rglob("__pycache__") if self.cars_dir.exists() else ():
            shutil.rmtree(directory, ignore_errors=True)
        debug_logs = self.base_dir / "debug_logs"
        shutil.rmtree(debug_logs, ignore_errors=True)
        return {"freed_bytes": before - self._cache_size(), "remaining_bytes": self._cache_size()}

    def set_preferences(self, preferences: dict) -> dict:
        current = self._preferences()
        for key in ("auto_sync", "reduced_motion", "compact_log"):
            if key in preferences:
                current[key] = bool(preferences[key])
        self.preferences_path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return current

    def _preferences(self) -> dict:
        defaults = {"auto_sync": True, "reduced_motion": False, "compact_log": True}
        try:
            loaded = json.loads(self.preferences_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return defaults
        if not isinstance(loaded, dict):
            return defaults
        return {key: bool(loaded.get(key, default)) for key, default in defaults.items()}

    def _cache_directories(self) -> list[Path]:
        payloads = []
        if self.cars_dir.exists():
            for name in ("files", "usb_files"):
                payloads.extend(path for path in self.cars_dir.rglob(name) if path.is_dir())
        return [self.apk_dir, *payloads, self._webview_profile_dir()]

    def _webview_profile_dir(self) -> Path:
        """Тот же путь, который main_web.py передаёт WebView2.

        Это не пользовательские данные программы, а пересоздаваемый профиль
        встроенного браузера, поэтому он входит в безопасную очистку кэша.
        """
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "MagicSQD" / "webview_profile"
        return self.base_dir / "webview_profile"

    def _cache_size(self) -> int:
        total = sum(self._size_of(path) for path in self._cache_directories())
        return total + self._size_of(self.base_dir / "debug_logs")

    @staticmethod
    def _size_of(path: Path) -> int:
        if path.is_file():
            try:
                return path.stat().st_size
            except OSError:
                return 0
        if not path.is_dir():
            return 0
        total = 0
        for file in path.rglob("*"):
            if file.is_file():
                try:
                    total += file.stat().st_size
                except OSError:
                    pass
        return total
