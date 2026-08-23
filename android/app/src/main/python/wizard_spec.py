"""Разбор _wizard_spec.json (декларативное описание этапов установки,
которое пишет desktop-мастер "Добавить машину...", см. app/car_generator.py)
в нормализованный JSON-совместимый вид для мобильного мастера установки.

Desktop сам _wizard_spec.json на установке НЕ читает — он всегда исполняет
сгенерированные из него stages.py/install.py (произвольный Python,
непортируемый). Но сам _wizard_spec.json достаточно декларативен (мини-DSL
ADB-команд вместо кода), чтобы интерпретировать его напрямую поверх уже
готового Kotlin-транспорта (UsbAdbTransport/AdbInstall) — этим и занимается
этот модуль. Регулярки/семантика команд ниже — порт app/car_generator.py
(_parse_adb_line/_render_command_body), должны разбирать те же строки так же.
Модели БЕЗ _wizard_spec.json (написанные руками) этим модулем не покрываются
— мобильный мастер для них должен явно показать "не поддерживается"."""
import json
import re
from pathlib import Path

SPEC_FILENAME = "_wizard_spec.json"

# Типы этапов, для которых Kotlin-сторона реально умеет что-то выполнять в
# этой версии мобильного приложения — остальные (exe/uart) рендерятся как
# "пока не поддерживается на телефоне" с возможностью пропустить этап.
# uart не поддержан сознательно — нужен физический доступ к serial-порту
# через USB-to-serial адаптер (usb-serial-for-android или аналог), отдельная
# от ADB-транспорта область USB Host API, не сделано.
EXECUTABLE_STAGE_TYPES = ("adb", "apps", "manual", "check", "instruction", "actions", "usb", "telnet")

_ADB_SLEEP_RE = re.compile(r"^#sleep\s+([\d.]+)\s*$", re.IGNORECASE)
_ADB_REBOOT_RE = re.compile(r"^#reboot\s*$", re.IGNORECASE)
_ADB_REBOOT_NOWAIT_RE = re.compile(r"^#reboot_nowait\s*$", re.IGNORECASE)
_ADB_WAIT_DEVICE_RE = re.compile(r"^#wait_device(?:\s+([\d.]+))?\s*$", re.IGNORECASE)
_ADB_ASK_RE = re.compile(r"^#ask\s+(.+)$", re.IGNORECASE)
_ADB_ROOT_RE = re.compile(r"^#root\s*$", re.IGNORECASE)
_ADB_DISABLE_VERITY_RE = re.compile(r"^#disable_verity\s*$", re.IGNORECASE)
_ADB_REMOUNT_RE = re.compile(r"^#remount\s*$", re.IGNORECASE)
_ADB_PUSH_RE = re.compile(r"^#push\s+(\S+)\s+(.+)$", re.IGNORECASE)
_ADB_INSTALL_RE = re.compile(r"^#install\s+(\S+)\s*$", re.IGNORECASE)
_ADB_INSTALL_STREAM_RE = re.compile(r"^#install_stream\s+(\S+)\s*$", re.IGNORECASE)

_DEV = r"(?:\s+-s\s+\S+)?"
_RAW_ADB_ROOT_RE = re.compile(rf"^adb{_DEV}\s+root\s*$", re.IGNORECASE)
_RAW_ADB_DISABLE_VERITY_RE = re.compile(rf"^adb{_DEV}\s+disable-verity\s*$", re.IGNORECASE)
_RAW_ADB_REMOUNT_RE = re.compile(rf"^adb{_DEV}\s+remount\s*$", re.IGNORECASE)
_RAW_ADB_REBOOT_RE = re.compile(rf"^adb{_DEV}\s+reboot\s*$", re.IGNORECASE)
_RAW_ADB_WAIT_DEVICE_RE = re.compile(rf"^adb{_DEV}\s+wait-for-device\s*$", re.IGNORECASE)
_RAW_ADB_SHELL_RE = re.compile(rf"^adb{_DEV}\s+shell\s+(.+)$", re.IGNORECASE)
_RAW_ADB_PUSH_RE = re.compile(rf"^adb{_DEV}\s+push\s+(\S+)\s+(\S+)\s*$", re.IGNORECASE)
_RAW_ADB_INSTALL_RE = re.compile(rf"^adb{_DEV}\s+install\s+(?:-[a-zA-Z]+\s+)*(\S+)\s*$", re.IGNORECASE)
_RAW_CAT_PM_INSTALL_STREAM_RE = re.compile(
    r"^cat\s+(?:\S*/)?(\S+)\s*\|\s*pm\s+install\s+-S\s+\d+\s*$", re.IGNORECASE)

