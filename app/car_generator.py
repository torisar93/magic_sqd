"""Генерация файлов новой модели (install.py/stages.py + копирование
инструкции/APK/файлов флешки) для мастера "Добавить машину..."
(см. app/add_car_dialog.py). Модель описывается свободной последовательностью
этапов (StepSpec) — тем же набором типов, что и stages.py, написанный
руками: "apps" (выбор приложений), "usb" (запись на флешку), "adb"
(команды/установка), "manual" (просто инструкция). Генератор только
собирает из них текст файла — сам механизм ("apps"-этап,
load_sibling.load_install, wifi_adb.connect_wifi, UsbContext.usb_file()/
copy_dir()/copy_selected_apks(), ctx.install_selected_apks()) уже есть в
проекте и используется как есть."""
import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import instruction_html

INVALID_NAME_CHARS = set('<>:"/\\|?*')
SPEC_FILENAME = "_wizard_spec.json"


@dataclass
class StepVariant:
    """Один вариант содержимого "usb"/"apps" этапа (например Full/Lite) —
    техник выбирает нужный прямо на этапе установки (см. StepSpec.variants
    ниже). Оба списка файлов есть у любого варианта просто для простоты
    (общий код работы с вариантами в car_generator.py/add_car_dialog.py не
    зависит от типа этапа) — реально используется только тот, что подходит
    типу этапа-владельца."""
    name: str = ""
    usb_files: list[Path] = field(default_factory=list)
    standard_apks: list[Path] = field(default_factory=list)


@dataclass
class StepSpec:
    type: str  # "usb" | "manual" | "adb" | "apps" | "exe" | "check"
    title: str = ""
    description: str = ""
    # "usb" — используется, только если variants (см. ниже) пуст
    usb_files: list[Path] = field(default_factory=list)
    usb_copy_selected_apks: bool = False
    # "adb" — см. _parse_adb_line ниже за синтаксисом спецкоманд (#sleep,
    # #reboot, #wait_device, #ask) вперемешку с обычными adb shell командами
    commands: list[str] = field(default_factory=list)
    adb_install_selected_apks: bool = False
    # "apps" — используется, только если variants (см. ниже) пуст
    standard_apks: list[Path] = field(default_factory=list)
    # "exe" — готовый установщик, который производитель магнитолы даёт
    # только собранным .exe без исходных скриптов/инструкций; этап просто
    # даёт пользователю его запустить и завершить установку в нём самому
    exe_file: Path | None = None
    # "check" — техник вручную определяет версию/вариант (например,
    # аппаратного обеспечения или прошивки), глядя на саму магнитолу, и
    # выбирает ответ из check_options; ответ живёт только в рамках текущего
    # сеанса мастера установки (app/stage_wizard.py) под именем check_var —
    # им можно управлять видимостью последующих этапов (condition_var ниже)
    check_var: str = ""
    check_options: list[str] = field(default_factory=list)
    # Условная видимость — для ЛЮБОГО типа этапа (не только "check"): этап
    # показывается только если технику уже задавали check-этап с таким же
    # check_var, и ответ входит в condition_values. Пусто — всегда показывать.
    condition_var: str = ""
    condition_values: list[str] = field(default_factory=list)
    # Несколько вариантов содержимого "usb"/"apps" (например Full/Lite) —
    # техник выбирает нужный прямо на этапе установки. Пусто — обычное
    # поведение (один набор файлов, см. usb_files/standard_apks выше).
    variants: list[StepVariant] = field(default_factory=list)


@dataclass
class NewCarSpec:
    brand: str
    model: str
    # обязателен — Path: .html/.htm копируется как есть, что угодно другое
    # оборачивается в простой HTML; list[dict] — блоки из редактора
    # инструкции (см. app/instruction_editor.py, app/instruction_html.py)
    instruction_source: Path | list[dict]
    wifi: bool = False
    wifi_port: int = 5555
    steps: list[StepSpec] = field(default_factory=list)


class CarGenerationError(RuntimeError):
    pass


