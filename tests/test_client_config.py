"""Настройки и правила с сервера (app/client_config.py, content/config/client.json; владелец, 2026-10-06: «чтобы
обновлений приложения стало поменьше»): копия рядом с программой, серверные правила отказов APK — первыми,
встроенные — запасными; битый/новый файл не ломает работу."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import apk_check, client_config

ROOT = Path(__file__).resolve().parents[1]
ANDROID_PY = ROOT / "android/app/src/main/python"


@pytest.mark.parametrize("name", ["client_config.py", "apk_check.py"])
def test_android_copy_is_identical(name):
    assert (ROOT / "app" / name).read_bytes() == (ANDROID_PY / name).read_bytes(), (
        f"app/{name} и android/.../python/{name} разошлись — правьте обе копии")


@pytest.fixture()
def config(tmp_path):
    path = tmp_path / client_config.CACHE_NAME
    client_config.configure(path, "1.0.62", "pc")
    yield path
    client_config.configure(None)


def _write(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_no_file_means_builtin_only(config):
    assert client_config.data() == {}
    assert client_config.rules("apk_rejections") == []
    assert client_config.setting("x", 5) == 5
    assert client_config.flag("y") is False


@pytest.mark.parametrize("payload", ["{битый", json.dumps({"schema": 99, "flags": {"y": True}}),
                                     json.dumps({"flags": {"y": True}}), json.dumps([1, 2])])
def test_unusable_file_is_ignored(config, payload):
    config.write_text(payload, encoding="utf-8")
    assert client_config.data() == {}
    assert client_config.flag("y") is False


def test_typed_values_fall_back_on_wrong_type(config):
    _write(config, {"schema": 1, "settings": {"wait": 120, "ratio": 2, "name": 7},
                    "flags": {"on": True}, "texts": {"hello": "Привет"}})
    assert client_config.setting("wait", 90) == 120
    assert client_config.setting("ratio", 0.5) == 2.0  # целое вместо дробного — годится
    assert client_config.setting("name", "x") == "x"  # число вместо строки — нет
    assert client_config.setting("missing", 3) == 3
    assert client_config.flag("on") is True
    assert client_config.text("hello", "Hi") == "Привет"


def test_file_change_is_picked_up(config):
    _write(config, {"schema": 1, "flags": {"on": False}})
    assert client_config.flag("on") is False
    import os
    import time
    _write(config, {"schema": 1, "flags": {"on": True}})
    os.utime(config, (time.time() + 5, time.time() + 5))  # mtime точно другой
    assert client_config.flag("on") is True


def test_rules_are_filtered_by_version_and_platform(config):
    _write(config, {"schema": 1, "apk_rejections": [
        {"id": "all", "contains": "A", "message": "m"},
        {"id": "android_only", "contains": "A", "message": "m", "platforms": ["android"]},
        {"id": "newer", "contains": "A", "message": "m", "min_app": "1.0.63"},
        {"id": "older", "contains": "A", "message": "m", "max_app": "1.0.61"},
        {"id": "range", "contains": "A", "message": "m", "min_app": "1.0.60", "max_app": "1.0.62"},
        "мусор",
    ]})
    assert [r["id"] for r in client_config.rules("apk_rejections")] == ["all", "range"]
    assert [r["id"] for r in client_config.view()["apk_rejections"]] == ["all", "range"]


def test_fill_substitutes_only_known_keys():
    assert client_config.fill("«{name}» и {pkg}, {missing}", {"name": "a.apk", "pkg": "ru.x"}) == "«a.apk» и ru.x, {missing}"
    assert client_config.fill("{name.__class__}", {"name": "x"}) == "{name.__class__}"


def test_match_rule_groups_and_broken_regex():
    rule = {"contains": "install_failed_test", "regex": r"package (?P<pkg>[\w.]+) owns (\w+)"}
    values = client_config.match_rule(rule, "Failure [INSTALL_FAILED_TEST: package ru.x owns perm]")
    assert values == {"g1": "ru.x", "g2": "perm", "pkg": "ru.x"}
    assert client_config.match_rule(rule, "Failure [OTHER]") is None
    assert client_config.match_rule({"regex": "(("}, "anything") is None
    assert client_config.match_rule({"message": "no matcher"}, "anything") is None


def test_server_rule_comes_first_and_builtin_stays(config):
    _write(config, {"schema": 1, "apk_rejections": [
        {"id": "new", "contains": "INSTALL_FAILED_VERIFICATION_FAILURE",
         "message": "«{name}» не прошёл проверку магнитолы — не встанет никаким способом."},
        {"id": "override", "contains": "INSTALL_FAILED_OLDER_SDK", "regex": r"sdk version #?(\d+)",
         "message": "«{name}» нужен API {g1}"},
        {"id": "broken", "regex": "((", "message": "никогда"},
        {"id": "empty", "contains": "INSTALL_FAILED_MISSING_SPLIT", "message": "  "},
    ]})
    assert apk_check.rejection_message("a.apk", "Failure [INSTALL_FAILED_VERIFICATION_FAILURE]") == (
        "«a.apk» не прошёл проверку магнитолы — не встанет никаким способом.")
    assert apk_check.rejection_message(
        "b.apk", "Failure [INSTALL_FAILED_OLDER_SDK: Requires newer sdk version #31 (current version is #28)]") == (
        "«b.apk» нужен API 31")
    # Пустой текст у правила — его пропускаем, срабатывает встроенное.
    builtin = apk_check.rejection_message("c.apk", "Failure [INSTALL_FAILED_MISSING_SPLIT]")
    assert builtin and "split" in builtin
    assert apk_check.rejection_message("d.apk", "Failure [INSTALL_FAILED_ABORTED]") is None


def test_refresh_downloads_validates_and_keeps_old_copy(config, content_server):
    content_server.add("config/client.json", json.dumps({"schema": 1, "flags": {"on": True}}).encode("utf-8"))
    assert client_config.refresh(content_server.url) is True
    assert client_config.flag("on") is True
    assert client_config.refresh(content_server.url) is False  # тот же файл — не меняем
    content_server.add("config/client.json", b"{broken")
    assert client_config.refresh(content_server.url) is False
    assert client_config.flag("on") is True  # битый файл с сервера не затирает рабочую копию
    assert client_config.refresh("http://127.0.0.1:9/content", timeout=1) is False
    assert client_config.flag("on") is True