_BAT_TIMEOUT_RE = re.compile(r"^TIMEOUT\s+/T\s+([\d.]+)", re.IGNORECASE)
_BAT_NOOP_RE = re.compile(
    r"^(@?echo(\s|\.|$)|cls\s*$|color\s|pause\s*$|rem[:\s]|::|:\w+\s*$)", re.IGNORECASE)


def _adb_basename(local: str) -> str:
    local = local.strip().strip('"')
    return local.replace("\\", "/").rsplit("/", 1)[-1]


def parse_adb_line(line: str) -> dict:
    """Одна строка команд ADB-этапа -> {"kind": ..., ...полезная нагрузка...}."""
    if m := _ADB_SLEEP_RE.match(line):
        return {"kind": "sleep", "seconds": float(m.group(1))}
    if _ADB_REBOOT_RE.match(line):
        return {"kind": "reboot"}
    if _ADB_REBOOT_NOWAIT_RE.match(line):
        return {"kind": "reboot_nowait"}
    if m := _ADB_WAIT_DEVICE_RE.match(line):
        return {"kind": "wait_device", "timeout": float(m.group(1)) if m.group(1) else None}
    if m := _ADB_ASK_RE.match(line):
        return {"kind": "ask", "prompt": m.group(1).strip()}
    if _ADB_ROOT_RE.match(line):
        return {"kind": "root"}
    if _ADB_DISABLE_VERITY_RE.match(line):
        return {"kind": "disable_verity"}
    if _ADB_REMOUNT_RE.match(line):
        return {"kind": "remount"}
    if m := _ADB_PUSH_RE.match(line):
        return {"kind": "push", "file": _adb_basename(m.group(1)), "remote": m.group(2).strip()}
    if m := _ADB_INSTALL_RE.match(line):
        return {"kind": "install", "file": _adb_basename(m.group(1))}
    if m := _ADB_INSTALL_STREAM_RE.match(line):
        return {"kind": "install_stream", "file": _adb_basename(m.group(1))}

    if _RAW_ADB_ROOT_RE.match(line):
        return {"kind": "root"}
    if _RAW_ADB_DISABLE_VERITY_RE.match(line):
        return {"kind": "disable_verity"}
    if _RAW_ADB_REMOUNT_RE.match(line):
        return {"kind": "remount"}
    if _RAW_ADB_REBOOT_RE.match(line):
        return {"kind": "reboot_nowait"}
    if _RAW_ADB_WAIT_DEVICE_RE.match(line):
        return {"kind": "wait_device", "timeout": None}
    if m := _RAW_ADB_PUSH_RE.match(line):
        return {"kind": "push", "file": _adb_basename(m.group(1)), "remote": m.group(2).strip()}
    if m := _RAW_ADB_INSTALL_RE.match(line):
        return {"kind": "install", "file": _adb_basename(m.group(1))}
    if m := _RAW_CAT_PM_INSTALL_STREAM_RE.match(line):
        return {"kind": "install_stream", "file": _adb_basename(m.group(1))}
    if m := _RAW_ADB_SHELL_RE.match(line):
        return {"kind": "shell", "command": m.group(1).strip()}
    if m := _BAT_TIMEOUT_RE.match(line):
        return {"kind": "sleep", "seconds": float(m.group(1))}
    if _BAT_NOOP_RE.match(line):
        return {"kind": "skip"}

    return {"kind": "shell", "command": line}


