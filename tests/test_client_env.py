"""Признаки системы для правила доступа по региону (app/client_env.py, с 1.1.4): языки, раскладки, часовой пояс —
и выбор языка интерфейса программы."""
from __future__ import annotations

from app import client_env

MAC_LANGUAGES = '(\n    "ru-RU",\n    "en-RU"\n)\n'
MAC_SOURCES = '''(
        {
        InputSourceKind = "Keyboard Layout";
        "KeyboardLayout ID" = 252;
        "KeyboardLayout Name" = ABC;
    },
        {
        InputSourceKind = "Keyboard Layout";
        "KeyboardLayout ID" = 19458;
        "KeyboardLayout Name" = RussianWin;
    },
        {
        InputSourceKind = "Keyboard Layout";
        "KeyboardLayout Name" = "Arabic - QWERTY";
    }
)'''


def test_mac_outputs_are_parsed():
    assert client_env.parse_mac_list(MAC_LANGUAGES) == ["ru-RU", "en-RU"]
    assert client_env.parse_mac_list("(\n    en\n)") == ["en"]
    assert client_env.parse_mac_layouts(MAC_SOURCES) == ["en", "ru", "ar"]


def test_collect_has_the_expected_shape():
    env = client_env.collect()
    assert set(env) - {"dev"} == {"ui", "region", "kbd", "tz", "os"}
    assert "dev" not in env or (len(env["dev"]) == 32 and set(env["dev"]) <= set("0123456789abcdef"))
    assert isinstance(env["tz"], int) and -14 * 60 <= env["tz"] <= 14 * 60
    assert env["os"] in ("windows", "macos", "linux")


def test_ui_language_russian_if_any_of_our_languages():
    assert client_env.ui_language({"ui": ["en-US"], "region": "en-US", "kbd": ["en-US", "ru-RU"]}) == "ru"
    assert client_env.ui_language({"ui": ["kk-KZ"], "region": "", "kbd": []}) == "ru"
    assert client_env.ui_language({"ui": ["fr-FR"], "region": "fr-DZ", "kbd": ["fr-FR", "ar-DZ"]}) == "en"
    # Ничего не определилось (сбой чтения) — русский, как было всегда: английский только по явным признакам.
    assert client_env.ui_language({"ui": [], "region": "", "kbd": []}) == "ru"


def test_device_hash_is_stable_and_matches_android(monkeypatch):
    """Номер устройства — только хеш с солью программы (так же считает ClientEnv.kt): сервер узнаёт компьютер после
    переустановки программы, сам MachineGuid не уходит."""
    h = client_env.device_hash("1F0C2A44-9A2B-4C3D-8E5F-0123456789AB")
    assert h == client_env.device_hash(" 1f0c2a44-9a2b-4c3d-8e5f-0123456789ab ") and len(h) == 32
    assert h == "%s" % __import__("hashlib").sha256(b"magicsqd-device|1f0c2a44-9a2b-4c3d-8e5f-0123456789ab").hexdigest()[:32]
    monkeypatch.setattr(client_env, "_static", None)
    monkeypatch.setattr(client_env, "_machine_id", lambda: "")
    assert "dev" not in client_env.collect()  # номер не узнали — признаки без него, как раньше
    monkeypatch.setattr(client_env, "_static", None)
    monkeypatch.setattr(client_env, "_machine_id", lambda: "abc")
    assert client_env.collect()["dev"] == client_env.device_hash("abc")
    monkeypatch.setattr(client_env, "_static", None)
