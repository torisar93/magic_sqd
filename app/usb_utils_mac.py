"""macOS-реализация usb_utils — список съёмных дисков и форматирование
через diskutil. См. usb_utils.py (диспетчер по sys.platform) и
usb_utils_win.py (эталонная Windows-реализация — тот же публичный
интерфейс: DriveInfo, list_drives, format_drive, UsbSafetyError).

diskutil info -plist принимает ТОЛЬКО точку монтирования/идентификатор
диска, а не произвольный подкаталог (проверено: 'diskutil info /Users'
падает с "Could not find disk: /Users", хотя / — точка монтирования —
работает) — поэтому для произвольного пути (base_dir программы) сначала
поднимаемся к ближайшей точке монтирования (см. _mount_point_for)."""
from __future__ import annotations
import plistlib
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class DriveInfo:
    letter: str  # точка монтирования тома, например "/Volumes/CARINSTALL" —
                 # имя поля сохранено как на Windows: usb_api.py передаёт его
                 # на фронтенд и обратно как непрозрачную строку, не разбирая.
    label: str
    total_bytes: int
    free_bytes: int

    @property
    def display(self):
        size_gb = self.total_bytes / (1024 ** 3)
        label = self.label or "без метки"
        return f"{self.letter}   [{label}]   {size_gb:.1f} ГБ"


class UsbSafetyError(RuntimeError):
    pass


