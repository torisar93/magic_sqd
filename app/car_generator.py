"""Генерация файлов новой модели (install.py/stages.py + копирование
инструкции/APK/файлов флешки) для мастера "Добавить машину..."
(см. app/add_car_dialog.py). Модель описывается свободной последовательностью
этапов (StepSpec) — тем же набором типов, что и stages.py, написанный
руками: "apps" (выбор приложений), "usb" (запись на флешку), "adb"
(команды/установка), "manual" (просто инструкция), "instruction" (часть
общей инструкции — заголовки/шаги/плашки/фото, см. StepSpec.
instruction_blocks). Общей instruction.html на всю модель больше нет —
содержимое набирается такими этапами прямо в последовательности установки.
Генератор только собирает из них текст файла — сам механизм ("apps"-этап,
load_sibling.load_install, wifi_adb.connect_wifi, UsbContext.usb_file()/
copy_dir()/copy_selected_apks(), ctx.install_selected_apks()) уже есть в
проекте и используется как есть."""
import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import instruction_html
from .scanner import VERSION_FILENAME

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
    # "apps" — обязательные (всегда устанавливаются, без чекбокса) и
    # необязательные (техник выбирает сам, чекбоксом — см. StepSpec ниже)
    # APK этого варианта.
    standard_apks: list[Path] = field(default_factory=list)
    standard_apks_optional: list[Path] = field(default_factory=list)


@dataclass
class ActionSpec:
    """Одна кнопка "actions"-этапа (StepSpec.actions ниже) — необязательные
    ADB-команды/спецдействия, которые техник может нажать после установки
    (или в любой другой момент, куда поставили этот этап) в любом порядке
    и по несколько раз: запустить приложение, выдать разрешения, включить
    фиктивные местоположения и т.п."""
    label: str = ""
    # "command" — commands ниже (тот же мини-DSL, что и у "adb"-этапа, см.
    # _parse_adb_line/_render_command_body, но без прикреплённых файлов —
    # #push/#install тут не поддерживаются). "grant_permissions" —
    # список приложений магнитолы предлагается выбрать во время установки
    # (ctx.ask_choice), дальше cars/_shared/adb_permissions.py.
    # grant_all_permissions сам выдаёт все обычные и специальные разрешения
    # (в т.ч. недоступные через штатный экран настроек на многих магнитолах).
    # "mock_location" — то же самое, но cars/_shared/adb_permissions.py.
    # set_mock_location_app назначает выбранное приложение приложением для
    # фиктивных местоположений и включает саму возможность.
    kind: str = "command"
    commands: list[str] = field(default_factory=list)


