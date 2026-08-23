"""Общий хелпер для моделей, где нужен доступ по UART (последовательный
порт, например через USB-UART переходник) — аналог того, что раньше
делалось руками через PuTTY: выбрать COM-порт и открыть соединение.

В отличие от wifi_adb.py/telnet_adb.py, здесь НЕТ обобщённой отправки
команды — протокол общения (какие байты слать, что ждать в ответ) у каждой
магнитолы свой и зашивается прямо в install.py конкретной модели, которая
использует open_uart(). Этот модуль отвечает только за выбор порта и
открытие соединения."""
import sys

import serial
import serial.tools.list_ports

CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def list_serial_ports() -> list[dict]:
    """Доступные COM-порты: [{"device": "COM5", "description": "..."}]."""
    return [
        {"device": p.device, "description": p.description or p.device}
        for p in serial.tools.list_ports.comports()
    ]


def open_uart(ctx, baudrate: int = 115200, port: str | None = None, timeout: float = 5) -> serial.Serial:
    """Открывает serial.Serial на выбранном порту. Если port не задан: ни
    одного порта — понятная ошибка (переходник не подключен/не определился
    Windows), один порт — берётся автоматически, несколько — предлагается
    выбрать (ctx.ask_choice). Дальнейшую отправку команд и чтение ответа
    (ser.write/ser.read/ser.close) делает уже код конкретной модели."""
    if not port:
        ports = list_serial_ports()
        if not ports:
            raise RuntimeError(
                "Не найдено ни одного COM-порта. Подключите USB-UART переходник и повторите."
            )
        if len(ports) == 1:
            port = ports[0]["device"]
            ctx.log(f"Найден единственный COM-порт: {port} ({ports[0]['description']})")
        else:
            labels = [f"{p['device']} — {p['description']}" for p in ports]
            choice = ctx.ask_choice("Выберите COM-порт:", labels, title="UART", allow_manual=False)
            port = choice.split(" — ", 1)[0]

    ctx.log(f"Открываю UART: {port} ({baudrate} бод)")
    try:
        return serial.Serial(port, baudrate=baudrate, timeout=timeout)
    except serial.SerialException as exc:
        raise RuntimeError(f"Не удалось открыть {port}: {exc}") from exc
