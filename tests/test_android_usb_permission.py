"""Android 12/12L: запрос разрешения USB (флешка и ADB по проводу — одна функция) падал IllegalArgumentException
«Targeting S+ requires FLAG_IMMUTABLE or FLAG_MUTABLE»: флаг изменяемости PendingIntent ставился только с Android 13,
а обязателен с 12 (логи №1678–1687 — новый техник так и не подключился). Kotlin-тестов в проекте нет —
проверяем исходник."""
from __future__ import annotations
import re
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1] / "android/app/src/main/java/ru/magicsqd/mobile/usb/UsbFlashSpike.kt"


def test_usb_permission_pending_intent_is_mutable_from_android_12():
    text = re.sub(r"/\*.*?\*/", "", SPIKE.read_text(encoding="utf-8"), flags=re.S)
    code = "\n".join(line.split("//")[0] for line in text.splitlines())
    request = re.split(r"\n}(?:\n|$)", code[code.index("fun requestUsbPermission("):])[0]
    flags = re.search(r"val flags = if \((.*?)\) \{\s*PendingIntent\.FLAG_MUTABLE\s*\} else \{\s*0\s*\}", request, re.S)
    assert flags and flags.group(1) == "Build.VERSION.SDK_INT >= Build.VERSION_CODES.S"
    assert "PendingIntent.getBroadcast(context, 0, intent, flags)" in request
    # FLAG_MUTABLE с неявным интентом Android 14 запрещает — интент адресован своему пакету
    assert "Intent(ACTION_USB_PERMISSION).setPackage(context.packageName)" in request