@dataclass
class StepSpec:
    type: str  # "usb" | "manual" | "adb" | "apps" | "exe" | "check" | "instruction" | "uart" | "telnet" | "actions"
    title: str = ""
    description: str = ""
    # "instruction" — часть общей инструкции (заголовки/шаги/плашки/фото),
    # собранная тем же блочным редактором, что и общая инструкция модели
    # (см. app/instruction_editor.py, app/instruction_html.py) — но
    # показывается отдельной страницей ПОСЛЕДОВАТЕЛЬНО среди этапов
    # установки (см. app/stage_wizard.py), а не только один раз в начале.
    # Пишется в files/instruction_<i>/instruction.html — своя папка на
    # каждый такой этап, чтобы фото разных "частей инструкции" не путались
    # друг с другом при очистке "осиротевших" файлов (см. _write_model_files).
    instruction_blocks: list[dict] = field(default_factory=list)
    # "usb" — используется, только если variants (см. ниже) пуст
    usb_files: list[Path] = field(default_factory=list)
    usb_copy_selected_apks: bool = False
    # "usb" — подпапка на флешке, куда копируются приложения, отмеченные
    # техником галочками из общей библиотеки apk/ (см. usb_copy_selected_apks).
    # Пусто — корень флешки (как раньше). Разные скрипты на разных флешках
    # ждут APK в разных папках (например "apps"/"APK"/"Install"), поэтому
    # это настраиваемое поле, а не хардкод.
    usb_apks_dest: str = ""
    # "usb" — имя папки в cars/_shared/ (см. app/install_context.py,
    # app/usb_context.py: ctx.shared_dir) с набором файлов, общим сразу для
    # многих моделей (например, один и тот же универсальный инструмент) —
    # копируется на флешку НАПРЯМУЮ из общей папки при установке, без
    # копирования в usb_files САМОЙ модели, чтобы не дублировать одно и то
    # же на сервере и у каждого техника по разу на модель. Пусто — не
    # используется, работает как раньше (только usb_files). Может
    # применяться ОДНОВРЕМЕННО с usb_files — на флешку попадёт и то, и
    # другое (см. _render_install_py).
    usb_shared_folder: str = ""
    # "adb" — см. _parse_adb_line ниже за синтаксисом спецкоманд (#sleep,
    # #reboot, #root, #push, ...) вперемешку с обычными adb shell командами
    # ИЛИ "сырыми" строками прямо из .bat/.sh автора набора (adb root/push/
    # install/..., TIMEOUT /T N) — парсер понимает оба варианта.
    # "uart" — тот же список, но БЕЗ мини-DSL adb: каждая строка отправляется
    # как есть (+ "\r\n") через открытое серийное соединение (см.
    # cars/_shared/uart_adb.py:open_uart), ответ (если есть) логируется.
    # "telnet" — тоже без мини-DSL: каждая строка — отдельный вызов
    # cars/_shared/telnet_adb.py:enable_adb_via_telnet(ctx, command=строка)
    # (IPv6-адрес находится автоматически или предлагается выбрать/ввести
    # заново на каждый вызов). Пусто — используется command по умолчанию
    # этой функции ("setprop persist.service.adb.button.visible ON").
    commands: list[str] = field(default_factory=list)
    adb_install_selected_apks: bool = False
    # Файлы, прикреплённые к ADB-этапу — на них ссылаются команды #push/
    # #install (или их "сырые" аналоги adb push/adb install) по имени файла.
    adb_files: list[Path] = field(default_factory=list)
    # "uart" — скорость порта (бод); разные магнитолы используют разные
    # значения (например 921600 у Haval M6 2026), поэтому настраивается за
    # этап, а не хардкодится в uart_adb.py. Порт (COM-имя) НЕ настраивается
    # здесь — выбирается техником на месте через ctx.ask_choice (см.
    # open_uart), т.к. заранее неизвестен и может быть разным на разных
    # компьютерах.
    uart_baudrate: int = 115200
    # "actions" — см. ActionSpec выше.
    actions: list[ActionSpec] = field(default_factory=list)
    # "apps" — используется, только если variants (см. ниже) пуст.
    # standard_apks — обязательные: устанавливаются всегда, без чекбокса и
    # без возможности отключить техником (см. app/stage_wizard.js:
    # buildAppRow(apk, required=true) — чекбокс показывается уже отмеченным
    # и задизейбленным). standard_apks_optional — необязательные: отдельным
    # разделом с обычными чекбоксами, техник решает сам. Оба показываются
    # ВЫШЕ выбора из общей библиотеки apk/ (см. install_api.py:
    # standard_apks — читает их из подпапок files/pack*/required и
    # .../optional соответственно).
    standard_apks: list[Path] = field(default_factory=list)
    standard_apks_optional: list[Path] = field(default_factory=list)
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
    # Позиция узла в визуальном редакторе-графе (app/web/frontend/js/screens/
    # graph_wizard.js) — только для раскладки на холсте, ни на что в
    # install.py/stages.py не влияет. Классический мастер их не показывает,
    # просто сохраняет как есть (0.0 по умолчанию — граф сам раскладывает
    # шаги без сохранённой позиции при открытии).
    pos_x: float = 0.0
    pos_y: float = 0.0


@dataclass
class NewCarSpec:
    brand: str
    model: str
    wifi: bool = False
    wifi_port: int = 5555
    steps: list[StepSpec] = field(default_factory=list)
    # Необязательный слой "модификация" (рестайлинг/версия для другого
    # рынка) — см. app/scanner.py:ModelGroup. Пусто — обычная модель,
    # cars/<Марка>/<Модель>/; иначе — cars/<Марка>/<Модель>/<Модификация>/.
    modification: str = ""
    # Короткая заметка "что изменилось в этом сохранении" — необязательная,
    # не хранится в _wizard_spec.json (это не часть состояния мастера, а
    # разовая подпись к конкретному сохранению). Пишется в version.json
    # вместе с автоинкрементным revision — см. _write_version_file,
    # app/update_tracker.py (сводка "Что нового" у техника при старте).
    changelog: str = ""


