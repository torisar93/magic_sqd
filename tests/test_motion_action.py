"""Кнопка «Разрешить работу в движении» в «Доп. действиях» (владелец, 2026-09-30: Haval Dargo 2026, видео в
движении). Генератор пишет вызов optimize_for_motion в install.py; InstallContext.optimize_for_motion снимает APK,
помечает его (motion_patch), переподписывает и ставит заново, а при неудаче возвращает исходный."""
from __future__ import annotations
import types
from pathlib import Path

import pytest

from app import car_generator as cg
from app import install_context as ic


def _render(action_kind: str) -> str:
    spec = cg.NewCarSpec(
        brand="Haval", model="Dargo", modification="2026", wifi=False,
        steps=[cg.StepSpec(type="actions", title="Доп. кнопки", actions=[
            cg.ActionSpec(label="Разрешить работу в движении", kind=action_kind)])])
    return cg._render_install_py(spec)


def test_generator_wires_the_motion_button():
    code = _render("motion_optimize")
    assert "from adb_permissions import optimize_for_motion" in code
    assert "from adb_permissions import disable_app" in code  # list_installed_packages для выбора приложения
    assert "    packages = list_installed_packages(ctx)" in code
    assert "    optimize_for_motion(ctx, package)" in code
    compile(code, "install.py", "exec")  # синтаксически валиден


def test_generator_does_not_import_motion_when_no_button():
    assert "optimize_for_motion" not in _render("grant_permissions")


class _Result:
    def __init__(self, stdout="", stderr=""):
        self.stdout, self.stderr, self.returncode = stdout, stderr, 0


class _FakeCtx:
    """Минимальный двойник InstallContext для optimize_for_motion: записывает shell/pull/установку."""
    def __init__(self, tmp_path, apk_paths, with_cert=True):
        self.shared_dir = tmp_path / "cars" / "_shared"
        (self.shared_dir / "motion_cert").mkdir(parents=True)
        if with_cert:
            (self.shared_dir / "motion_cert" / "private.pk8").write_bytes(b"key")
            (self.shared_dir / "motion_cert" / "certificate.crt").write_bytes(b"cert")
        self._apk_paths = apk_paths
        self._install_method = None
        self._granted_packages = set()
        self.logs = []
        self.shell_calls = []
        self.installed = []
        self.granted = []
        self.verdicts = 0
        self.install_should_fail = []  # список путей (по имени), установка которых должна упасть
        self.car_dump = "**System allowlist**\n  com.example.video\n**end**"
        self._prestaged = {}
        self.pushes = []          # (имя файла, путь на магнитоле)
        self.push_should_fail = False
        self.cp_should_fail = False
        self.staged_at_install = []  # что было «уже на магнитоле» в момент установки

    # --- примитивы, которыми пользуется optimize_for_motion ---
    def check_cancelled(self):
        pass

    def log(self, message):
        self.logs.append(message)

    def shell(self, command, check=False, timeout=None):
        self.shell_calls.append(command)
        if ic._STAGED_MARK in command:
            return _Result("" if self.cp_should_fail else ic._STAGED_MARK)
        if command.startswith("pm path "):
            return _Result("\n".join(f"package:{p}" for p in self._apk_paths))
        if command == "dumpsys car_service":
            return _Result(self.car_dump)
        return _Result()

    def pull(self, remote, local, timeout=180):
        Path(local).write_bytes(b"original-apk-bytes")

    def push(self, local, remote, timeout=180):
        if self.push_should_fail:
            raise ic.AdbError("adb: error: closed")
        self.pushes.append((Path(local).name, remote))

    def install_apk_auto(self, path, extra_args=None):
        name = Path(path).name
        self.installed.append(name)
        self.staged_at_install.append(dict(self._prestaged))
        if name in self.install_should_fail:
            raise ic.AppInstallFailed(f"{name}: не встало")

    def _grant_all_permissions_if_available(self, package):
        self.granted.append(package)

    # методы, которые optimize_for_motion берёт с самого InstallContext, — переиспользуем настоящие
    _installed_apk_paths = ic.InstallContext._installed_apk_paths
    _drop_motion_staging = ic.InstallContext._drop_motion_staging
    _log_motion_verdict = ic.InstallContext._log_motion_verdict
    optimize_for_motion = ic.InstallContext.optimize_for_motion


@pytest.fixture
def patched_signing(monkeypatch):
    def fake_patch(src, dst):
        Path(dst).write_bytes(b"patched-apk")
        return 3  # пометили 3 окна
    monkeypatch.setattr("app.motion_patch.patch_apk", fake_patch)

    def fake_resign(base_dir, apk_path, cert_dir, out_path, timeout=60):
        Path(out_path).write_bytes(b"signed-apk")
    monkeypatch.setattr("app.apk_signer.resign_apk", fake_resign)


