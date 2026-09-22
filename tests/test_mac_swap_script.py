"""_MAC_SWAP_SCRIPT (app/web/api/update_api.py) — реально исполняем через
/bin/sh на временных папках (не мокаем), потому что это критичный код: ошибка
здесь при настоящем самообновлении технику означала бы "программа пропала".

До правки скрипт переносил данные пользователя (cars/, apk/, ...) ВНУТРЬ
нового бандла — это и ломало codesign-печать сразу после того, как её же
проверяли строчкой раньше (см. main_web.py:get_base_dir, докстринг про
"a sealed resource is missing or invalid" / реальный баг с ADB, не видевшим
USB именно при запуске через Finder). Теперь данные переносятся в отдельную
DATA_DIR (эмулирует ~/Library/Application Support/MagicSQD/), а сам новый
бандл остаётся нетронутым после проверки подписи."""
from __future__ import annotations
import os
import stat
import subprocess
from pathlib import Path

import pytest

from app.web.api.update_api import MAC_EXE_NAME, _MAC_SWAP_SCRIPT


def _make_fake_app(path: Path, exe_name: str = MAC_EXE_NAME, extra_files: dict[str, str] | None = None) -> Path:
    macos = path / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    exe = macos / exe_name
    exe.write_text("#!/bin/sh\necho fake\n")
    exe.chmod(0o755)
    for rel, content in (extra_files or {}).items():
        f = macos / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    return path


def _fake_bin_dir(tmp_path: Path) -> Path:
    """PATH-заглушка на "open" — реальный /usr/bin/open в тестовой песочнице
    попытался бы что-то запустить/показать Finder; скрипту важен только сам
    факт вызова, не результат."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    open_stub = bin_dir / "open"
    open_stub.write_text("#!/bin/sh\necho \"open called: $*\" >> \"$FAKE_OPEN_LOG\"\nexit 0\n")
    open_stub.chmod(0o755)
    return bin_dir


def _run_swap(tmp_path, pid, app, new, data_dir) -> subprocess.CompletedProcess:
    script = tmp_path / "swap.sh"
    script.write_text(_MAC_SWAP_SCRIPT.replace("__EXE__", MAC_EXE_NAME))
    script.chmod(0o755)
    bin_dir = _fake_bin_dir(tmp_path)
    open_log = tmp_path / "open.log"
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["FAKE_OPEN_LOG"] = str(open_log)
    return subprocess.run(
        ["/bin/sh", str(script), str(pid), str(app), str(new), str(data_dir)],
        capture_output=True, text=True, timeout=15, env=env,
    )


def test_swap_replaces_bundle_and_leaves_new_bundle_untouched(tmp_path):
    """Обычная подмена: новый бандл после swap — байт-в-байт тот же набор
    файлов, что и до него (см. докстрин модуля выше — раньше именно это
    нарушалось)."""
    app = _make_fake_app(tmp_path / "MagicSQD.app")
    new = _make_fake_app(tmp_path / "new" / "MagicSQD.app")
    new_files_before = sorted(p.relative_to(new) for p in new.rglob("*"))
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "cars").mkdir()  # уже мигрировали раньше — новых переносов быть не должно

    result = _run_swap(tmp_path, os.getpid() + 100_000, app, new, data_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert app.exists()  # на месте старого пути теперь новый бандл
    new_files_after = sorted(p.relative_to(app) for p in app.rglob("*"))
    assert new_files_after == new_files_before  # ни одного лишнего файла не добавилось
    assert (data_dir / "cars").is_dir()  # то, что уже было в data_dir, не пострадало


def test_swap_migrates_legacy_data_out_of_old_bundle_not_into_new_one(tmp_path):
    """Переход со старой версии: данные лежали в Contents/MacOS/ старого
    бандла — должны переехать в DATA_DIR, а НЕ попасть внутрь нового бандла
    (это и было причиной сломанной подписи)."""
    app = _make_fake_app(tmp_path / "MagicSQD.app", extra_files={
        "cars/Haval/Jolion/install.py": "# car data",
        "client_id.txt": "abc-123",
    })
    new = _make_fake_app(tmp_path / "new" / "MagicSQD.app")
    new_files_before = sorted(p.relative_to(new) for p in new.rglob("*"))
    data_dir = tmp_path / "data"  # ещё не существует — первая миграция

    result = _run_swap(tmp_path, os.getpid() + 100_001, app, new, data_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (data_dir / "cars" / "Haval" / "Jolion" / "install.py").read_text() == "# car data"
    assert (data_dir / "client_id.txt").read_text() == "abc-123"
    new_files_after = sorted(p.relative_to(app) for p in app.rglob("*"))
    assert new_files_after == new_files_before  # новый бандл не тронут перенесёнными файлами
    assert not (app / "Contents" / "MacOS" / "cars").exists()  # и в новом их точно нет


def test_swap_does_not_remigrate_when_data_dir_already_populated(tmp_path):
    """Вторая и все следующие подмены — DATA_DIR уже не пустая, блок переноса
    должен быть no-op (даже если в СТАРОМ бандле снова завалялся какой-то
    файл — например от ручной правки на месте)."""
    app = _make_fake_app(tmp_path / "MagicSQD.app", extra_files={"stray.txt": "should stay in old bundle"})
    new = _make_fake_app(tmp_path / "new" / "MagicSQD.app")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "cars").mkdir()
    (data_dir / "cars" / "marker.txt").write_text("already migrated")

    result = _run_swap(tmp_path, os.getpid() + 100_002, app, new, data_dir)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (data_dir / "cars" / "marker.txt").read_text() == "already migrated"
    assert not (data_dir / "stray.txt").exists()  # ничего нового не перенеслось


def test_swap_rolls_back_when_new_bundle_missing(tmp_path):
    """Если "новый" бандл почему-то исчез (сорвалось скачивание/копирование)
    — старая версия должна остаться на месте и открыться, а не пропасть."""
    app = _make_fake_app(tmp_path / "MagicSQD.app")
    new = tmp_path / "new" / "MagicSQD.app"  # намеренно не существует
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "cars").mkdir()

    result = _run_swap(tmp_path, os.getpid() + 100_003, app, new, data_dir)

    assert app.exists()  # старая версия осталась на месте
    assert (app / "Contents" / "MacOS" / MAC_EXE_NAME).is_file()
    assert not (tmp_path / f"{app.name}.old-update").exists()  # без мусора после отката


# Примечание: "ждёт выхода PID перед подменой" (kill -0 в цикле) этой
# правкой не тронуто и здесь не тестируется — у скрипта там 120 попыток по
# 0.5 с (реальные ~60 с ожидания), а в проекте нет инфраструктуры для
# отдельно помечаемых медленных тестов, чтобы не раздувать обычный прогон.