class CarGenerationError(RuntimeError):
    pass


def create_car(cars_dir: Path, spec: NewCarSpec) -> Path:
    """Создаёт cars/<марка>/<модель>/ (или cars/<марка>/<модель>/
    <модификация>/, если spec.modification задана) со всеми файлами.
    Бросает CarGenerationError, если модель уже существует, марка/модель/
    модификация заданы некорректно, не задано ни одного этапа, или
    модификация указана для модели, у которой уже есть свои файлы прямо
    в cars/<марка>/<модель>/ (обычная модель без модификаций — см.
    app/scanner.py:ModelGroup, там же и обратный случай "leaf побеждает
    модификации", так что смешивать их в одной папке модели нельзя)."""
    brand = spec.brand.strip()
    model = spec.model.strip()
    modification = spec.modification.strip()
    if not brand or not model:
        raise CarGenerationError("Не заданы марка и/или модель.")
    if any(c in INVALID_NAME_CHARS for c in brand) or any(c in INVALID_NAME_CHARS for c in model):
        raise CarGenerationError('Марка/модель не должны содержать символы: < > : " / \\ | ? *')
    if modification and any(c in INVALID_NAME_CHARS for c in modification):
        raise CarGenerationError('Модификация не должна содержать символы: < > : " / \\ | ? *')
    if not spec.steps:
        raise CarGenerationError("Не задано ни одного этапа установки.")

    model_root = cars_dir / brand / model
    if modification:
        if model_root.exists() and _has_own_files(model_root):
            raise CarGenerationError(
                f"{model_root} уже существует как обычная модель без модификаций — "
                "чтобы добавить модификацию, сначала перенесите её файлы в подпапку вручную.")
        model_dir = model_root / modification
    else:
        model_dir = model_root
    if model_dir.exists():
        raise CarGenerationError(f"Такая модель уже существует: {model_dir}")

    model_dir.mkdir(parents=True)
    _write_model_files(model_dir, spec)
    return model_dir


def _has_own_files(model_root: Path) -> bool:
    return any((model_root / name).exists() for name in ("instruction.html", "stages.py", "install.py"))


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


def load_car_spec(model_dir: Path, brand: str, model: str, modification: str = "") -> NewCarSpec | None:
    """Загружает _wizard_spec.json модели (если она была создана этим
    мастером) — для повторного открытия в редакторе. Пути в StepSpec
    указывают на уже скопированные файлы внутри model_dir (тот же порядок
    подпапок pack/pack_N, usb_files/step_N, что пишет _write_model_files).
    brand/model/modification — передаются вызывающим кодом (см.
    app/scanner.py:ModelInfo), а не выводятся из model_dir.parent/.name:
    для модификации (cars/<Марка>/<Модель>/<Модификация>/) такой вывод дал
    бы Модель вместо Марки и Модификацию вместо Модели."""
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
        standard_apks_optional: list[Path] = []
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
                        standard_apks=[v_dir / "required" / name for name in v.get("standard_apks", [])],
                        standard_apks_optional=[
                            v_dir / "optional" / name for name in v.get("standard_apks_optional", [])],
                    ))
            else:
                standard_apks = [pack_dir / "required" / name for name in step_data.get("standard_apks", [])]
                standard_apks_optional = [
                    pack_dir / "optional" / name for name in step_data.get("standard_apks_optional", [])]
        elif step_type == "exe" and step_data.get("exe_file"):
            exe_file = files_dir / f"exe_{i}" / step_data["exe_file"]
        adb_files: list[Path] = []
        if step_type == "adb":
            adb_files = [files_dir / f"adb_{i}" / name for name in step_data.get("adb_files", [])]
        instruction_blocks: list[dict] = []
        if step_type == "instruction":
            instr_step_dir = files_dir / f"instruction_{i}"
            instr_path = instr_step_dir / "instruction.html"
            try:
                text = instr_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            # model_dir=instr_step_dir (не model_dir целиком) — относительные
            # пути фото-блоков в этом файле считаются от instr_step_dir/images/
            # (см. instruction_html.save_instruction/_write_model_files).
            instruction_blocks = instruction_html.parse_blocks(text, instr_step_dir) or []
        actions = [
            ActionSpec(label=a.get("label", ""), kind=a.get("kind", "command"), commands=a.get("commands") or [])
            for a in step_data.get("actions", [])
        ]
        steps.append(StepSpec(
            type=step_type,
            title=step_data.get("title", ""),
            description=step_data.get("description", ""),
            instruction_blocks=instruction_blocks,
            usb_files=usb_files,
            usb_copy_selected_apks=step_data.get("usb_copy_selected_apks", False),
            usb_apks_dest=step_data.get("usb_apks_dest", ""),
            usb_shared_folder=step_data.get("usb_shared_folder", ""),
            commands=step_data.get("commands", []),
            adb_install_selected_apks=step_data.get("adb_install_selected_apks", False),
            adb_files=adb_files,
            standard_apks=standard_apks,
            standard_apks_optional=standard_apks_optional,
            exe_file=exe_file,
            check_var=step_data.get("check_var", ""),
            check_options=step_data.get("check_options", []),
            condition_var=step_data.get("condition_var", ""),
            condition_values=step_data.get("condition_values", []),
            variants=variants,
            pos_x=step_data.get("pos_x", 0.0),
            pos_y=step_data.get("pos_y", 0.0),
            uart_baudrate=step_data.get("uart_baudrate", 115200),
            actions=actions,
        ))

    return NewCarSpec(
        brand=brand,
        model=model,
        modification=modification,
        wifi=data.get("wifi", False),
        wifi_port=data.get("wifi_port", 5555),
        steps=steps,
    )


