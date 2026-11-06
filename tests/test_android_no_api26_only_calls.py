"""Android: программа запускается и на магнитолах с Android 7 (minSdk 26, но прошивки бывают с подменёнными данными) —
вызовы, которых нет до Android 8, роняют её NoSuchMethodError, а это не Exception: обычный catch не спасает. Лог №13602
(05.11.2026, Android 7.1.2): File.toPath() в WebBridge.serverApkIcon — вылет на каждом значке приложения."""
from __future__ import annotations
import re
from pathlib import Path

KOTLIN = Path(__file__).resolve().parents[1] / "android/app/src/main/java"


def test_no_file_to_path():
    offenders = []
    for path in KOTLIN.rglob("*.kt"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//")[0]
            if re.search(r"\.toPath\(\)", code):
                offenders.append(f"{path.name}:{n}")
    assert offenders == []
