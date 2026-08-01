"""Haval M6 — создано мастером "Добавить машину...".
Отредактируйте вручную, если нужно что-то сложнее, чем список ADB-команд
и файлы для флешки."""


def usb_step_1(ctx):
    """Понижение версии прошивки."""
    usb_dir = ctx.usb_file("step_1")
    if usb_dir.exists():
        ctx.copy_dir(usb_dir, "")


ADB_STEP_2_COMMANDS = [
]


def adb_step_2(ctx):
    """Этап 3."""
    for command in ADB_STEP_2_COMMANDS:
        ctx.shell(command, check=False)


def usb_step_3(ctx):
    """Установка АПК с флешки."""
    usb_dir = ctx.usb_file("step_3")
    if usb_dir.exists():
        ctx.copy_dir(usb_dir, "")
