"""Режим стелс — кнопки «Дополнительных действий» всех моделей (владелец, 09.11.2026: «показывает список установленных
приложений, дает поставить галочки, и скрывает (pm-disable) их, а после визита к дилеру одной кнопкой можно вернуть все
назад»).

Скрыть = «pm disable-user --user 0»: приложение пропадает с экрана и не запускается, но остаётся установленным со всеми
данными (как кнопка «Отключить приложение», adb_permissions.disable_app). В списке — только сторонние приложения.

Что скрыто, программа записывает на саму магнитолу (STATE_FILE, /data/local/tmp доступен adb shell и переживает
перезагрузку): вернуть можно с любого телефона или компьютера. Вместе с приложением запоминаются его службы
спецвозможностей и доступа к уведомлениям (кнопки руля, GInputBridge…) — при возврате программа включает их снова, если
прошивка их выключила.

Окно с галочками: заголовок вопроса помечен невидимым MULTI_MARK — интерфейс программы, который это умеет (бандл
интерфейса после 1.1.22), рисует список с галочками и отвечает JSON-списком. Прежний интерфейс знака не видит и
показывает обычный выбор одного — тогда спрашиваем по одному, пока техник не выберет «Готово».
Общий для ПК и Android (android/.../python/py_runner.py)."""
from __future__ import annotations

import json
import re

import adb_permissions  # тот же каталог cars/_shared — список сторонних приложений с повторами (Haval H3/H7)

STATE_FILE = "/data/local/tmp/msqd_stealth"
MULTI_MARK = "⁣"
TITLE = "Режим стелс"
HIDE_LABEL = "Режим стелс: скрыть приложения"
RESTORE_LABEL = "Режим стелс: вернуть всё"
DONE_CHOICE = "Готово — скрыть выбранные"

_END = "msqd_end"
_SAVED = "msqd_saved"
_A11Y = "enabled_accessibility_services"
_NLS = "enabled_notification_listeners"
_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
_COMPONENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*/[A-Za-z0-9_.$]+$")
_KINDS = ("p", "a", "n")  # p — пакет, a — служба спецвозможностей, n — доступ к уведомлениям


def _text(result) -> str:
    return ((getattr(result, "stdout", "") or "") + (getattr(result, "stderr", "") or "")).strip()


def _owner(component: str) -> str:
    return component.split("/", 1)[0]


def read_state(ctx) -> dict:
    """Что программа скрыла на этой магнитоле: {"p": пакеты, "a": службы спецвозможностей, "n": доступ к уведомлениям}."""
    out = _text(ctx.shell(f"cat {STATE_FILE} 2>/dev/null; echo {_END}", check=False, timeout=30))
    if _END not in out:
        raise RuntimeError("Магнитола не ответила — подождите несколько секунд и нажмите «Выполнить» ещё раз.")
    state = {kind: [] for kind in _KINDS}
    for line in out.split(_END, 1)[0].splitlines():
        kind, _, value = line.strip().partition(" ")
        pattern = _PACKAGE_RE if kind == "p" else _COMPONENT_RE
        if kind in state and pattern.match(value) and value not in state[kind]:
            state[kind].append(value)
    return state


def _write_state(ctx, state: dict) -> bool:
    lines = [f"{kind} {value}" for kind in _KINDS for value in state.get(kind, [])]
    if not lines:
        ctx.shell(f"rm -f {STATE_FILE}", check=False, timeout=30)
        return True
    quoted = " ".join(f"'{line}'" for line in lines)  # имена проверены регэкспами — кавычек в них нет
    return _SAVED in _text(ctx.shell(f"printf '%s\\n' {quoted} > {STATE_FILE} && echo {_SAVED}", check=False, timeout=30))


def _packages(ctx, flags: str) -> list[str]:
    """pm list packages без повторов и записи в журнал: пустой список отключённых — обычное дело, не сбой."""
    return [p for p in adb_permissions._packages_in(ctx.shell(f"pm list packages {flags}", check=False, timeout=20))
            if _PACKAGE_RE.match(p)]


def _secure_list(ctx, key: str) -> list[str]:
    value = _text(ctx.shell(f"settings get secure {key}", check=False, timeout=20))
    if not value or value == "null":
        return []
    return [c for c in value.split(":") if _COMPONENT_RE.match(c)]


def _ask_many(ctx, packages: list[str], prompt: str) -> list[str]:
    answer = str(ctx.ask_choice(prompt, packages, title=TITLE + MULTI_MARK, allow_manual=False) or "").strip()
    if answer.startswith("["):
        try:
            picked = set(json.loads(answer))
        except ValueError:
            picked = set()
        return [p for p in packages if p in picked]
    chosen = [answer] if answer in packages else []  # прежний интерфейс: выбор одного — дальше по одному
    while True:
        rest = [p for p in packages if p not in chosen]
        if not rest:
            break
        asked = "Выбрано: " + ", ".join(chosen) + ". Скрыть ещё одно приложение?" if chosen else prompt
        pick = str(ctx.ask_choice(asked, [DONE_CHOICE] + rest, title=TITLE, allow_manual=False) or "").strip()
        if pick not in rest:
            break
        chosen.append(pick)
    return [p for p in packages if p in chosen]


