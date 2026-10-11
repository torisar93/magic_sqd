"""Единый вход в ПК-программе (владелец 11.10: «часто вылезает надпись, что сессия входа истекла… в ПК несколько
мест, через которые можно залогиниться — привести к единому виду»). Одна сессия — аккаунта: у администратора она же
кладётся в кеш admin_client (сервер принимает её в /admin/api/*); «вход истёк» (401) отличается от «нет сети»;
админ-действия без сессии отвечают need_login; логин и пароль веб-кабинета на диск больше не пишутся."""
from types import SimpleNamespace

import pytest

from app import admin_client, admin_config
from app.auth_client import AuthClientError
from app.web.api import admin_api, auth_api

AUTH = "https://auth.test"
ADMIN = "https://admin.test"
COOKIE = "magicsqd_user_session=tok-1"


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_api, "get_submit_config", lambda base_dir: SimpleNamespace(auth_base_url=AUTH))
    monkeypatch.setattr(auth_api, "get_admin_base_url", lambda base_dir: ADMIN)
    monkeypatch.setattr(auth_api, "set_auth_cookie", lambda cookie, host: None)
    monkeypatch.setattr(auth_api.AuthApi, "sync_my_cars", lambda self: {"ok": True})
    admin_client.clear_cached_session(ADMIN)
    yield auth_api.AuthApi(tmp_path, scanner_api=None)
    admin_client.clear_cached_session(ADMIN)


def _account(is_admin=True, **extra):
    return {"email": "owner@example.com", "is_admin": is_admin, "subscriber": False,
            "session_expires_at": 1_800_000_000.0, **extra}


def test_admin_login_puts_account_session_into_admin_cache(api, monkeypatch):
    monkeypatch.setattr(auth_api, "_login", lambda base, email, pw: {**_account(), "user_cookie": COOKIE})
    result = api.login("owner@example.com", "pw")
    assert result["ok"] and result["is_admin"]
    assert admin_client.get_cached_session(ADMIN) == COOKIE  # одна сессия — и для админки


def test_regular_login_leaves_admin_cache_empty(api, monkeypatch):
    monkeypatch.setattr(auth_api, "_login", lambda base, email, pw: {**_account(is_admin=False), "user_cookie": COOKIE})
    assert api.login("tech@example.com", "pw")["ok"]
    assert admin_client.get_cached_session(ADMIN) is None


def test_login_passes_whole_account_to_interface(api, monkeypatch):
    monkeypatch.setattr(auth_api, "_login",
                        lambda base, email, pw: {**_account(new_field="x"), "user_cookie": COOKIE})
    result = api.login("owner@example.com", "pw")
    assert result["session_expires_at"] == 1_800_000_000.0
    assert result["new_field"] == "x"  # новые поля сервера доходят до интерфейса без выпуска
    assert "user_cookie" not in result
    assert api.state()["session_expires_at"] == 1_800_000_000.0


def test_logout_clears_admin_cache(api, monkeypatch):
    monkeypatch.setattr(auth_api, "_login", lambda base, email, pw: {**_account(), "user_cookie": COOKIE})
    monkeypatch.setattr(auth_api, "_logout", lambda base, cookie: None)
    api.login("owner@example.com", "pw")
    api.logout()
    assert admin_client.get_cached_session(ADMIN) is None
    assert api.state() == {"ok": False}


def test_saved_session_restored_at_startup(api, monkeypatch, tmp_path):
    auth_api.save_saved_session(tmp_path, "owner@example.com", COOKIE, None)
    monkeypatch.setattr(auth_api, "_me", lambda base, cookie: _account())
    result = api.status()
    assert result["ok"] and result["is_admin"]
    assert admin_client.get_cached_session(ADMIN) == COOKIE


def test_expired_session_at_startup_is_reported_with_email(api, monkeypatch, tmp_path):
    auth_api.save_saved_session(tmp_path, "owner@example.com", COOKIE, None)

    def expired(base, cookie):
        raise AuthClientError("нет активной сессии", status=401)

    monkeypatch.setattr(auth_api, "_me", expired)
    result = api.status()
    assert result == {"ok": False, "expired": True, "email": "owner@example.com"}
    assert auth_api.load_saved_session(tmp_path) is None
    assert admin_client.get_cached_session(ADMIN) is None


def test_no_network_at_startup_keeps_saved_session(api, monkeypatch, tmp_path):
    auth_api.save_saved_session(tmp_path, "owner@example.com", COOKIE, None)

    def offline(base, cookie):
        raise AuthClientError("Не удалось связаться с сервером", status=None)

    monkeypatch.setattr(auth_api, "_me", offline)
    result = api.status()
    assert result["offline"] and not result["ok"]
    assert auth_api.load_saved_session(tmp_path) is not None  # сессия может быть жива — не выбрасываем