def create_car(cars_dir: Path, spec: NewCarSpec) -> Path:
    """Создаёт cars/<марка>/<модель>/ со всеми файлами. Бросает
    CarGenerationError, если модель уже существует, марка/модель заданы
    некорректно или не задано ни одного этапа."""
    brand = spec.brand.strip()
    model = spec.model.strip()
    if not brand or not model:
        raise CarGenerationError("Не заданы марка и/или модель.")
    if any(c in INVALID_NAME_CHARS for c in brand) or any(c in INVALID_NAME_CHARS for c in model):
        raise CarGenerationError('Марка/модель не должны содержать символы: < > : " / \\ | ? *')
    if not spec.steps:
        raise CarGenerationError("Не задано ни одного этапа установки.")

    model_dir = cars_dir / brand / model
    if model_dir.exists():
        raise CarGenerationError(f"Такая модель уже существует: {model_dir}")

    model_dir.mkdir(parents=True)
    _write_model_files(model_dir, spec)
    return model_dir


def update_car(model_dir: Path, spec: NewCarSpec) -> None:
    """Пересобирает файлы уже существующей модели (созданной этим мастером
    ранее — см. load_car_spec) заново из текущего spec. Марку/модель
    (папку) не переименовывает — если они изменились в spec, они просто
    игнорируются, вызывающий код (add_car_dialog.py) держит поля марки/
    модели заблокированными в режиме редактирования."""
    if not model_dir.exists():
        raise CarGenerationError(f"Модель не найдена: {model_dir}")
    if not spec.steps:
        raise CarGenerationError("Не задано ни одного этапа установки.")
    _write_model_files(model_dir, spec)


def load_car_spec(model_dir: Path) -> NewCarSpec | None:
    """Загружает _wizard_spec.json модели (если она была создана этим
    мастером) — для повторного открытия в редакторе. Пути в StepSpec
    указывают на уже скопированные файлы внутри model_dir (тот же порядок
    подпапок pack/pack_N, usb_files/step_N, что пишет _write_model_files)."""
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
        usb_files: list[Path] = []
        standard_apks: list[Path] = []
        exe_file: Path | None = None
        variants: list[StepVariant] = []
        variant_data = step_data.get("variants") or []
        if step_type == "usb":
            usb_step_dir = usb_root / f"step_{i}"
            if variant_data:
                for v in variant_data:
                    v_dir = usb_step_dir / v.get("name", "")
                    variants.append(StepVariant(
                        name=v.get("name", ""),
                        usb_files=[v_dir / name for name in v.get("usb_files", [])],
                    ))
            else:
                usb_files = [usb_step_dir / name for name in step_data.get("usb_files", [])]
        elif step_type == "apps":
            apps_index += 1
            pack_dir = files_dir / ("pack" if apps_index == 1 else f"pack_{apps_index}")
            if variant_data:
                for v in variant_data:
                    v_dir = pack_dir / v.get("name", "")
                    variants.append(StepVariant(
                        name=v.get("name", ""),
                        standard_apks=[v_dir / name for name in v.get("standard_apks", [])],
                    ))
            else:
                standard_apks = [pack_dir / name for name in step_data.get("standard_apks", [])]
        elif step_type == "exe" and step_data.get("exe_file"):
            exe_file = files_dir / f"exe_{i}" / step_data["exe_file"]
        steps.append(StepSpec(
            type=step_type,
            title=step_data.get("title", ""),
            description=step_data.get("description", ""),
            usb_files=usb_files,
            usb_copy_selected_apks=step_data.get("usb_copy_selected_apks", False),
            commands=step_data.get("commands", []),
            adb_install_selected_apks=step_data.get("adb_install_selected_apks", False),
            standard_apks=standard_apks,
            exe_file=exe_file,
            check_var=step_data.get("check_var", ""),
            check_options=step_data.get("check_options", []),
            condition_var=step_data.get("condition_var", ""),
            condition_values=step_data.get("condition_values", []),
            variants=variants,
        ))

    return NewCarSpec(
        brand=model_dir.parent.name,
        model=model_dir.name,
        instruction_source=model_dir / "instruction.html",
        wifi=data.get("wifi", False),
        wifi_port=data.get("wifi_port", 5555),
        steps=steps,
    )


