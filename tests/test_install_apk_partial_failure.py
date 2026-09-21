"""Регрессия на баг из логов #390/#391 (2026-09-20, Geely Atlas New/Monji):

когда способ установки уже залочен (сработал на предыдущих apk этого же
запуска), сбой ОДНОГО файла (gnss-client-v2.10.1.apk — dex-хелпер печатал
"Success", но диф списка пакетов не подтвердил ровно один новый пакет) не
перехватывался вообще и улетал прямо в runner.py — "Ошибка установки: ..."
рушила ВЕСЬ этап, стирая уже успешно поставленные ~10 приложений. Теперь
install_apk_auto поднимает AppInstallFailed, install_selected_apks пропускает
только этот файл и продолжает список, а runner.py честно упоминает пропуски
в итоговом сообщении вместо голого "успешно"."""
from __future__ import annotations
import threading

import pytest

from app.adb_utils import AdbError
from app.install_context import AppInstallFailed, InstallCancelled, InstallContext
from app.runner import InstallRunner


def _make_ctx(tmp_path, apk_names, log):
    apks = []
    for name in apk_names:
        path = tmp_path / name
        path.write_bytes(b"")  # невалидный APK — read_package_name должен тихо вернуть None
        apks.append(path)
    return InstallContext(
        adb_path="fake-adb",
        device_serial="fake-device",
        model_dir=tmp_path,
        selected_apks=apks,
        log_fn=log.append,
        cancel_flag=threading.Event(),
        shared_dir=None,  # выдача разрешений становится no-op — не мешает тесту
    ), apks


def test_locked_method_failure_raises_app_install_failed(tmp_path):
    """install_apk_auto: способ залочен на первом apk; второй вызов, падающий
    с AdbError, не должен вести себя иначе, чем AppInstallFailed — и метод
    должен остаться залоченным (третий apk не должен заново перебирать всё)."""
    log = []
    ctx, apks = _make_ctx(tmp_path, ["a.apk"], log)
    calls = []

    def fake_install(method, path, extra_args):
        calls.append((method, path.name))
        if path.name == "a.apk":
            return  # первый вызов — успех, локает способ 0
        raise AdbError('monji: session=1 flags=0x116\nmonji: wrote 42 bytes\nSuccess')

    ctx._install_with_method = fake_install

    ctx.install_apk_auto(apks[0])  # локает self._install_method
    assert ctx._install_method == 0

    with pytest.raises(AppInstallFailed) as exc_info:
        ctx.install_apk_auto(tmp_path / "b.apk")
    assert "b.apk" in str(exc_info.value)
    assert ctx._install_method == 0  # способ остаётся залоченным для следующих файлов
    assert any("не сработало" in line for line in log)


def test_install_selected_apks_skips_one_and_continues(tmp_path):
    """install_selected_apks: 3 apk, второй падает после лока способа — первый
    и третий должны получить пост-обработку (_after_app_installed), второй —
    нет; ctx.failed_apps должен содержать ровно один пропуск, а этап не
    должен прерваться исключением."""
    log = []
    ctx, apks = _make_ctx(tmp_path, ["a.apk", "b.apk", "c.apk"], log)

    def fake_install(method, path, extra_args):
        if path.name == "b.apk":
            raise AdbError("Success")  # диф не совпал, хотя хелпер отчитался об успехе
        return

    ctx._install_with_method = fake_install
    processed = []
    ctx._after_app_installed = lambda apk, mock: processed.append(apk.name)

    ctx.install_selected_apks()  # не должно бросить исключение

    assert processed == ["a.apk", "c.apk"]
    assert len(ctx.failed_apps) == 1
    assert "b.apk" in ctx.failed_apps[0]
    assert any("Не установлено" in line and "b.apk" in line for line in log)


def test_install_selected_apks_all_succeed_no_failures(tmp_path):
    """Без сбоев failed_apps должен остаться пустым и лог не должен упоминать пропуски."""
    log = []
    ctx, apks = _make_ctx(tmp_path, ["a.apk", "b.apk"], log)
    ctx._install_with_method = lambda method, path, extra_args: None
    ctx._after_app_installed = lambda apk, mock: None

    ctx.install_selected_apks()

    assert ctx.failed_apps == []
    assert not any("Не установлено" in line for line in log)


def test_version_downgrade_still_cancels_whole_run(tmp_path):
    """Понижение версии — не AppInstallFailed: это по-прежнему InstallCancelled
    (регресс-проверка, что новый except AdbError не перехватил лишнего)."""
    from app.install_context import VersionDowngradeError

    log = []
    ctx, apks = _make_ctx(tmp_path, ["a.apk"], log)
    ctx._install_with_method = lambda method, path, extra_args: None
    ctx.install_apk_auto(apks[0])  # локает способ

    def fake_fail(method, path, extra_args):
        raise VersionDowngradeError("INSTALL_FAILED_VERSION_DOWNGRADE")

    ctx._install_with_method = fake_fail
    with pytest.raises(InstallCancelled):
        ctx.install_apk_auto(tmp_path / "b.apk")


def test_runner_reports_partial_failure_in_final_message(tmp_path):
    """runner.py: итоговое сообщение этапа должно упомянуть пропуски из
    ctx.failed_apps, а не просто "Установка завершена успешно.", когда
    install_selected_apks пропустил хотя бы один apk."""
    results = []
    runner = InstallRunner(adb_path="fake-adb", on_log=lambda m: None,
                            on_finished=lambda ok, msg: results.append((ok, msg)))

    class FakeModel:
        dir = tmp_path

    def run_fn(ctx):
        ctx.failed_apps.append("x.apk: причина")

    runner._run(FakeModel(), "fake-device", [], run_fn, [], "", True)

    assert len(results) == 1
    ok, message = results[0]
    assert ok is True
    assert "x.apk: причина" in message
    assert "не всё встало" in message


def test_runner_reports_plain_success_without_failures(tmp_path):
    """Без пропусков сообщение должно остаться прежним — регресс-проверка,
    что обычный путь не поменялся."""
    results = []
    runner = InstallRunner(adb_path="fake-adb", on_log=lambda m: None,
                            on_finished=lambda ok, msg: results.append((ok, msg)))

    class FakeModel:
        dir = tmp_path

    runner._run(FakeModel(), "fake-device", [], lambda ctx: None, [], "", True)

    assert results == [(True, "Установка завершена успешно.")]