def test_refresh_detects_expired_session(api, monkeypatch):
    monkeypatch.setattr(auth_api, "_login", lambda base, email, pw: {**_account(), "user_cookie": COOKIE})
    api.login("owner@example.com", "pw")

    def expired(base, cookie):
        raise AuthClientError("нет активной сессии", status=401)

    monkeypatch.setattr(auth_api, "_me", expired)
    result = api.refresh()
    assert result == {"ok": False, "expired": True, "email": "owner@example.com"}
    assert admin_client.get_cached_session(ADMIN) is None


def test_refresh_updates_expiry_and_rights(api, monkeypatch):
    monkeypatch.setattr(auth_api, "_login", lambda base, email, pw: {**_account(), "user_cookie": COOKIE})
    api.login("owner@example.com", "pw")
    monkeypatch.setattr(auth_api, "_me", lambda base, cookie: _account(is_admin=False, session_expires_at=1_900_000_000.0))
    result = api.refresh()
    assert result["ok"] and result["session_expires_at"] == 1_900_000_000.0
    assert admin_client.get_cached_session(ADMIN) is None  # права сняли — админке сессии не даём


# --- админ-действия без сессии — need_login ---

@pytest.fixture
def admin(tmp_path, monkeypatch):
    monkeypatch.setattr(admin_api, "get_admin_base_url", lambda base_dir: ADMIN)
    admin_client.clear_cached_session(ADMIN)
    yield admin_api.AdminApi(tmp_path, tmp_path / "apk")
    admin_client.clear_cached_session(ADMIN)


def test_upload_without_session_asks_for_login(admin):
    result = admin.start_upload()
    assert result["need_login"] and not result["ok"]


def test_browse_without_session_asks_for_login(admin):
    assert admin.browse_server_cars("")["need_login"]


def test_expired_session_on_admin_action_asks_for_login(admin, monkeypatch):
    admin_client.set_cached_session(ADMIN, COOKIE)

    def expired(base, cookie, rel):
        raise admin_client.AdminClientError("Сессия входа истекла — войдите заново.")

    monkeypatch.setattr(admin_api, "list_cars_path", expired)
    result = admin.browse_server_cars("")
    assert result["need_login"]
    assert admin_client.get_cached_session(ADMIN) is None


def test_other_admin_errors_are_shown_as_is(admin, monkeypatch):
    admin_client.set_cached_session(ADMIN, COOKIE)

    def broken(base, cookie, rel):
        raise admin_client.AdminClientError("Сервер ответил 500")

    monkeypatch.setattr(admin_api, "list_cars_path", broken)
    result = admin.browse_server_cars("")
    assert result == {"ok": False, "error": "Сервер ответил 500"}


# --- пароль веб-кабинета на диске ---

def test_web_cabinet_password_is_never_saved():
    assert not hasattr(admin_config, "save_saved_login")
    assert not hasattr(admin_api.AdminApi, "login_only")
    assert not hasattr(admin_api.AdminApi, "try_saved_login")


def test_old_saved_password_file_is_wiped(tmp_path):
    (tmp_path / "admin_saved_login.json").write_text('{"username": "u", "password": "p"}', encoding="utf-8")
    admin_config.clear_saved_login(tmp_path)
    assert not (tmp_path / "admin_saved_login.json").exists()


# --- заявки на модерации — тот же единый вход ---

@pytest.fixture
def submissions(tmp_path, monkeypatch):
    from app.web.api import submissions_api
    monkeypatch.setattr(submissions_api, "get_admin_base_url", lambda base_dir: ADMIN)
    admin_client.clear_cached_session(ADMIN)
    yield submissions_api, submissions_api.SubmissionsApi(tmp_path, tmp_path / "cars", scanner_api=None)
    admin_client.clear_cached_session(ADMIN)


def test_submissions_without_session_ask_for_login(submissions):
    _, api = submissions
    result = api.list()
    assert result["need_login"] and not result["ok"]


def test_submissions_expired_session_asks_for_login(submissions, monkeypatch):
    module, api = submissions
    admin_client.set_cached_session(ADMIN, COOKIE)

    def expired(base, cookie):
        raise admin_client.AdminClientError("Сессия входа истекла — войдите заново.")

    monkeypatch.setattr(module, "list_submissions", expired)
    assert api.list()["need_login"]
    assert admin_client.get_cached_session(ADMIN) is None