def _write_model_files(model_dir: Path, spec: NewCarSpec) -> None:
    """Пишет instruction.html/files/usb_files/install.py/stages.py/
    _wizard_spec.json в model_dir — общая часть create_car/update_car.
    Копирование файлов идёт "по требуемому состоянию": уже лежащий на месте
    файл (source == destination, обычный случай при повторном сохранении
    без изменений в этом этапе) не трогаем — shutil.copy2 не переживает
    копирование файла в самого себя; а то, что раньше было скопировано, но
    больше не входит ни в один этап (переименовали/удалили/переставили
    этапы местами), удаляем, чтобы не копились сироты."""
    files_dir = model_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    usb_root = model_dir / "usb_files"

    _write_instruction(model_dir, spec.instruction_source)

    keep_paths: set[Path] = set()
    apps_index = 0
    for i, step in enumerate(spec.steps, start=1):
        if step.type == "apps":
            # Счётчик увеличиваем всегда (для КАЖДОГО "apps"-этапа, даже без
            # своих APK — pack/pack_N нумеруются по порядку этапов, а не по
            # тому, есть ли у них файлы) — иначе номера pack_N разъедутся с
            # тем, что рассчитывают load_car_spec()/_render_stages_py().
            apps_index += 1
            pack_dir = files_dir / ("pack" if apps_index == 1 else f"pack_{apps_index}")
            if step.variants:
                for variant in step.variants:
                    variant_dir = pack_dir / variant.name
                    variant_dir.mkdir(parents=True, exist_ok=True)
                    for apk in variant.standard_apks:
                        dst = (variant_dir / apk.name).resolve()
                        if apk.resolve() != dst:
                            shutil.copy2(apk, dst)
                        keep_paths.add(dst)
            elif step.standard_apks:
                pack_dir.mkdir(parents=True, exist_ok=True)
                for apk in step.standard_apks:
                    dst = (pack_dir / apk.name).resolve()
                    if apk.resolve() != dst:
                        shutil.copy2(apk, dst)
                    keep_paths.add(dst)
        elif step.type == "usb":
            usb_step_dir = usb_root / f"step_{i}"
            if step.variants:
                for variant in step.variants:
                    variant_dir = usb_step_dir / variant.name
                    variant_dir.mkdir(parents=True, exist_ok=True)
                    for f in variant.usb_files:
                        dst = (variant_dir / f.name).resolve()
                        if f.resolve() != dst:
                            shutil.copy2(f, dst)
                        keep_paths.add(dst)
            elif step.usb_files:
                usb_step_dir.mkdir(parents=True, exist_ok=True)
                for f in step.usb_files:
                    dst = (usb_step_dir / f.name).resolve()
                    if f.resolve() != dst:
                        shutil.copy2(f, dst)
                    keep_paths.add(dst)
        elif step.type == "exe" and step.exe_file:
            exe_step_dir = files_dir / f"exe_{i}"
            exe_step_dir.mkdir(parents=True, exist_ok=True)
            dst = (exe_step_dir / step.exe_file.name).resolve()
            if step.exe_file.resolve() != dst:
                shutil.copy2(step.exe_file, dst)
            keep_paths.add(dst)

    for root in (files_dir, usb_root):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.resolve() not in keep_paths:
                path.unlink()
        for path in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
            try:
                path.rmdir()
            except OSError:
                pass  # не пусто (не относящееся к мастеру) — оставляем как есть

    (model_dir / "install.py").write_text(_render_install_py(spec), encoding="utf-8")
    (model_dir / "stages.py").write_text(_render_stages_py(spec), encoding="utf-8")
    (model_dir / SPEC_FILENAME).write_text(_render_spec_json(spec), encoding="utf-8")


def _write_instruction(model_dir: Path, source: Path | list[dict]) -> None:
    if isinstance(source, list):
        instruction_html.save_instruction(model_dir, source)
        return

    dest = model_dir / "instruction.html"
    if source.suffix.lower() in (".html", ".htm"):
        if source.resolve() != dest.resolve():
            shutil.copy2(source, dest)
    else:
        text = source.read_text(encoding="utf-8", errors="replace")
        dest.write_text(_text_to_html(text), encoding="utf-8")


def _text_to_html(text: str) -> str:
    escaped = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
               .replace("\n", "<br>"))
    return (
        "<!DOCTYPE html>\n<html lang=\"ru\"><head><meta charset=\"utf-8\"></head>\n"
        "<body style='font-family:Segoe UI, sans-serif; background:#171f30; "
        "color:#e8ecf4; padding:16px'>"
        f"<p>{escaped}</p></body></html>\n"
    )


