"""Общие куски интерфейса ПК и Android — одинаковые файлы в двух деревьях
(app/web/frontend/ и android/app/src/main/assets/): окно выполнения этапа,
кольцо установки и вкладки этапа «Приложения». Владелец: «должно быть всё
одинаково на всех платформах» (2026-09-23) — правка только одной копии
тихо разводила бы платформы, поэтому копии обязаны совпадать побайтово."""
from __future__ import annotations
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "app/web/frontend"
ANDROID = ROOT / "android/app/src/main/assets"

SHARED = [
    ("js/components/stage_run.js", "js/stage_run.js"),
    ("css/stage_run.css", "css/stage_run.css"),
    ("js/progress08.js", "js/progress08.js"),
    ("css/progress08.css", "css/progress08.css"),
    ("js/apps_tabs.js", "js/apps_tabs.js"),
    ("css/apps_tabs.css", "css/apps_tabs.css"),
]


@pytest.mark.parametrize("desktop, android", SHARED)
def test_shared_file_is_identical_on_both_platforms(desktop, android):
    assert (DESKTOP / desktop).read_bytes() == (ANDROID / android).read_bytes(), (
        f"{desktop} (ПК) и {android} (Android) разошлись — правьте одну и копируйте в другую")


@pytest.mark.parametrize("index, css, js", [
    (DESKTOP / "index.html", "css/apps_tabs.css", "js/apps_tabs.js"),
    (ANDROID / "index.html", "css/apps_tabs.css", "js/apps_tabs.js"),
])
def test_apps_tabs_is_included_on_both_platforms(index, css, js):
    html = index.read_text(encoding="utf-8")
    assert f'href="{css}"' in html and f'src="{js}"' in html
