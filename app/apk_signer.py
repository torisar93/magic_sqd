"""Переподпись APK своим сертификатом (см. app/install_context.py:
InstallContext._maybe_resign) — нужна для платформ, где ГУ проверяет
устанавливаемый APK по конкретному сертификату (совпадение по serial
number, а не по реальной криптографической цепочке доверия — так
устроена проверка на прошивках Changan WutongOS, см. research/Changan/
notes.md) и НЕ пускает "adb install" произвольного APK без такой подписи.

Технически — обычная переподпись через официальный apksigner (схемы
v1+v2+v3 разом, как по умолчанию делает сам инструмент) — тот же самый
Google-инструмент, каким пользуется сторонний платный сервис
(ChanganInstall), а не своя реализация формата APK Signing Block (v2/v3
имеют небанальный бинарный формат — рисковать собственной реализацией
без возможности проверить результат на живой магнитоле не стали).
apksigner.jar сам по себе from Android SDK build-tools, требует только
JRE — вместо полноценного JDK/JRE с установщиком в комплект вшит
минимальный рантайм, собранный через jlink (только java.base+java.logging,
модули, которые реально использует apksigner.jar — см. tools/jre_minimal/,
~31МБ вместо сотен)."""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

from .adb_utils import CREATE_NO_WINDOW


class ApkSignError(RuntimeError):
    pass


def find_java_path(base_dir: Path) -> Path | None:
    """Путь к java.exe внутри вшитого минимального JRE (см. tools/
    jre_minimal/) — None, если почему-то отсутствует (старая установка до
    появления этой функции, или ручной запуск из исходников без tools/)."""
    candidate = base_dir / "tools" / "jre_minimal" / "bin" / "java.exe"
    return candidate if candidate.exists() else None


def find_apksigner_jar(base_dir: Path) -> Path | None:
    candidate = base_dir / "tools" / "apksigner.jar"
    return candidate if candidate.exists() else None


def resign_cert_dir_for_model(model_dir: Path) -> Path | None:
    """cars/<Марка>/<Модель>/files/resign_cert/{private.pk8,certificate.crt}
    — если оба файла на месте, apps-этап автоматически переподписывает
    каждый устанавливаемый APK этим сертификатом ПЕРЕД установкой (см.
    install_context.py: InstallContext._maybe_resign). Никакого отдельного
    поля в редакторе не заводили — конкретной модели либо нужен свой
    сертификат (тогда просто кладут оба файла в её files/), либо нет."""
    d = model_dir / "files" / "resign_cert"
    if (d / "private.pk8").is_file() and (d / "certificate.crt").is_file():
        return d
    return None


def resign_apk(base_dir: Path, apk_path: Path, cert_dir: Path, out_path: Path,
                timeout: int = 60) -> None:
    """Переподписывает apk_path сертификатом из cert_dir (private.pk8 —
    PKCS#8 DER, certificate.crt — X.509 PEM), результат — out_path.
    Бросает ApkSignError с понятным текстом, если JRE/apksigner.jar не
    найдены или сам apksigner вернул ошибку (например файл — не валидный
    zip/APK)."""
    java_path = find_java_path(base_dir)
    if java_path is None:
        raise ApkSignError("Не найден встроенный JRE (tools/jre_minimal) — "
                            "переустановите программу или соберите заново.")
    apksigner_jar = find_apksigner_jar(base_dir)
    if apksigner_jar is None:
        raise ApkSignError("Не найден tools/apksigner.jar — переустановите программу.")

    private_key = cert_dir / "private.pk8"
    certificate = cert_dir / "certificate.crt"

    result = subprocess.run(
        [str(java_path), "-jar", str(apksigner_jar), "sign",
         "--key", str(private_key), "--cert", str(certificate),
         "--out", str(out_path), str(apk_path)],
        capture_output=True, text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW,
    )
    if result.returncode != 0 or not out_path.is_file():
        detail = (result.stdout + result.stderr).strip() or f"код возврата {result.returncode}"
        raise ApkSignError(f"apksigner не смог переподписать {apk_path.name}: {detail}")