# ----------------------------------------------------------------------
# _wizard_spec.json — снимок NewCarSpec для повторного открытия в редакторе
# ----------------------------------------------------------------------
def _render_spec_json(spec: NewCarSpec) -> str:
    data = {
        "wifi": spec.wifi,
        "wifi_port": spec.wifi_port,
        "steps": [
            {
                "type": step.type,
                "title": step.title,
                "description": step.description,
                "usb_files": [f.name for f in step.usb_files],
                "usb_copy_selected_apks": step.usb_copy_selected_apks,
                "commands": step.commands,
                "adb_install_selected_apks": step.adb_install_selected_apks,
                "standard_apks": [f.name for f in step.standard_apks],
                "exe_file": step.exe_file.name if step.exe_file else None,
                "check_var": step.check_var,
                "check_options": step.check_options,
                "condition_var": step.condition_var,
                "condition_values": step.condition_values,
                "variants": [
                    {
                        "name": v.name,
                        "usb_files": [f.name for f in v.usb_files],
                        "standard_apks": [f.name for f in v.standard_apks],
                    }
                    for v in step.variants
                ],
            }
            for step in spec.steps
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


# ----------------------------------------------------------------------
# Спецкоманды ADB-этапа — см. подсказку в add_car_dialog._build_adb_fields.
# Каждая строка команд этапа — либо обычная "adb shell" команда, либо одна
# из этих спецкоманд (пауза/перезагрузка/ожидание устройства/вопрос
# пользователю) — то, что уже умеет InstallContext (см. install_context.py),
# но раньше было недоступно из мастера, только из руками написанных
# install.py.
# ----------------------------------------------------------------------
_ADB_SLEEP_RE = re.compile(r"^#sleep\s+([\d.]+)\s*$", re.IGNORECASE)
_ADB_REBOOT_RE = re.compile(r"^#reboot\s*$", re.IGNORECASE)
_ADB_REBOOT_NOWAIT_RE = re.compile(r"^#reboot_nowait\s*$", re.IGNORECASE)
_ADB_WAIT_DEVICE_RE = re.compile(r"^#wait_device(?:\s+([\d.]+))?\s*$", re.IGNORECASE)
_ADB_ASK_RE = re.compile(r"^#ask\s+(.+)$", re.IGNORECASE)


def _parse_adb_line(line: str) -> tuple[str, float | str | None]:
    """Разбирает одну строку команд ADB-этапа. Что не распознано ни одной
    из спецкоманд — обычная adb shell команда как есть (в том числе
    случайная строка, начинающаяся с "#", но не совпавшая ни с одним
    шаблоном — так безопаснее, чем требовать явного экранирования)."""
    if m := _ADB_SLEEP_RE.match(line):
        return "sleep", float(m.group(1))
    if _ADB_REBOOT_RE.match(line):
        return "reboot", None
    if _ADB_REBOOT_NOWAIT_RE.match(line):
        return "reboot_nowait", None
    if m := _ADB_WAIT_DEVICE_RE.match(line):
        return "wait_device", float(m.group(1)) if m.group(1) else None
    if m := _ADB_ASK_RE.match(line):
        return "ask", m.group(1).strip()
    return "shell", line


# ----------------------------------------------------------------------
# install.py
# ----------------------------------------------------------------------
def _render_install_py(spec: NewCarSpec) -> str:
    lines = [
        f'"""{spec.brand} {spec.model} — создано мастером "Добавить машину...".',
        'Отредактируйте вручную, если нужно что-то сложнее, чем список ADB-команд',
        'и файлы для флешки."""',
    ]

    for i, step in enumerate(spec.steps, start=1):
        if step.type == "adb":
            lines += ["", "", f"def adb_step_{i}(ctx):"]
            lines += [f'    """{step.title or f"Этап {i}"}."""']
            lines.append("    _ask = None")
            for raw_line in step.commands:
                kind, payload = _parse_adb_line(raw_line)
                if kind == "sleep":
                    lines.append(f"    ctx.sleep({payload})")
                elif kind == "reboot":
                    lines.append("    ctx.reboot(wait=True)")
                elif kind == "reboot_nowait":
                    lines.append("    ctx.reboot(wait=False)")
                elif kind == "wait_device":
                    if payload is not None:
                        lines.append(f"    ctx.wait_for_device(timeout={payload})")
                    else:
                        lines.append("    ctx.wait_for_device()")
                elif kind == "ask":
                    lines.append(f"    _ask = ctx.ask_input({payload!r})")
                else:
                    if "{ask}" in payload:
                        lines.append(
                            f"    ctx.shell({payload!r}.replace('{{ask}}', str(_ask)), check=False)")
                    else:
                        lines.append(f"    ctx.shell({payload!r}, check=False)")
            if step.adb_install_selected_apks:
                lines.append('    ctx.install_selected_apks(extra_args=["-g"])')
        elif step.type == "usb":
            lines += ["", "", f"def usb_step_{i}(ctx):"]
            lines += [f'    """{step.title or f"Этап {i}"}."""']
            # ctx.variant — какой вариант (Full/Lite/...) выбрал техник на
            # этапе установки (см. app/stage_wizard.py._run_usb_stage,
            # app/usb_context.py) — None, если у этапа нет вариантов вовсе
            # (обычный однонаборный этап, как раньше).
            lines += [
                '    variant = getattr(ctx, "variant", None)',
                f'    usb_dir = ctx.usb_file(f"step_{i}/{{variant}}") if variant '
                f'else ctx.usb_file("step_{i}")',
                "    if usb_dir.exists():",
                '        ctx.copy_dir(usb_dir, "")',
            ]
            if step.usb_copy_selected_apks:
                lines += ["    if ctx.selected_apks:",
                           '        ctx.copy_selected_apks("")']

    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# stages.py
# ----------------------------------------------------------------------
def _render_stages_py(spec: NewCarSpec) -> str:
    lines = [
        f'"""{spec.brand} {spec.model} — этапы установки.',
        'Создано мастером "Добавить машину...", можно редактировать вручную."""',
        "import sys",
        "from pathlib import Path",
        "",
        'sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))',
        "from load_sibling import load_install  # noqa: E402",
    ]
    if spec.wifi:
        lines.append("from wifi_adb import connect_wifi  # noqa: E402")
    lines += ["", "m = load_install(__file__)", ""]

    if spec.wifi:
        lines += [
            "",
            "def _with_connect(fn):",
            "    def wrapped(ctx):",
            "        connect_wifi(ctx, WIFI_PORT)",
            "        fn(ctx)",
            "    return wrapped",
            "",
            "",
            f"WIFI_PORT = {spec.wifi_port}",
            "",
        ]

    lines.append("STAGES = [")
    apps_index = 0
    for i, step in enumerate(spec.steps, start=1):
        title = step.title or f"Этап {i}"
        entry = ["    {"]
        entry.append(f'        "type": {step.type!r},')
        entry.append(f'        "title": {title!r},')
        if step.description:
            entry.append(f'        "description": {step.description!r},')
        if step.condition_var:
            entry.append(f'        "condition_var": {step.condition_var!r},')
            entry.append(f'        "condition_values": {step.condition_values!r},')

        if step.type == "check":
            entry.append(f'        "check_var": {step.check_var!r},')
            entry.append(f'        "check_options": {step.check_options!r},')
        elif step.type == "apps":
            apps_index += 1
            pack_name = "pack" if apps_index == 1 else f"pack_{apps_index}"
            pack_expr = f'Path(__file__).resolve().parent / "files" / "{pack_name}"'
            if step.variants:
                entry.append(f'        "standard_dir_base": {pack_expr},')
                entry.append(f'        "variant_names": {[v.name for v in step.variants]!r},')
            else:
                entry.append(f'        "standard_dir": {pack_expr},')
        elif step.type == "usb":
            run_expr = f"m.usb_step_{i}"
            entry.append(f'        "run": {run_expr},')
            if step.variants:
                entry.append(f'        "variant_names": {[v.name for v in step.variants]!r},')
        elif step.type == "adb":
            run_expr = f"_with_connect(m.adb_step_{i})" if spec.wifi else f"m.adb_step_{i}"
            entry.append(f'        "run": {run_expr},')
        elif step.type == "exe" and step.exe_file:
            entry.append(
                f'        "exe_path": Path(__file__).resolve().parent / "files" / "exe_{i}" '
                f'/ {step.exe_file.name!r},'
            )
        # "manual" — без "run"

        entry.append("    },")
        lines.append("\n".join(entry))
    lines.append("]")
    return "\n".join(lines) + "\n"