def hide(ctx, restore_label: str = RESTORE_LABEL) -> list:
    """Спросить, какие сторонние приложения скрыть, и скрыть их. Возвращает скрытые."""
    ctx.log("Режим стелс: получаю список приложений...")
    state = read_state(ctx)
    installed = adb_permissions.list_installed_packages(ctx)
    if not installed:
        ctx.log("Не удалось получить список приложений: магнитола не ответила или оборвалась связь. Подождите несколько "
                "секунд и нажмите «Выполнить» ещё раз.")
        return []
    disabled = set(_packages(ctx, "-d"))
    visible = [p for p in installed if p not in disabled and p not in state["p"]]
    if not visible:
        ctx.log("Режим стелс: скрывать нечего — " + ("все сторонние приложения уже скрыты." if state["p"]
                                                        else "сторонних приложений на магнитоле нет."))
        return []
    chosen = _ask_many(ctx, visible, "Отмеченные приложения пропадут с экрана магнитолы. Вернуть — кнопкой "
                                     f"«{restore_label}».")
    if not chosen:
        ctx.log("Режим стелс: ничего не выбрано.")
        return []
    services = {"a": _secure_list(ctx, _A11Y), "n": _secure_list(ctx, _NLS)}
    planned = {"p": state["p"] + [p for p in chosen if p not in state["p"]]}
    for kind in ("a", "n"):
        planned[kind] = state[kind] + [c for c in services[kind] if _owner(c) in chosen and c not in state[kind]]
    # Сначала запись — потом скрытие: без записи «вернуть всё» не найдёт скрытое.
    if not _write_state(ctx, planned):
        ctx.log(f"Режим стелс: не удалось записать список на магнитолу ({STATE_FILE}) — без него кнопка «{restore_label}» "
                "не сработает, поэтому ничего не скрываю.")
        return []
    hidden, failed = [], []
    for package in chosen:
        out = _text(ctx.shell(f"pm disable-user --user 0 {package}", check=False))
        if "new state" in out.lower():
            hidden.append(package)
        else:
            failed.append(package)
            ctx.log(f"Не удалось скрыть {package}: {out[:200] or 'магнитола не ответила'}")
    if failed:
        planned = {kind: [v for v in values if (v if kind == "p" else _owner(v)) not in failed]
                   for kind, values in planned.items()}
        _write_state(ctx, planned)
    if hidden:
        ctx.log(f"Режим стелс: скрыто {len(hidden)} — {', '.join(hidden)}. Вернуть — кнопкой «{restore_label}».")
    return hidden


def _put_secure(ctx, key: str, components: list) -> None:
    ctx.shell(f"settings put secure {key} '{':'.join(components)}'", check=False)


def _services_back(ctx, state: dict, back: list) -> None:
    """Службы вернувшихся приложений. Спецвозможности Android при скрытии выключает сам (убирает из списка), а когда
    приложение возвращается, держит его службу «упавшей» и не запускает, даже если она снова в списке (эмулятор
    Android 15, 09.11.2026: Enabled — она, Bound — пусто, Crashed — она). «Упавшей» её перестаёт считать, только когда
    она уходит из списка: вписать → убрать → вписать снова, с паузами (записи подряд Android видит одним изменением).
    Доступ к уведомлениям Android не снимает — подтверждаем его ещё раз."""
    a11y = [c for c in state["a"] if _owner(c) in back]
    if a11y:
        others = [c for c in _secure_list(ctx, _A11Y) if c not in a11y]
        _put_secure(ctx, _A11Y, others + a11y)
        adb_permissions._pause(ctx, 1.5)
        _put_secure(ctx, _A11Y, others)
        adb_permissions._pause(ctx, 1.5)
        _put_secure(ctx, _A11Y, others + a11y)
        ctx.shell("settings put secure accessibility_enabled 1", check=False)
        ctx.log("Снова включены службы спецвозможностей: " + ", ".join(a11y))
    listeners = [c for c in state["n"] if _owner(c) in back]
    if listeners:
        current = _secure_list(ctx, _NLS)
        missing = [c for c in listeners if c not in current]
        if missing:
            _put_secure(ctx, _NLS, current + missing)
        for component in listeners:
            ctx.shell(f"cmd notification allow_listener '{component}'", check=False)
        ctx.log("Снова включён доступ к уведомлениям: " + ", ".join(listeners))


def restore(ctx) -> list:
    """Вернуть всё, что скрыл режим стелс на этой магнитоле. Возвращает включённые пакеты."""
    state = read_state(ctx)
    if not state["p"]:
        others = _packages(ctx, "-d -3")
        if others:
            ctx.log("Режим стелс: программа на этой магнитоле ничего не скрывала. Отключены другим способом: "
                    f"{', '.join(others)} — включить можно кнопкой «Включить приложение».")
        else:
            ctx.log("Режим стелс: скрытых приложений нет.")
        return []
    ctx.log(f"Режим стелс: возвращаю {len(state['p'])} приложений...")
    back, gone, left = [], [], []
    for package in state["p"]:
        out = _text(ctx.shell(f"pm enable {package}", check=False))
        low = out.lower()
        if "new state" in low:
            back.append(package)
        elif "unknown package" in low or "does not exist" in low:
            gone.append(package)
        else:
            left.append(package)
            ctx.log(f"Не удалось вернуть {package}: {out[:200] or 'магнитола не ответила'}")
    _services_back(ctx, state, back)
    _write_state(ctx, {"p": left, "a": [c for c in state["a"] if _owner(c) in left],
                       "n": [c for c in state["n"] if _owner(c) in left]})
    if gone:
        ctx.log(f"Удалены с магнитолы, пока были скрыты: {', '.join(gone)}.")
    if back:
        ctx.log(f"Режим стелс: возвращено {len(back)} — {', '.join(back)}.")
    return back
