"""Включение закрытого каталога на Android — Kotlin (WebBridge.configureCatalog) зовёт configure()
при старте, передавая секрет официальной сборки (BuildConfig) и случайный секрет этой установки
(SharedPreferences). Без секрета (сборка из исходников) ничего не включается: каталог открыт,
файлы плейнтекстом, как раньше.

Аналог app/catalog_setup.py на ПК, но секреты приходят из Kotlin, а не из файлов. stages.py/install.py
на Android не исполняются (поток спек-driven, см. wizard_spec), поэтому хук catalog_io здесь не нужен.
"""
import app_token
import catalog_key
import content_sync


def configure(build_secret_hex: str, device_random_hex: str, base_url: str, client_id: str) -> None:
    build_secret = bytes.fromhex(build_secret_hex) if build_secret_hex else b""
    device_random = bytes.fromhex(device_random_hex) if device_random_hex else b""
    catalog_key.configure(build_secret, device_random if build_secret else None)
    if build_secret and base_url and client_id:
        content_sync.set_app_token(app_token.AppToken(base_url, build_secret, client_id))
