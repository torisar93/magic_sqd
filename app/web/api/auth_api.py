"""Аккаунт техника (регистрация/вход/выход, синхронизация СВОИХ заявок на
модерации между устройствами) — см. app/auth_client.py, server/backend.py:
протокол /auth/*. Единая кнопка "Войти" в приложении: если у аккаунта есть
права администратора (см. server/backend.py: set_user_is_admin), вход
заодно поднимает и admin_mode — тем же самым, уже полностью рабочим
admin_client.py/ADMIN_SESSION_COOKIE, которым пользуется отдельный
веб-кабинет разработчика (см. auth_client.login: сервер сам присылает обе
cookie одним ответом)."""
from __future__ import annotations
import secrets
import sys
import threading
import webbrowser
from pathlib import Path

from ..events import event_bridge
from ... import pending_submissions
from ...admin_config import get_admin_base_url
from ...admin_client import get_cached_session, set_cached_session, clear_cached_session
from ...auth_client import AuthClientError, boosty_confirm as _boosty_confirm, boosty_refresh as _boosty_refresh, \
    boosty_start as _boosty_start, boosty_status as _boosty_status, boosty_unlink as _boosty_unlink, \
    change_password as _change_password, download_my_car, \
    forgot_password as _forgot_password, login as _login, logout as _logout, me as _me, \
    my_cars as _my_cars, register as _register, tg_poll as _tg_poll, tg_start as _tg_start, tg_unlink as _tg_unlink
from ...auth_config import clear_saved_session, load_saved_session, save_saved_session
from ...scanner import ModelInfo
from ...submit_config import get_submit_config


