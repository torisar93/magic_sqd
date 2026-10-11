"""Аккаунт техника (регистрация/вход/выход, синхронизация СВОИХ заявок на
модерации между устройствами) — см. app/auth_client.py, server/backend.py:
протокол /auth/*. Единый вход (11.10): одна сессия — аккаунта. Если у аккаунта есть права администратора
(server/backend.py: set_user_is_admin), она же кладётся в кеш admin_client (сервер принимает её в
/admin/api/*), и включается admin_mode. Отдельной админ-сессии и входа по логину/паролю веб-кабинета у
программы нет. Состояние аккаунта (account) передаётся в интерфейс целиком — срок входа и будущие поля
сервера доходят до окна аккаунта без выпуска программы."""
from __future__ import annotations
import threading
from pathlib import Path
from urllib.parse import urlparse

from ..events import event_bridge
from ... import pending_submissions
from ...admin_config import get_admin_base_url
from ...admin_client import set_cached_session, clear_cached_session
from ...auth_client import AuthClientError, change_password as _change_password, download_my_car, \
    forgot_password as _forgot_password, login as _login, logout as _logout, me as _me, \
    my_cars as _my_cars, register as _register
from ...auth_config import clear_saved_session, load_saved_session, save_saved_session
from ...content_sync import set_auth_cookie
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
        # Последний ответ сервера об аккаунте (auth_client._account): email, is_admin, subscriber,
        # session_expires_at, … — для окна аккаунта (state/refresh).
        self._account: dict = {}

    @property
    def user_cookie(self) -> str | None:
        """Для car_editor_api.py: если технику залогинен, отправляемая на
        модерацию заявка привязывается к его аккаунту (см. submit_client.
        submit_model: session_cookie)."""
        return self._user_cookie

    def _auth_base_url(self) -> str | None:
        config = get_submit_config(self.base_dir)
        return config.auth_base_url if config else None

    def _set_session(self, email: str | None, user_cookie: str | None) -> None:
        """Сессия техника — и для каталога: вошедшему сервер отдаёт скрытые модели его
        групп (см. content_sync.set_auth_cookie, server/user_groups.py)."""
        self._email, self._user_cookie = email, user_cookie
        base_url = self._auth_base_url()
        set_auth_cookie(user_cookie, urlparse(base_url).hostname if base_url else None)

    def _apply_account(self, account: dict | None) -> None:
        """Запоминает состояние аккаунта и даёт admin_client ту же сессию аккаунта, если у него есть права
        администратора (сервер принимает её в /admin/api/*); иначе кеш админки пуст."""
        self._account = dict(account or {})
        admin_base = get_admin_base_url(self.base_dir)
        if not admin_base:
            return
        if self._user_cookie and self._account.get("is_admin"):
            set_cached_session(admin_base, self._user_cookie)
        else:
            clear_cached_session(admin_base)

    def state(self) -> dict:
        """Что известно об аккаунте сейчас, без похода в сеть — для интерфейса (кнопка и окно аккаунта)."""
        if not self._email:
            return {"ok": False}
        return {"ok": True, **self._account, "email": self._email}

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
        account = {key: value for key, value in result.items() if key != "user_cookie"}
        self._set_session(result["email"], result["user_cookie"])
        self._apply_account(account)
        if remember:
            save_saved_session(self.base_dir, result["email"], result["user_cookie"], None)
        self.sync_my_cars()
        return {"ok": True, **account}

    def logout(self) -> dict:
        base_url = self._auth_base_url()
        if base_url and self._user_cookie:
            try:
                _logout(base_url, self._user_cookie)
            except AuthClientError:
                pass  # сессия и так истекла/недоступна — на клиенте всё равно чистим
        self._set_session(None, None)
        self._apply_account(None)
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
        self._set_session(self._email, new_cookie)
        self._apply_account(self._account)  # админке — тоже новая сессия
        save_saved_session(self.base_dir, self._email, new_cookie, None)
        return {"ok": True}

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
        """Тихая проверка сохранённой сессии — вызывается один раз при старте (см. bridge.py), ДО показа
        интерфейса. Вход истёк (сервер ответил 401) — {"ok": False, "expired": True, "email": …}: окно
        аккаунта покажет «Вход истёк» с уже вписанной почтой. Нет сети — сессию НЕ выбрасываем (она может быть
        жива): {"ok": False, "offline": True}."""
        base_url = self._auth_base_url()
        if base_url is None:
            return {"ok": False}
        saved = load_saved_session(self.base_dir)
        if saved is None:
            return {"ok": False}
        try:
            info = _me(base_url, saved["user_cookie"])
        except AuthClientError as exc:
            if exc.status == 401:
                clear_saved_session(self.base_dir)
                self._apply_account(None)
                return {"ok": False, "expired": True, "email": saved.get("email")}
            return {"ok": False, "offline": True, "email": saved.get("email")}
        self._set_session(info["email"], saved["user_cookie"])
        self._apply_account(info)
        self.sync_my_cars()
        return {"ok": True, **info}

    def refresh(self) -> dict:
        """Свежее состояние аккаунта с сервера (окно аккаунта открыли, админ-действию отказали): продлевает
        вход и обновляет срок, права, подписку. Вход истёк — {"ok": False, "expired": True, "email": …}."""
        base_url = self._auth_base_url()
        if not (base_url and self._user_cookie):
            return {"ok": False}
        try:
            info = _me(base_url, self._user_cookie)
        except AuthClientError as exc:
            if exc.status == 401:
                email = self._email
                self._set_session(None, None)
                self._apply_account(None)
                clear_saved_session(self.base_dir)
                return {"ok": False, "expired": True, "email": email}
            return {"ok": False, "offline": True, **self.state()}
        self._apply_account(info)
        return {"ok": True, **info}

    def refresh_subscriber(self) -> dict:
        """Свежий статус «подписчик Boosty» (владелец выставляет его вручную и он может измениться,
        пока программа открыта) — для цвета кнопки аккаунта."""
        result = self.refresh()
        return {"ok": True, "subscriber": result.get("subscriber", False)} if result.get("ok") else {"ok": False}

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
