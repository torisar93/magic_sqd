"""Список «Спасибо вам» на клиенте (app/supporters_client.py): чистка данных и устойчивость к сбоям —
в окно «Готово!» не должно попасть ничего, кроме имени и цвета карточки."""
from __future__ import annotations
import json


def test_clean_keeps_only_name_kind_top():
    from app.supporters_client import _clean
    data = {"version": 2, "people": [
        {"name": "  Алексей К. ", "kind": "sub", "top": True, "email": "a@x.ru", "amount": 1500},
        {"name": "", "kind": "don"},                       # без имени — выкидывается
        {"name": "Тимур", "kind": "weird", "top": "yes"},  # неизвестный kind -> don, top не bool -> False
        "мусор", None,
    ]}
    assert _clean(data) == {"people": [
        {"name": "Алексей К.", "kind": "sub", "top": True},
        {"name": "Тимур", "kind": "don", "top": False},
    ]}


def test_clean_rejects_wrong_shapes_and_caps_length():
    from app.supporters_client import _clean, MAX_NAME, MAX_PEOPLE
    assert _clean(None) is None and _clean([]) is None and _clean({"people": "x"}) is None
    long_name = "я" * (MAX_NAME + 20)
    cleaned = _clean({"people": [{"name": long_name}] * (MAX_PEOPLE + 5)})
    assert len(cleaned["people"]) == MAX_PEOPLE and len(cleaned["people"][0]["name"]) == MAX_NAME


def test_fetch_from_content_server(content_server, app_base):
    from app.supporters_client import fetch_supporters
    content_server.add("supporters.json", json.dumps({"people": [{"name": "Иван", "kind": "sub", "top": False}]}).encode("utf-8"))
    assert fetch_supporters(app_base) == {"people": [{"name": "Иван", "kind": "sub", "top": False}]}


def test_fetch_returns_none_without_file_or_server(content_server, app_base, tmp_path):
    from app.supporters_client import fetch_supporters
    assert fetch_supporters(app_base) is None  # 404
    other = tmp_path / "other"
    other.mkdir()
    other.joinpath("server.json").write_text('{"base_url": "http://127.0.0.1:9/content"}', encoding="utf-8")
    assert fetch_supporters(other, timeout=1) is None
    assert fetch_supporters(tmp_path / "nowhere") is None  # нет server.json