def _diskutil_plist(*args) -> dict:
    """Пустой dict при любой ошибке (нет прав, устройство отвалилось между
    вызовами и т.п.) — вызывающий код относится к этому как "ничего не
    нашли", а не падает (тот же принцип, что и у GetVolumeInformationW на
    Windows — см. usb_utils_win.py). -plist обязательно СРАЗУ после самой
    команды (list/info) — diskutil не принимает его в конце списка
    аргументов (пытается разобрать "-plist" как имя диска и падает с
    "Could not find disk for -plist" — реальный найденный баг, из-за
    которого list_drives() всегда возвращала пустой список)."""
    try:
        result = subprocess.run(["diskutil", args[0], "-plist", *args[1:]], capture_output=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    try:
        return plistlib.loads(result.stdout)
    except Exception:
        return {}


def _mount_point_for(path: Path) -> Path:
    """Ближайшая точка монтирования, содержащая path."""
    path = path.resolve()
    while not path.is_mount() and path != path.parent:
        path = path.parent
    return path


def _whole_disk_for(path: Path) -> str | None:
    """Идентификатор физического диска ("disk4"), на котором лежит path —
    для сравнения "это не системный/не программный диск" (см.
    assert_safe_to_format) и для diskutil eraseDisk (нужен именно ЦЕЛЫЙ
    диск, а не раздел)."""
    info = _diskutil_plist("info", str(_mount_point_for(path)))
    return info.get("ParentWholeDisk") or info.get("DeviceIdentifier")


def list_drives(include_all: bool = False, base_dir: Path | None = None) -> list[DriveInfo]:
    """По умолчанию — только внешние диски (аналог Windows DRIVE_REMOVABLE).
    include_all=True добавляет внутренние физические диски (аналог
    DRIVE_FIXED) — как и на Windows, отличить "настоящий" внутренний диск от
    внешнего, который macOS почему-то не считает external, автоматически не
    всегда возможно, поэтому решение оставлено технику. Системный диск и
    диск, на котором лежит сама программа, не показываются в любом режиме."""
    scopes = ["external"] if not include_all else ["external", "internal"]
    system_disk = _whole_disk_for(Path("/"))
    app_disk = _whole_disk_for(Path(base_dir)) if base_dir is not None else None

    drives = []
    seen_mounts = set()
    for scope in scopes:
        data = _diskutil_plist("list", scope, "physical")
        for disk in data.get("AllDisksAndPartitions", []):
            whole_id = disk.get("DeviceIdentifier", "")
            if whole_id and whole_id in (system_disk, app_disk):
                continue
            for part in disk.get("Partitions", []):
                part_id = part.get("DeviceIdentifier")
                if not part_id:
                    continue
                info = _diskutil_plist("info", part_id)
                mount_point = info.get("MountPoint")
                if not mount_point or mount_point in seen_mounts:
                    continue  # раздел есть, но не примонтирован (носитель не готов) — как на Windows
                try:
                    usage = shutil.disk_usage(mount_point)
                except OSError:
                    continue
                seen_mounts.add(mount_point)
                drives.append(DriveInfo(
                    letter=mount_point,
                    label=info.get("VolumeName") or part.get("VolumeName") or "",
                    total_bytes=usage.total,
                    free_bytes=usage.free,
                ))
    return drives


def assert_safe_to_format(letter: str, base_dir: Path) -> str:
    """letter — точка монтирования тома (см. list_drives). Возвращает
    идентификатор физического диска ("disk4") — format_drive() передаёт его
    в diskutil eraseDisk; наружу (usb_api.py) этот возврат не протекает,
    это внутренний контракт только этого модуля (Windows-версия ничего не
    возвращает — там буква диска не привязана так жёстко к конкретному
    физическому устройству)."""
    info = _diskutil_plist("info", letter)
    whole_disk = info.get("ParentWholeDisk")
    if not whole_disk:
        raise UsbSafetyError(f"Не удалось определить диск для {letter}. Форматирование отменено.")

    system_disk = _whole_disk_for(Path("/"))
    if system_disk and whole_disk == system_disk:
        raise UsbSafetyError("Нельзя форматировать системный диск.")

    try:
        app_disk = _whole_disk_for(Path(base_dir))
    except OSError:
        app_disk = None
    if app_disk and whole_disk == app_disk:
        raise UsbSafetyError("Нельзя форматировать диск, на котором запущена сама программа.")

    return whole_disk


_FS_NAMES = {"FAT32": "FAT32", "exFAT": "ExFAT"}  # см. `diskutil listFilesystems`


def format_drive(letter: str, filesystem: str, label: str, base_dir: Path, log=lambda m: None) -> str:
    """letter — точка монтирования тома, как вернул list_drives(). В отличие
    от Windows (буква диска не меняется после форматирования), diskutil
    eraseDisk стирает ВЕСЬ физический диск и создаёт новый том, который
    монтируется под НОВЫМ путём в /Volumes/<label> — поэтому, в отличие от
    format_drive() в usb_utils_win.py (ничего не возвращает), здесь
    возвращается этот новый путь: вызывающий код (usb_api.py) должен
    копировать файлы именно туда, а не по старому (уже недействительному)
    пути."""
    whole_disk = assert_safe_to_format(letter, base_dir)
    safe_label = "".join(ch for ch in (label or "CARINSTALL") if ch.isalnum())[:11] or "CARINSTALL"
    fs_name = _FS_NAMES.get(filesystem, filesystem)

    log(f"Форматирование {letter} в {filesystem}...")
    try:
        result = subprocess.run(
            ["diskutil", "eraseDisk", fs_name, safe_label, f"/dev/{whole_disk}"],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Форматирование {letter} не завершилось за отведённое время.") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"Не удалось отформатировать {letter}: {detail}")
    log("Форматирование завершено.")

    new_mount = f"/Volumes/{safe_label}"
    # diskutil примонтирует новый том не мгновенно — ждём появления, а не
    # гадаем совпадение вслепую (реального имени тома он тоже может слегка
    # изменить, например добавить суффикс при коллизии, но для нашего
    # только что стёртого диска коллизий не бывает).
    for _ in range(20):
        if Path(new_mount).is_mount():
            return new_mount
        time.sleep(0.5)
    raise RuntimeError(
        f"Диск отформатирован, но не удалось определить точку монтирования нового тома "
        f"(ожидался {new_mount})."
    )