def _copy_path(src: Path, dst: Path, keep_paths: set[Path]) -> None:
    """Копирует src в dst — файл через copy2, папку целиком и рекурсивно
    через copytree (dirs_exist_ok=True, чтобы повторное сохранение без
    изменений в этой папке не падало на уже существующей dst — тот же
    принцип "не трогаем то, что и так на месте", что и для одиночных
    файлов). Для папки в keep_paths попадают ВСЕ файлы внутри нужно —
    иначе цикл очистки "осиротевших" файлов в _write_model_files сочтёт их
    лишними и удалит сразу после копирования (он ничего не знает про
    usb_files/adb_files, только ходит по files_dir/usb_root целиком)."""
    dst = dst.resolve()
    if src.is_dir():
        if src.resolve() != dst:
            shutil.copytree(src, dst, dirs_exist_ok=True)
        for f in dst.rglob("*"):
            if f.is_file():
                keep_paths.add(f.resolve())
    else:
        if src.resolve() != dst:
            shutil.copy2(src, dst)
        keep_paths.add(dst)


def _write_model_files(model_dir: Path, spec: NewCarSpec) -> None:
    """Пишет files/usb_files/install.py/stages.py/_wizard_spec.json в
    model_dir — общая часть create_car/update_car. Копирование файлов идёт
    "по требуемому состоянию": уже лежащий на месте файл (source ==
    destination, обычный случай при повторном сохранении без изменений в
    этом этапе) не трогаем — shutil.copy2 не переживает копирование файла в
    самого себя; а то, что раньше было скопировано, но больше не входит ни
    в один этап (переименовали/удалили/переставили этапы местами), удаляем,
    чтобы не копились сироты."""
    files_dir = model_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    usb_root = model_dir / "usb_files"

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
                    for subdir_name, apks in (("required", variant.standard_apks),
                                               ("optional", variant.standard_apks_optional)):
                        if not apks:
                            continue
                        sub_dir = variant_dir / subdir_name
                        sub_dir.mkdir(parents=True, exist_ok=True)
                        for apk in apks:
                            dst = (sub_dir / apk.name).resolve()
                            if apk.resolve() != dst:
                                shutil.copy2(apk, dst)
                            keep_paths.add(dst)
            else:
                for subdir_name, apks in (("required", step.standard_apks),
                                           ("optional", step.standard_apks_optional)):
                    if not apks:
                        continue
                    sub_dir = pack_dir / subdir_name
                    sub_dir.mkdir(parents=True, exist_ok=True)
                    for apk in apks:
                        dst = (sub_dir / apk.name).resolve()
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
                        _copy_path(f, variant_dir / f.name, keep_paths)
            elif step.usb_files:
                usb_step_dir.mkdir(parents=True, exist_ok=True)
                for f in step.usb_files:
                    _copy_path(f, usb_step_dir / f.name, keep_paths)
        elif step.type == "exe" and step.exe_file:
            exe_step_dir = files_dir / f"exe_{i}"
            exe_step_dir.mkdir(parents=True, exist_ok=True)
            dst = (exe_step_dir / step.exe_file.name).resolve()
            if step.exe_file.resolve() != dst:
                shutil.copy2(step.exe_file, dst)
            keep_paths.add(dst)
        elif step.type == "adb" and step.adb_files:
            adb_step_dir = files_dir / f"adb_{i}"
            adb_step_dir.mkdir(parents=True, exist_ok=True)
            for f in step.adb_files:
                _copy_path(f, adb_step_dir / f.name, keep_paths)
        elif step.type == "instruction" and step.instruction_blocks:
            instr_step_dir = files_dir / f"instruction_{i}"
            instr_step_dir.mkdir(parents=True, exist_ok=True)
            instruction_html.save_instruction(instr_step_dir, step.instruction_blocks)
            keep_paths.add((instr_step_dir / "instruction.html").resolve())
            images_dir = instr_step_dir / "images"
            if images_dir.is_dir():
                for f in images_dir.iterdir():
                    if f.is_file():
                        keep_paths.add(f.resolve())

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
    _write_version_file(model_dir, spec.changelog)