def test_single_apk_is_patched_resigned_and_reinstalled(tmp_path, patched_signing):
    ctx = _FakeCtx(tmp_path, ["/data/app/com.example.video/base.apk"])
    ctx.optimize_for_motion("com.example.video")
    assert any(c == "pm uninstall com.example.video" for c in ctx.shell_calls)  # старое удалили
    assert ctx.installed == ["com.example.video.signed.apk"]  # поставили помеченную
    assert ctx.granted == ["com.example.video"]  # разрешения выдали заново
    assert any("разрешены в движении" in m for m in ctx.logs)  # магнитола приняла (allowlist)
    uninstall_at = next(i for i, c in enumerate(ctx.shell_calls) if c == "pm uninstall com.example.video")
    dump_at = next(i for i, c in enumerate(ctx.shell_calls) if c == "dumpsys car_service")
    assert uninstall_at < dump_at  # проверка — уже после переустановки


def test_new_version_is_on_the_head_unit_before_the_old_one_is_removed(tmp_path, patched_signing):
    # Лог №2784 (Android, Strelka HUD на Haval H3 по Wi-Fi): старое сняли, а связь оборвалась на заливке новой —
    # приложение пропало. Теперь заливка и копия исходного — до снятия, установка и возврат — с диска магнитолы.
    ctx = _FakeCtx(tmp_path, ["/data/app/com.example.video/base.apk"])
    ctx.optimize_for_motion("com.example.video")
    assert ctx.pushes == [("com.example.video.signed.apk", "/data/local/tmp/com.example.video.motion.apk")]
    backup = next(i for i, c in enumerate(ctx.shell_calls) if c.startswith("cp /data/app/com.example.video/base.apk "))
    uninstall = ctx.shell_calls.index("pm uninstall com.example.video")
    assert backup < uninstall
    assert ctx.staged_at_install[0] == {str(tmp_path / "motion_cache/com.example.video.signed.apk"): "/data/local/tmp/com.example.video.motion.apk",
                                        str(tmp_path / "motion_cache/com.example.video.apk"): "/data/local/tmp/com.example.video.orig.apk"}
    assert ctx._prestaged == {}  # после — не путает следующие установки
    assert ("rm -f /data/local/tmp/com.example.video.motion.apk /data/local/tmp/com.example.video.orig.apk"
            in ctx.shell_calls[uninstall:])  # прибрали за собой


@pytest.mark.parametrize("what", ["push", "cp"])
def test_staging_failure_leaves_the_app_untouched(tmp_path, patched_signing, what):
    ctx = _FakeCtx(tmp_path, ["/data/app/com.example.video/base.apk"])
    setattr(ctx, f"{what}_should_fail", True)
    ctx.optimize_for_motion("com.example.video")
    assert not any(c.startswith("pm uninstall") for c in ctx.shell_calls) and not ctx.installed
    assert any("оставлено как было" in m for m in ctx.logs)


def test_prestaged_file_is_copied_and_installed_on_the_head_unit(tmp_path):
    calls = []

    class _Adb:
        def shell(self, command, check=True, timeout=120):
            calls.append(command)
            return _Result(ic._STAGED_MARK if ic._STAGED_MARK in command else "Success")

        def push(self, *args, **kwargs):
            raise AssertionError("по сети не заливаем — файл уже на магнитоле")

        def install(self, *args, **kwargs):
            raise AssertionError("adb install тянул бы файл с компьютера")

    ctx = ic.InstallContext.__new__(ic.InstallContext)
    ctx._adb, ctx._prestaged, ctx.log = _Adb(), {"/pc/app.signed.apk": "/data/local/tmp/app.motion.apk"}, lambda m: None
    ctx.check_cancelled = lambda: None
    ctx.push("/pc/app.signed.apk", "/sdcard/Download/app.signed.apk")
    ctx.install_apk("/pc/app.signed.apk", extra_args=["-g"])
    assert calls == [f"cp /data/local/tmp/app.motion.apk /sdcard/Download/app.signed.apk && echo {ic._STAGED_MARK}",
                     "pm install -r -g /data/local/tmp/app.motion.apk"]


def test_split_apk_is_refused(tmp_path, patched_signing):
    ctx = _FakeCtx(tmp_path, ["/data/app/x/base.apk", "/data/app/x/split_config.arm64.apk"])
    ctx.optimize_for_motion("com.example.video")
    assert not ctx.installed and not any(c.startswith("pm uninstall") for c in ctx.shell_calls)
    assert any("нескольких частей (split" in m for m in ctx.logs)


def test_not_installed_is_reported(tmp_path, patched_signing):
    ctx = _FakeCtx(tmp_path, [])
    ctx.optimize_for_motion("com.example.video")
    assert not ctx.installed
    assert any("не установлено" in m for m in ctx.logs)


def test_failed_install_restores_the_original(tmp_path, patched_signing):
    ctx = _FakeCtx(tmp_path, ["/data/app/com.example.video/base.apk"])
    ctx.install_should_fail = ["com.example.video.signed.apk"]  # помеченная не встаёт
    ctx.optimize_for_motion("com.example.video")
    assert ctx.installed == ["com.example.video.signed.apk", "com.example.video.apk"]  # вернули исходную
    assert any("Возвращаю исходную" in m or "возвращ" in m.lower() for m in ctx.logs)
    assert any("не включена" in m for m in ctx.logs)