def parse_commands(commands) -> list:
    return [parse_adb_line(line) for line in (commands or []) if line and line.strip()]


def _apk_filename(item) -> str:
    """Элемент standard_apks/standard_apks_optional — старый формат (голая
    строка-имя файла) ИЛИ новый (словарь {"filename","name","description"},
    см. app/car_generator.py: _apk_entry_to_json/_parse_apk_entry — тот же
    дуализм форматов, портирован сюда, иначе модели с "красивыми"
    именами/описаниями APK (сохранённые через редактор) падали бы с
    TypeError при разборе на телефоне."""
    return item["filename"] if isinstance(item, dict) else item


def _variant_dicts(variant_data, base_dir: Path, step_type: str) -> list:
    variants = []
    for v in variant_data or []:
        name = v.get("name", "")
        v_dir = base_dir / name
        if step_type == "usb":
            variants.append({
                "name": name,
                "usb_files": [str(v_dir / n) for n in v.get("usb_files", [])],
            })
        elif step_type == "apps":
            variants.append({
                "name": name,
                "standard_apks": [str(v_dir / "required" / _apk_filename(n)) for n in v.get("standard_apks", [])],
                "standard_apks_optional": [str(v_dir / "optional" / _apk_filename(n))
                                            for n in v.get("standard_apks_optional", [])],
            })
    return variants


_IMG_SRC_RE = re.compile(r'(<img[^>]*\bsrc=")(?!https?://)([^"]+)(")', re.IGNORECASE)


def _rewrite_instruction_images(html: str, instr_dir: Path, files_root: Path) -> str:
    """Переписывает относительные <img src="images/...">  на абсолютные
    https://appassets.androidplatform.net/data/... — origin, из которого
    WebView реально отдаёт файлы приложения (см. MainActivity.kt:
    WebViewAssetLoader, InternalStoragePathHandler("/data/", filesDir)).
    Без этого картинки в instruction.html биты — HTML вставляется через
    innerHTML в страницу с ДРУГИМ origin (assets/index.html), относительные
    пути резолвятся от НЕЁ, а не от исходного instruction.html на диске."""
    try:
        rel = instr_dir.relative_to(files_root).as_posix()
    except ValueError:
        return html  # instr_dir не под files_root — рассинхрон путей, не трогаем
    base = f"https://appassets.androidplatform.net/data/{rel}/"
    return _IMG_SRC_RE.sub(lambda m: f"{m.group(1)}{base}{m.group(2)}{m.group(3)}", html)