def _write_version_file(model_dir: Path, changelog: str) -> None:
    """revision — просто счётчик сохранений этой модели через мастер,
    +1 к тому, что уже лежало в version.json (0, если файла ещё нет —
    первое сохранение новой модели даёт revision=1). Не привязан к
    какой-либо внешней схеме версионирования (semver и т.п.) — единственная
    цель: у клиента (см. app/update_tracker.py) есть монотонно растущее
    число, чтобы отличить "видел уже" от "появилось новое", а changelog —
    что показать техническому в сводке при этом."""
    version_path = model_dir / VERSION_FILENAME
    revision = 0
    try:
        existing = json.loads(version_path.read_text(encoding="utf-8"))
        revision = int(existing.get("revision", 0))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    data = {
        "revision": revision + 1,
        "changelog": changelog.strip(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    version_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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
                "usb_apks_dest": step.usb_apks_dest,
                "usb_shared_folder": step.usb_shared_folder,
                "commands": step.commands,
                "adb_install_selected_apks": step.adb_install_selected_apks,
                "adb_files": [f.name for f in step.adb_files],
                "standard_apks": [f.name for f in step.standard_apks],
                "standard_apks_optional": [f.name for f in step.standard_apks_optional],
                "exe_file": step.exe_file.name if step.exe_file else None,
                "check_var": step.check_var,
                "check_options": step.check_options,
                "condition_var": step.condition_var,
                "condition_values": step.condition_values,
                "pos_x": step.pos_x,
                "pos_y": step.pos_y,
                "uart_baudrate": step.uart_baudrate,
                "actions": [
                    {"label": a.label, "kind": a.kind, "commands": a.commands}
                    for a in step.actions
                ],
                "variants": [
                    {
                        "name": v.name,
                        "usb_files": [f.name for f in v.usb_files],
                        "standard_apks": [f.name for f in v.standard_apks],
                        "standard_apks_optional": [f.name for f in v.standard_apks_optional],
                    }
                    for v in step.variants
                ],
            }
            for step in spec.steps
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


# ----------------------------------------------------------------------
# Команды ADB-этапа — см. подсказку в add_car_dialog._build_adb_fields.
# Каждая строка — одна из трёх вещей:
#   1. #-спецкоманда (пауза/перезагрузка/root/push/...) — то, что уже умеет
#      InstallContext (см. install_context.py), но раньше было недоступно
#      из мастера, только из руками написанных install.py;
#   2. "сырая" строка ПРЯМО из .bat/.sh автора набора установки — "adb
#      root"/"adb shell ..."/"adb push ... ..."/"TIMEOUT /T N" и т.п. —
#      технику не нужно вручную переписывать в #-спецкоманды, можно просто
#      вставить нужный кусок скрипта как есть; декоративный batch-мусор
#      (@echo/cls/pause/rem/:метки) распознаётся и пропускается, а не
#      отправляется на устройство как есть;
#   3. обычная "adb shell" команда как есть — если ничего из вышеперечисленного
#      не подошло (так безопаснее, чем требовать явного экранирования).
# ----------------------------------------------------------------------
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

# "Сырые" строки прямо из .bat/.sh — необязательный "-s <serial>" после adb
# (техник мог скопировать команду вместе с указанием устройства).
_DEV = r"(?:\s+-s\s+\S+)?"
_RAW_ADB_ROOT_RE = re.compile(rf"^adb{_DEV}\s+root\s*$", re.IGNORECASE)
_RAW_ADB_DISABLE_VERITY_RE = re.compile(rf"^adb{_DEV}\s+disable-verity\s*$", re.IGNORECASE)
_RAW_ADB_REMOUNT_RE = re.compile(rf"^adb{_DEV}\s+remount\s*$", re.IGNORECASE)
_RAW_ADB_REBOOT_RE = re.compile(rf"^adb{_DEV}\s+reboot\s*$", re.IGNORECASE)
_RAW_ADB_WAIT_DEVICE_RE = re.compile(rf"^adb{_DEV}\s+wait-for-device\s*$", re.IGNORECASE)
_RAW_ADB_SHELL_RE = re.compile(rf"^adb{_DEV}\s+shell\s+(.+)$", re.IGNORECASE)
_RAW_ADB_PUSH_RE = re.compile(rf"^adb{_DEV}\s+push\s+(\S+)\s+(\S+)\s*$", re.IGNORECASE)
_RAW_ADB_INSTALL_RE = re.compile(rf"^adb{_DEV}\s+install\s+(?:-[a-zA-Z]+\s+)*(\S+)\s*$", re.IGNORECASE)

# Batch-специфика без прямого аналога на устройстве — пауза для техника,
# читающего консоль ноутбука ("TIMEOUT"), и чисто декоративный/управляющий
# batch-мусор (цвет, echo-статусы, cls, pause, rem-комментарии, метки
# ":имя") — просто пропускается, а не улетает на устройство буквально.
_BAT_TIMEOUT_RE = re.compile(r"^TIMEOUT\s+/T\s+([\d.]+)", re.IGNORECASE)
_BAT_NOOP_RE = re.compile(
    r"^(@?echo(\s|\.|$)|cls\s*$|color\s|pause\s*$|rem[:\s]|::|:\w+\s*$)", re.IGNORECASE)


def _adb_basename(local: str) -> str:
    """Имя файла из batch-пути вида %~dp0\\..\\apk\\File.apk — для поиска
    среди StepSpec.adb_files по имени (см. _ADB_PUSH_RE/_RAW_ADB_PUSH_RE)."""
    local = local.strip().strip('"')
    return local.replace("\\", "/").rsplit("/", 1)[-1]


def _parse_adb_line(line: str) -> tuple[str, object]:
    """Разбирает одну строку команд ADB-этапа. Что не распознано ни одной
    из спецкоманд/сырых adb-команд — обычная adb shell команда как есть (в
    том числе случайная строка, начинающаяся с "#", но не совпавшая ни с
    одним шаблоном — так безопаснее, чем требовать явного экранирования)."""
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
    if _ADB_ROOT_RE.match(line):
        return "root", None
    if _ADB_DISABLE_VERITY_RE.match(line):
        return "disable_verity", None
    if _ADB_REMOUNT_RE.match(line):
        return "remount", None
    if m := _ADB_PUSH_RE.match(line):
        return "push", (_adb_basename(m.group(1)), m.group(2).strip())
    if m := _ADB_INSTALL_RE.match(line):
        return "install", _adb_basename(m.group(1))

    # "Сырые" строки прямо из .bat/.sh автора набора (см. пояснение выше).
    if _RAW_ADB_ROOT_RE.match(line):
        return "root", None
    if _RAW_ADB_DISABLE_VERITY_RE.match(line):
        return "disable_verity", None
    if _RAW_ADB_REMOUNT_RE.match(line):
        return "remount", None
    if _RAW_ADB_REBOOT_RE.match(line):
        return "reboot_nowait", None
    if _RAW_ADB_WAIT_DEVICE_RE.match(line):
        return "wait_device", None
    if m := _RAW_ADB_PUSH_RE.match(line):
        return "push", (_adb_basename(m.group(1)), m.group(2).strip())
    if m := _RAW_ADB_INSTALL_RE.match(line):
        return "install", _adb_basename(m.group(1))
    if m := _RAW_ADB_SHELL_RE.match(line):
        return "shell", m.group(1).strip()
    if m := _BAT_TIMEOUT_RE.match(line):
        return "sleep", float(m.group(1))
    if _BAT_NOOP_RE.match(line):
        return "skip", None

    return "shell", line


def _render_command_body(commands: list[str], files_rel_prefix: str) -> list[str]:
    """Тело функции для списка ADB-команд (см. _parse_adb_line выше) — общее
    для "adb"-этапа и командных действий "actions"-этапа (ActionSpec.kind ==
    "command"), чтобы не дублировать разбор мини-DSL дважды. files_rel_prefix
    — подпапка files/ с прикреплёнными файлами для #push/#install (у
    "actions"-действий их нет — там этот путь просто не встретится, если
    техник не использует #push/#install в командах кнопки)."""
    lines = ["    _ask = None"]
    for raw_line in commands:
        kind, payload = _parse_adb_line(raw_line)
        if kind == "skip":
            continue
        elif kind == "sleep":
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
        elif kind == "root":
            lines.append('    ctx.adb("root", check=False)')
        elif kind == "disable_verity":
            lines.append('    ctx.adb("disable-verity", check=False)')
        elif kind == "remount":
            lines.append('    ctx.adb("remount", check=False)')
        elif kind == "push":
            name, remote = payload
            rel = f"{files_rel_prefix}/{name}"
            lines.append(f"    ctx.push(ctx.file({rel!r}), {remote!r})")
        elif kind == "install":
            rel = f"{files_rel_prefix}/{payload}"
            lines.append(f"    ctx.install_apk(ctx.file({rel!r}))")
        else:
            if "{ask}" in payload:
                lines.append(
                    f"    ctx.shell({payload!r}.replace('{{ask}}', str(_ask)), check=False)")
            else:
                lines.append(f"    ctx.shell({payload!r}, check=False)")
    return lines


# ----------------------------------------------------------------------
# install.py
# ----------------------------------------------------------------------
def _render_install_py(spec: NewCarSpec) -> str:
    lines = [
        f'"""{spec.brand} {spec.model} — создано мастером "Добавить машину...".',
        'Отредактируйте вручную, если нужно что-то сложнее, чем список ADB-команд',
        'и файлы для флешки."""',
    ]
    if any(step.type == "uart" for step in spec.steps):
        # sys.path на _shared/ уже расширен в stages.py (см. _render_stages_py)
        # ДО того, как оно вызывает load_sibling.load_install(__file__) и тем
        # самым исполняет этот файл — поэтому импорт здесь уже видит uart_adb.
        lines.append("from uart_adb import open_uart  # noqa: E402")
    if any(step.type == "telnet" for step in spec.steps):
        lines.append("from telnet_adb import enable_adb_via_telnet  # noqa: E402")
    if any(step.type == "actions" and a.kind in ("grant_permissions", "mock_location")
           for step in spec.steps for a in step.actions):
        lines.append(
            "from adb_permissions import grant_all_permissions, list_installed_packages, "
            "set_mock_location_app  # noqa: E402"
        )

    for i, step in enumerate(spec.steps, start=1):
        if step.type == "adb":
            lines += ["", "", f"def adb_step_{i}(ctx):"]
            lines += [f'    """{step.title or f"Этап {i}"}."""']
            lines += _render_command_body(step.commands, f"adb_{i}")
            if step.adb_install_selected_apks:
                lines.append('    ctx.install_selected_apks(extra_args=["-g"])')
        elif step.type == "actions":
            for j, action in enumerate(step.actions, start=1):
                action_title = action.label or f"Действие {j}"
                lines += ["", "", f"def action_step_{i}_{j}(ctx):"]
                lines += [f'    """{action_title}."""']
                if action.kind == "grant_permissions":
                    lines.append("    packages = list_installed_packages(ctx)")
                    lines.append(
                        f"    package = ctx.ask_choice('Выберите приложение', packages, title={action_title!r})")
                    lines.append("    grant_all_permissions(ctx, package)")
                elif action.kind == "mock_location":
                    lines.append("    packages = list_installed_packages(ctx)")
                    lines.append(
                        f"    package = ctx.ask_choice('Выберите приложение', packages, title={action_title!r})")
                    lines.append("    set_mock_location_app(ctx, package)")
                else:
                    lines += _render_command_body(action.commands, f"actions_{i}_{j}")
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
                           f"        ctx.copy_selected_apks({step.usb_apks_dest!r})"]
            if step.usb_shared_folder:
                # Общий для многих моделей набор файлов (см.
                # StepSpec.usb_shared_folder) — копируется НАПРЯМУЮ из
                # cars/_shared/, а не из usb_files этой модели, чтобы не
                # дублировать одно и то же на сервере/у каждого техника
                # отдельно на модель. Работает независимо/вместе с обычным
                # usb_dir выше — не привязан к variant (общий набор один на
                # все варианты этого этапа).
                lines += [
                    f"    shared_dir = ctx.shared_dir / {step.usb_shared_folder!r} if ctx.shared_dir else None",
                    "    if shared_dir and shared_dir.exists():",
                    '        ctx.copy_dir(shared_dir, "")',
                ]
        elif step.type == "telnet":
            lines += ["", "", f"def telnet_step_{i}(ctx):"]
            lines += [f'    """{step.title or f"Этап {i}"}."""']
            for raw_line in (step.commands or [None]):
                if raw_line:
                    lines.append(f"    enable_adb_via_telnet(ctx, command={raw_line!r})")
                else:
                    lines.append("    enable_adb_via_telnet(ctx)")
        elif step.type == "uart":
            lines += ["", "", f"def uart_step_{i}(ctx):"]
            lines += [f'    """{step.title or f"Этап {i}"}."""']
            lines.append(f"    ser = open_uart(ctx, baudrate={step.uart_baudrate})")
            lines.append("    try:")
            if step.commands:
                for raw_line in step.commands:
                    cmd_bytes = (raw_line + "\r\n").encode()
                    lines.append(f"        ctx.log({('UART >> ' + raw_line)!r})")
                    lines.append(f"        ser.write({cmd_bytes!r})")
                    lines.append("        ctx.sleep(1.0)")
                    lines.append('        response = ser.read(4096).decode(errors="replace")')
                    lines.append("        if response:")
                    lines.append('            ctx.log(f"UART << {response}")')
            else:
                lines.append("        pass")
            lines.append("    finally:")
            lines.append("        ser.close()")

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
        # Не жёсткий parents[2] — модель с модификацией лежит на уровень
        # глубже (cars/<Марка>/<Модель>/<Модификация>/stages.py), чем
        # модель без неё (cars/<Марка>/<Модель>/stages.py), поэтому ищем
        # _shared/ подъёмом вверх до первого совпадения, а не по фиксированному
        # числу родителей.
        "for _p in Path(__file__).resolve().parents:",
        '    if (_p / "_shared").is_dir():',
        '        sys.path.insert(0, str(_p / "_shared"))',
        "        break",
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
            if step.usb_copy_selected_apks:
                entry.append('        "usb_copy_selected_apks": True,')
            if step.usb_shared_folder:
                entry.append(f'        "usb_shared_folder": {step.usb_shared_folder!r},')
            if step.variants:
                entry.append(f'        "variant_names": {[v.name for v in step.variants]!r},')
        elif step.type == "adb":
            run_expr = f"_with_connect(m.adb_step_{i})" if spec.wifi else f"m.adb_step_{i}"
            entry.append(f'        "run": {run_expr},')
        elif step.type == "uart":
            entry.append(f'        "run": m.uart_step_{i},')
        elif step.type == "telnet":
            entry.append(f'        "run": m.telnet_step_{i},')
        elif step.type == "actions":
            entry.append('        "actions": [')
            for j, action in enumerate(step.actions, start=1):
                action_title = action.label or f"Действие {j}"
                entry.append("            {")
                entry.append(f'                "label": {action_title!r},')
                entry.append(f'                "kind": {action.kind!r},')
                entry.append(f'                "run": m.action_step_{i}_{j},')
                entry.append("            },")
            entry.append("        ],")
        elif step.type == "exe" and step.exe_file:
            entry.append(
                f'        "exe_path": Path(__file__).resolve().parent / "files" / "exe_{i}" '
                f'/ {step.exe_file.name!r},'
            )
        elif step.type == "instruction" and step.instruction_blocks:
            # "instruction" — относительный путь-строка (см.
            # app/stage_runner.py: stage_instruction_html_path резолвит его
            # как model.dir / rel), а не Path-выражение, как "exe_path" выше.
            entry.append(f'        "instruction": {f"files/instruction_{i}/instruction.html"!r},')
        # "manual" — без "run"

        entry.append("    },")
        lines.append("\n".join(entry))
    lines.append("]")
    return "\n".join(lines) + "\n"