def test_already_marked_app_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("app.motion_patch.patch_apk", lambda src, dst: 0)  # менять нечего
    ctx = _FakeCtx(tmp_path, ["/data/app/com.example.video/base.apk"])
    ctx.optimize_for_motion("com.example.video")
    assert not ctx.installed and not any(c.startswith("pm uninstall") for c in ctx.shell_calls)
    assert any("уже все окна помечены" in m for m in ctx.logs)


def _serve_cert(server, tmp_path):
    server.add("cars/_shared/motion_cert/private.pk8", b"server-key")
    server.add("cars/_shared/motion_cert/certificate.crt", b"server-cert")
    server.write_manifest()
    (tmp_path / "server.json").write_text('{"base_url": "%s"}' % server.url, encoding="utf-8")


def test_missing_cert_is_downloaded_before_patching(tmp_path, content_server, patched_signing):
    # Лог №1985 (Dargo, 1.0.53): ключ лежит в подпапке cars/_shared, программа его не скачивала — кнопка сразу
    # писала «нет ключа подписи». Теперь, если ключа нет, кнопка докачивает его сама и работает дальше.
    _serve_cert(content_server, tmp_path)
    ctx = _FakeCtx(tmp_path, ["/data/app/com.example.video/base.apk"], with_cert=False)
    ctx.optimize_for_motion("com.example.video")
    assert (ctx.shared_dir / "motion_cert" / "private.pk8").read_bytes() == b"server-key"
    assert ctx.installed == ["com.example.video.signed.apk"]


def test_missing_cert_without_internet_says_so(tmp_path, patched_signing):
    ctx = _FakeCtx(tmp_path, ["/data/app/com.example.video/base.apk"], with_cert=False)  # server.json нет
    ctx.optimize_for_motion("com.example.video")
    assert not ctx.installed and not any(c.startswith("pm uninstall") for c in ctx.shell_calls)
    assert any("нет ключа подписи" in m and "интернет" in m for m in ctx.logs)


def _load_shared_wrapper():
    from importlib import util
    spec = util.spec_from_file_location("adb_permissions_motion",
                                        Path(__file__).resolve().parents[1] / "cars/_shared/adb_permissions.py")
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_shared_wrapper_fetches_cert_for_old_desktop(tmp_path, content_server):
    # ПК 1.0.53 сам ключ не докачивает — это делает обёртка из cars/_shared (приходит с сервера при запуске).
    _serve_cert(content_server, tmp_path)
    shared = tmp_path / "cars" / "_shared"
    shared.mkdir(parents=True)
    seen = []
    ctx = types.SimpleNamespace(shared_dir=shared, log=lambda m: None,
                                optimize_for_motion=lambda pkg: seen.append(
                                    (pkg, (shared / "motion_cert" / "certificate.crt").is_file())))
    _load_shared_wrapper().optimize_for_motion(ctx, "com.x")
    assert seen == [("com.x", True)]  # к вызову программы ключ уже на месте


def test_shared_wrapper_delegates_and_warns_on_old_client():
    mod = _load_shared_wrapper()
    seen = []
    mod.optimize_for_motion(types.SimpleNamespace(optimize_for_motion=lambda pkg: seen.append(pkg)), "com.x")
    assert seen == ["com.x"]
    logs = []
    mod.optimize_for_motion(types.SimpleNamespace(log=logs.append), "com.x")  # старый клиент — метода нет
    assert logs and "обновите программу" in logs[0]


ANDROID = Path(__file__).resolve().parents[1] / "android/app/src/main/java/ru/magicsqd/mobile/usb"


def test_android_removes_the_app_only_after_the_new_version_is_on_the_head_unit():
    # Тот же порядок, что на ПК (лог №2784 — Android): заливка помеченной версии и копия исходного на магнитоле — до
    # pm uninstall; установка и возврат — с её диска (preStaged), без заливки по сети после снятия.
    code = (ANDROID / "MotionOptimize.kt").read_text(encoding="utf-8")
    run = code[code.index("fun run("):code.index("private fun installedApkPaths(")]
    push = run.index("AdbSession.push(PushSource.of(signed), signedRemote, log)")
    backup = run.index("cp '${paths[0]}' $originalRemote")
    uninstall = run.index('AdbSession.shell("pm uninstall $pkg", log, 120000)')
    assert push < backup < uninstall
    assert "preStaged = mapOf(signed.absolutePath to signedRemote)" in run[uninstall:]
    assert "preStaged = mapOf(original.absolutePath to originalRemote)" in run[uninstall:]
    assert 'val signedRemote = "/data/local/tmp/" + stagedNameFor(signed.name)' in run  # тот же путь, что берёт dex-хелпер
    engine = (ANDROID / "InstallEngine.kt").read_text(encoding="utf-8")
    assert "var stagedValid = preStagedRemote != null" in engine  # заранее залитое не заливается заново
    assert "if (preStagedRemote == null) stagedValid = false" in engine  # и после обрыва связи считается целым
    assert "val stagedName = stagedNameFor(file.name)" in engine