class AuthApi:
    def __init__(self, base_dir: Path, scanner_api):
        self.base_dir = base_dir
        self._scanner_api = scanner_api
        self._sync_thread: threading.Thread | None = None
        # Кэш последней успешной сессии в памяти процесса — чтобы logout()/
        # повторные login() не должны заново читать submit.json на каждый
        # вызов, тот же приём, что и у admin_client._session_cache.
        self._email: str | None = None
        self._user_cookie: str | None = None
        self._tg_pending: dict | None = None

    @property
    def user_cookie(self) -> str | None:
        """Для car_editor_api.py: если технику залогинен, отправляемая на
        модерацию заявка привязывается к его аккаунту (см. submit_client.
        submit_model: session_cookie)."""
        return self._user_cookie

    def _auth_base_url(self) -> str | None:
        config = get_submit_config(self.base_dir)
        return config.auth_base_url if config else None

    def _apply_admin_cookie(self, admin_cookie: str | None) -> None:
        admin_base = get_admin_base_url(self.base_dir)
        if not admin_base:
            return
        if admin_cookie:
            set_cached_session(admin_base, admin_cookie)
        else:
            clear_cached_session(admin_base)

    # ------------------------------------------------------------------
    def register(self, email: str, password: str) -> dict:
        base_url = self._auth_base_url()
        if base_url is None:
            return {"ok": False, "error": "Сервер не настроен (нет submit.json)."}
        email = email.strip().lower()
        if not email or not password:
            return {"ok": False, "error": "Введите email и пароль."}
        try:
            _register(base_url, email, password)
        except AuthClientError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def login(self, email: str, password: str, remember: bool = True) -> dict:
        base_url = self._auth_base_url()
        if base_url is None:
            return {"ok": False, "error": "Сервер не настроен (нет submit.json)."}
        email = email.strip().lower()
        if not email or not password:
            return {"ok": False, "error": "Введите email и пароль."}
        try:
            result = _login(base_url, email, password)
        except AuthClientError as exc:
            return {"ok": False, "error": str(exc)}
        self._email, self._user_cookie = result["email"], result["user_cookie"]
        self._apply_admin_cookie(result["admin_cookie"])
        if remember:
            save_saved_session(self.base_dir, result["email"], result["user_cookie"], result["admin_cookie"])
        self.sync_my_cars()
        return {"ok": True, "email": result["email"], "is_admin": result["is_admin"]}

    def logout(self) -> dict:
        base_url = self._auth_base_url()
        if base_url and self._user_cookie:
            try:
                _logout(base_url, self._user_cookie)
            except AuthClientError:
                pass  # сессия и так истекла/недоступна — на клиенте всё равно чистим
        self._email = self._user_cookie = None
        self._apply_admin_cookie(None)
        clear_saved_session(self.base_dir)
        return {"ok": True}

    def change_password(self, current_password: str, new_password: str) -> dict:
        base_url = self._auth_base_url()
        if not (base_url and self._user_cookie and self._email):
            return {"ok": False, "error": "Не выполнен вход."}
        if len(new_password) < 8:
            return {"ok": False, "error": "Новый пароль должен быть не короче 8 символов."}
        try:
            new_cookie = _change_password(base_url, self._user_cookie, current_password, new_password)
        except AuthClientError as exc:
            return {"ok": False, "error": str(exc)}
        # Смена пароля инвалидирует старую сессию на сервере — подменяем её
        # на новую, которую сервер тут же выписал (см. auth_client.
        # change_password), иначе следующий же запрос (например,
        # sync_my_cars) получил бы 401 сразу после успешной смены пароля.
        self._user_cookie = new_cookie
        admin_base = get_admin_base_url(self.base_dir)
        admin_cookie = get_cached_session(admin_base) if admin_base else None
        save_saved_session(self.base_dir, self._email, new_cookie, admin_cookie)
        return {"ok": True}

    # -- Telegram: вход/регистрация без почты и привязка к существующему аккаунту --
    _EXTERNAL_HOSTS = ("https://t.me/", "https://boosty.to/")

    def open_external(self, url: str) -> dict:
        """Открывает в системном браузере только ссылки на Telegram и Boosty."""
        if not any(str(url).startswith(prefix) for prefix in self._EXTERNAL_HOSTS):
            return {"ok": False, "error": "Ссылка не разрешена."}
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001 - браузера может не быть
            return {"ok": False, "error": "Не удалось открыть браузер."}
        return {"ok": True}

    def tg_start(self, purpose: str) -> dict:
        base_url = self._auth_base_url()
        if base_url is None:
            return {"ok": False, "error": "Сервер не настроен (нет submit.json)."}
        if purpose == "link" and not self._user_cookie:
            return {"ok": False, "error": "Не выполнен вход."}
        secret = secrets.token_urlsafe(24)
        system = {"win32": "Windows", "darwin": "macOS"}.get(sys.platform, sys.platform)
        try:
            payload = _tg_start(base_url, purpose, secret, f"Magic SQD · {system}",
                                self._user_cookie if purpose == "link" else None)
        except AuthClientError as exc:
            return {"ok": False, "error": str(exc)}
        self._tg_pending = {"code": payload["code"], "secret": secret, "purpose": purpose}
        self.open_external(payload["link"])
        return {"ok": True, "link": payload["link"], "expires_in": payload.get("expires_in", 600)}

    def tg_poll(self) -> dict:
        """Один опрос. {"ok", "status": pending|awaiting|expired|error|done, ...}; при успешном ВХОДЕ
        сессия уже установлена (как после обычного login) и в ответе есть email/is_admin."""
        pending = getattr(self, "_tg_pending", None)
        base_url = self._auth_base_url()
        if not (pending and base_url):
            return {"ok": False, "status": "expired", "error": "Запрос входа не активен."}
        try:
            payload, cookies = _tg_poll(base_url, pending["code"], pending["secret"])
        except AuthClientError as exc:
            return {"ok": False, "status": "error", "error": str(exc)}
        status = payload.get("status")
        if status in ("expired", "error"):
            self._tg_pending = None
            return {"ok": False, "status": status, "error": payload.get("error") or "Ссылка устарела — начните заново."}
        if status != "done":
            return {"ok": True, "status": status}
        self._tg_pending = None
        if pending["purpose"] == "link":
            return payload
        user_cookie = next((c for c in cookies if c.startswith("magicsqd_user_session=")), None)
        admin_cookie = next((c for c in cookies if c.startswith("magicsqd_admin_session=")), None)
        if not user_cookie:
            return {"ok": False, "status": "error", "error": "Сервер не выдал сессию входа."}
        self._email, self._user_cookie = payload["email"], user_cookie
        self._apply_admin_cookie(admin_cookie)
        save_saved_session(self.base_dir, payload["email"], user_cookie, admin_cookie)
        self.sync_my_cars()
        return {"ok": True, "status": "done", "purpose": "login", "email": payload["email"],
                "is_admin": bool(payload.get("is_admin")), "created": bool(payload.get("created"))}

    def tg_cancel(self) -> dict:
        self._tg_pending = None
        return {"ok": True}

    def tg_unlink(self) -> dict:
        return self._boosty_call(_tg_unlink)

    # -- Boosty: подписчик любого платного уровня снимает лимиты на место и чат --
    def _boosty_call(self, fn, *args) -> dict:
        base_url = self._auth_base_url()
        if not (base_url and self._user_cookie):
            return {"ok": False, "error": "Не выполнен вход."}
        try:
            result = fn(base_url, self._user_cookie, *args)
        except AuthClientError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, **(result or {})}

    def boosty_status(self) -> dict:
        return self._boosty_call(_boosty_status)

    def boosty_start(self, email: str) -> dict:
        return self._boosty_call(_boosty_start, email)

    def boosty_confirm(self, code: str) -> dict:
        return self._boosty_call(_boosty_confirm, code)

    def boosty_refresh(self) -> dict:
        return self._boosty_call(_boosty_refresh)

    def boosty_unlink(self) -> dict:
        return self._boosty_call(_boosty_unlink)

    def forgot_password(self, email: str) -> dict:
        base_url = self._auth_base_url()
        if base_url is None:
            return {"ok": False, "error": "Сервер не настроен (нет submit.json)."}
        email = email.strip().lower()
        if not email:
            return {"ok": False, "error": "Введите email."}
        try:
            _forgot_password(base_url, email)
        except AuthClientError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def status(self) -> dict:
        """Тихая проверка сохранённой сессии — вызывается один раз при
        старте (см. bridge.py), ДО показа UI входа. {"ok": False} без
        текста ошибки, если сессии нет/истекла — дальше просто показывается
        обычная кнопка "Войти", объяснять нечего (та же логика, что и у
        admin_api.py:try_saved_login)."""
        base_url = self._auth_base_url()
        if base_url is None:
            return {"ok": False}
        saved = load_saved_session(self.base_dir)
        if saved is None:
            return {"ok": False}
        # Админ-cookie восстанавливаем СРАЗУ и оптимистично (может быть уже
        # просрочена — 24ч, короче 30-дневной сессии техника) — первый же
        # настоящий admin_client-вызов сам обнаружит 401 и почистит её;
        # никакого вреда от попытки нет.
        self._apply_admin_cookie(saved["admin_cookie"])
        try:
            info = _me(base_url, saved["user_cookie"])
        except AuthClientError:
            clear_saved_session(self.base_dir)
            self._apply_admin_cookie(None)
            return {"ok": False}
        self._email, self._user_cookie = info["email"], saved["user_cookie"]
        self.sync_my_cars()
        return {"ok": True, "email": info["email"], "is_admin": info["is_admin"]}

    # ------------------------------------------------------------------
    def sync_my_cars(self) -> dict:
        """Тянет список СВОИХ заявок на модерации (см. GET /auth/my-cars) и
        стейджит каждую локально — тот же механизм (pending_submissions.
        stage + ScannerApi.register_pending), которым уже пользуется
        админский "На модерации" (см. submissions_api.py:_stage_worker),
        просто без похода за сессией администратора: свежая своя, только
        что подтверждённая /auth/login или /auth/me. Фоновый поток — список
        может быть заявок несколько, каждая скачивается отдельно."""
        base_url = self._auth_base_url()
        if not (base_url and self._user_cookie):
            return {"ok": False, "error": "Не выполнен вход."}
        if self._sync_thread is not None and self._sync_thread.is_alive():
            return {"ok": True}  # уже синхронизируется, второй запуск не нужен
        self._sync_thread = threading.Thread(
            target=self._sync_worker, args=(base_url, self._user_cookie), daemon=True)
        self._sync_thread.start()
        return {"ok": True}

    def _sync_worker(self, base_url: str, user_cookie: str) -> None:
        try:
            items = _my_cars(base_url, user_cookie, all_states=True)
        except AuthClientError as exc:
            event_bridge.push({"kind": "auth_sync_finished", "success": False, "error": str(exc)})
            return
        staged_models = []
        for item in items:
            name, brand, model, modification = item["name"], item.get("brand"), item.get("model"), item.get("modification")
            if not brand or not model:
                continue  # очень старая заявка без метаданных — как и в submissions_api.py
            status = item.get("status") or "pending"
            if status == "approved":
                # Уже в общем каталоге — свою копию рядом не дублируем; прежний
                # локальный стейдж (когда заявка ещё ждала) больше не нужен.
                pending_submissions.discard(self.base_dir, name)
                self._scanner_api.unregister_pending_by_submission(name)
                continue
            tmp_zip = self.base_dir / pending_submissions.PENDING_DIRNAME / f"{Path(name).stem}.download.zip"
            try:
                download_my_car(base_url, user_cookie, name, tmp_zip)
                model_dir = pending_submissions.stage(self.base_dir, name, tmp_zip)
                model_info = ModelInfo(
                    brand=brand, name=model, dir=model_dir,
                    stages_script=(model_dir / "stages.py") if (model_dir / "stages.py").exists() else None,
                    modification=modification or None, is_pending=True, submission_name=name,
                    submission_status=status,
                )
                staged_models.append(self._scanner_api.register_pending(model_info))
            except AuthClientError:
                continue  # эта конкретная заявка не подтянулась — не блокируем остальные
            finally:
                tmp_zip.unlink(missing_ok=True)
        event_bridge.push({"kind": "auth_sync_finished", "success": True, "models": staged_models})