def load_wizard_spec(model_dir: Path, files_root: Path | None = None):
    """cars/<...>/<модель>/_wizard_spec.json -> нормализованный список этапов
    (список dict'ов, JSON-совместимо) с УЖЕ РАЗРЕШЁННЫМИ путями к файлам
    (files/pack*, usb_files/step_N, files/adb_N, files/exe_N) — но сами файлы
    физически на диске могут ещё не быть скачаны (см. content_sync.
    sync_model_payload — должен быть вызван до попытки исполнения этапов
    apps/usb/adb с прикреплёнными файлами). files_root (обычно
    context.filesDir с Kotlin-стороны) — только для переписывания картинок в
    instruction.html (см. _rewrite_instruction_images), можно не передавать,
    тогда картинки останутся с относительными (битыми) путями. Возвращает
    None, если файла нет или он повреждён (модель не создавалась мастером —
    вызывающий код должен показать "не поддерживается")."""
    spec_path = model_dir / SPEC_FILENAME
    if not spec_path.exists():
        return None
    try:
        data = json.loads(spec_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    files_dir = model_dir / "files"
    usb_root = model_dir / "usb_files"
    steps = []
    apps_index = 0
    for i, step_data in enumerate(data.get("steps", []), start=1):
        step_type = step_data.get("type", "manual")
        variant_data = step_data.get("variants") or []
        usb_files: list = []
        standard_apks: list = []
        standard_apks_optional: list = []
        exe_file = None
        variants: list = []

        if step_type == "usb":
            usb_step_dir = usb_root / f"step_{i}"
            if variant_data:
                variants = _variant_dicts(variant_data, usb_step_dir, "usb")
            else:
                usb_files = [str(usb_step_dir / name) for name in step_data.get("usb_files", [])]
        elif step_type == "apps":
            apps_index += 1
            pack_dir = files_dir / ("pack" if apps_index == 1 else f"pack_{apps_index}")
            if variant_data:
                variants = _variant_dicts(variant_data, pack_dir, "apps")
            else:
                standard_apks = [str(pack_dir / "required" / _apk_filename(item))
                                  for item in step_data.get("standard_apks", [])]
                standard_apks_optional = [
                    str(pack_dir / "optional" / _apk_filename(item))
                    for item in step_data.get("standard_apks_optional", [])]
        elif step_type == "exe" and step_data.get("exe_file"):
            exe_file = str(files_dir / f"exe_{i}" / step_data["exe_file"])

        adb_files = []
        if step_type in ("adb",):
            adb_files = [str(files_dir / f"adb_{i}" / name) for name in step_data.get("adb_files", [])]

        instruction_html = ""
        if step_type == "instruction":
            instr_dir = files_dir / f"instruction_{i}"
            instr_path = instr_dir / "instruction.html"
            try:
                instruction_html = instr_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                instruction_html = ""
            if instruction_html and files_root is not None:
                instruction_html = _rewrite_instruction_images(instruction_html, instr_dir, files_root)

        actions = [
            {
                "label": a.get("label", ""),
                "kind": a.get("kind", "command"),
                "commands": parse_commands(a.get("commands")),
                # Прикреплённые файлы ОДНОГО действия (см. app/car_generator.py:
                # ActionSpec.files, "ADB-команды" — бывший отдельный тип "adb"
                # слит с actions, теперь умеет #push/#install и здесь тоже) —
                # "actions_{i}_{j}", то же имя папки, что и на desktop
                # (_write_model_files/_render_command_body), должно совпадать
                # буквально.
                "files": [str(files_dir / f"actions_{i}_{j}" / name) for name in a.get("files", [])],
            }
            for j, a in enumerate(step_data.get("actions", []), start=1)
        ]

        steps.append({
            "index": i - 1,
            "type": step_type,
            "supported": step_type in EXECUTABLE_STAGE_TYPES,
            "title": step_data.get("title", ""),
            "description": step_data.get("description", ""),
            "instruction_html": instruction_html,
            "usb_files": usb_files,
            "usb_copy_selected_apks": step_data.get("usb_copy_selected_apks", False),
            "usb_apks_dest": step_data.get("usb_apks_dest", ""),
            "usb_shared_folder": step_data.get("usb_shared_folder", ""),
            # "adb" — через мини-DSL (см. parse_commands). "uart"/"telnet" —
            # НЕ через мини-DSL (нет #push/#install и т.п. — desktop шлёт
            # каждую строку как есть: по UART "как есть + \r\n", по telnet —
            # каждая отдельным вызовом enable_adb_via_telnet(command=строка),
            # см. car_generator.py:_render_install_py) — просто список сырых
            # строк, не список {"kind":...} словарей.
            "commands": (
                parse_commands(step_data.get("commands")) if step_type == "adb"
                else list(step_data.get("commands") or []) if step_type in ("uart", "telnet")
                else []
            ),
            "adb_install_selected_apks": step_data.get("adb_install_selected_apks", False),
            "adb_files": adb_files,
            "standard_apks": standard_apks,
            "standard_apks_optional": standard_apks_optional,
            "exe_file": exe_file,
            "check_var": step_data.get("check_var", ""),
            "check_options": step_data.get("check_options", []),
            "condition_var": step_data.get("condition_var", ""),
            "condition_values": step_data.get("condition_values", []),
            "variants": variants,
            "uart_baudrate": step_data.get("uart_baudrate", 115200),
            "actions": actions,
        })

    return {
        "wifi": data.get("wifi", False),
        "wifi_port": data.get("wifi_port", 5555),
        "steps": steps,
    }
