"""Haval M6 — этапы установки.
Создано мастером "Добавить машину...", можно редактировать вручную."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_shared"))
from load_sibling import load_install  # noqa: E402

m = load_install(__file__)

STAGES = [
    {
        "type": 'usb',
        "title": 'Понижение версии прошивки',
        "description": 'Если у вас прошивка 8 то необходимо понизить ее до версии 7, запишите на флешку и запустите обновление через меню авто',
        "run": m.usb_step_1,
    },
    {
        "type": 'adb',
        "title": 'Этап 3',
        "run": m.adb_step_2,
    },
    {
        "type": 'usb',
        "title": 'Установка АПК с флешки',
        "run": m.usb_step_3,
    },
]
