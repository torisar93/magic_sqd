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
    def __init__(self, tmp_path, apk_paths):
        self.shared_dir = tmp_path / "cars" / "_shared"
        (self.shared_dir / "motion_cert").mkdir(parents=True)
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

    # --- примитивы, которыми пользуется optimize_for_motion ---
    def check_cancelled(self):
        pass

    def log(self, message):
        self.logs.append(message)

    def shell(self, command, check=False, timeout=None):
        self.shell_calls.append(command)
        if command.startswith("pm path "):
            return _Result("\n".join(f"package:{p}" for p in self._apk_paths))
        if command == "dumpsys car_service":
            return _Result(self.car_dump)
        return _Result()

    def pull(self, remote, local, timeout=180):
        Path(local).write_bytes(b"original-apk-bytes")

    def install_apk_auto(self, path, extra_args=None):
        name = Path(path).name
        self.installed.append(name)
        if name in self.install_should_fail:
            raise ic.AppInstallFailed(f"{name}: не встало")

    def _grant_all_permissions_if_available(self, package):
        self.granted.append(package)

    # методы, которые optimize_for_motion берёт с самого InstallContext, — переиспользуем настоящие
    _installed_apk_paths = ic.InstallContext._installed_apk_paths
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


def test_shared_wrapper_delegates_and_warns_on_old_client():
    from importlib import util
    spec = util.spec_from_file_location("adb_permissions_motion",
                                        Path(__file__).resolve().parents[1] / "cars/_shared/adb_permissions.py")
    mod = util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seen = []
    mod.optimize_for_motion(types.SimpleNamespace(optimize_for_motion=lambda pkg: seen.append(pkg)), "com.x")
    assert seen == ["com.x"]
    logs = []
    mod.optimize_for_motion(types.SimpleNamespace(log=logs.append), "com.x")  # старый клиент — метода нет
    assert logs and "обновите программу" in logs[0]
