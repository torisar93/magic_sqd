"""Публикация модели из редактора (app/web/api/car_editor_api.py) и заявки (submissions_api.py) — раньше
сначала СТИРАЛА всю опубликованную версию модели на сервере (delete_cars_path), а потом заливала актуальную
(upload_model льёт только слиянием) — любой сбой между этими двумя шагами (обрыв сети, перезапуск/зависание
backend, таймаут прокси) оставлял модель на сервере БЕЗ ФАЙЛОВ ВООБЩЕ (реальный инцидент 2026-09-21: 502 от
nginx посреди публикации Haval Jolion 2026 стёр files/pack/optional/ целиком, включая APK, которые вообще не
менялись). Теперь порядок другой: залить (безопасное слияние) -> сверить с тем, что реально на сервере
(list_cars_path_recursive) -> убрать ПООТДЕЛЬНОСТИ только то, чего больше нет локально (см.
admin_client.py: cleanup_stale_model_files/compute_stale_files). Эти тесты проверяют именно
compute_stale_files (чистая функция diff'а) — сетевую часть (list_cars_path_recursive/delete_cars_path)
подменяем."""
from __future__ import annotations

from app.admin_client import AdminClientError, cleanup_stale_model_files, compute_stale_files


def test_compute_stale_files_empty_when_everything_matches():
    local = {"install.py", "stages.py", "_wizard_spec.json"}
    server = ["install.py", "stages.py", "_wizard_spec.json"]
    assert compute_stale_files(local, server) == []


def test_compute_stale_files_finds_only_removed_files():
    # Технику убрал APK из обязательных локально — на сервере он пока ещё есть.
    local = {"install.py", "files/pack/required/keep.apk"}
    server = ["install.py", "files/pack/required/keep.apk", "files/pack/required/removed.apk"]
    assert compute_stale_files(local, server) == ["files/pack/required/removed.apk"]


def test_compute_stale_files_never_touches_new_local_files():
    # Файл, который есть только локально (только что добавлен), не должен считаться "лишним".
    local = {"install.py", "files/pack/required/new.apk"}
    server = ["install.py"]
    assert compute_stale_files(local, server) == []


def test_cleanup_stale_model_files_deletes_only_the_diff(tmp_path, monkeypatch):
    """cleanup_stale_model_files — реальная функция, которую зовут car_editor_api.py/
    submissions_api.py ПОСЛЕ успешной заливки. list_cars_path_recursive/delete_cars_path
    подменены заглушками — проверяем, что удаляется РОВНО disjoint-набор, а не вся модель."""
    model_dir = tmp_path / "model"
    (model_dir / "files" / "pack" / "required").mkdir(parents=True)
    (model_dir / "install.py").write_text("# keep", encoding="utf-8")
    (model_dir / "files" / "pack" / "required" / "keep.apk").write_bytes(b"keep")

    deleted: list[str] = []
    logged: list[str] = []

    def fake_list(base_url, cookie, rel_path):
        assert rel_path == "Brand/Model"
        return ["install.py", "files/pack/required/keep.apk", "files/pack/required/stale.apk"]

    def fake_delete(base_url, cookie, rel_path):
        deleted.append(rel_path)

    monkeypatch.setattr("app.admin_client.list_cars_path_recursive", fake_list)
    monkeypatch.setattr("app.admin_client.delete_cars_path", fake_delete)

    cleanup_stale_model_files("https://x", "cookie", "Brand/Model", model_dir, log=logged.append)

    assert deleted == ["Brand/Model/files/pack/required/stale.apk"]
    assert any("stale.apk" in line for line in logged)


def test_cleanup_stale_model_files_is_not_fatal_on_list_error(tmp_path, monkeypatch):
    """Если сверить список на сервере не удалось (сеть/сессия) — публикация уже прошла
    успешно к этому моменту, поэтому здесь только предупреждение в лог, без исключения."""
    model_dir = tmp_path / "model"
    model_dir.mkdir()

    def fake_list(base_url, cookie, rel_path):
        raise AdminClientError("сессия истекла")

    monkeypatch.setattr("app.admin_client.list_cars_path_recursive", fake_list)
    logged: list[str] = []

    cleanup_stale_model_files("https://x", "cookie", "Brand/Model", model_dir, log=logged.append)  # не бросает

    assert any("сверить" in line for line in logged)


def test_cleanup_stale_model_files_one_delete_failure_does_not_stop_the_rest(tmp_path, monkeypatch):
    model_dir = tmp_path / "model"
    model_dir.mkdir()

    def fake_list(base_url, cookie, rel_path):
        return ["a.apk", "b.apk"]

    deleted: list[str] = []

    def fake_delete(base_url, cookie, rel_path):
        if rel_path.endswith("a.apk"):
            raise AdminClientError("сервер недоступен")
        deleted.append(rel_path)

    monkeypatch.setattr("app.admin_client.list_cars_path_recursive", fake_list)
    monkeypatch.setattr("app.admin_client.delete_cars_path", fake_delete)
    logged: list[str] = []

    cleanup_stale_model_files("https://x", "cookie", "Brand/Model", model_dir, log=logged.append)

    assert deleted == ["Brand/Model/b.apk"]
    assert any("a.apk" in line and "Не удалось" in line for line in logged)
