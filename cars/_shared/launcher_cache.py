"""Очистка кэша лаунчера — для кнопки «Дополнительные действия» моделей Changan («#py launcher_cache.clear»; владелец,
15.10.2026: «чистку лаунчера кнопкой тоже обязательно добавь»). Так делает ChanganInstaller после каждой установки
(`rm -rf /data/data/com.iflytek.autofly.launcher/cache/*` под adb root): лаунчер показывает список приложений из своего
кэша, и только что поставленные приложения в нём не появляются. Лаунчеры у Changan разные (iFlytek, WT, AutoAI…) —
чистим кэш того, что стоит домашним экраном, и iFlytek, если он есть, затем перезапускаем лаунчер, чтобы он перечитал
список приложений. Данные чужого приложения доступны только root: сначала adb root — adbd перезапускается и связь
рвётся, поэтому ждём магнитолу и повторяем. Общий для ПК и Android (android/.../python/py_runner.py)."""
from __future__ import annotations

IFLYTEK = "com.iflytek.autofly.launcher"
_HOME = ("cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME "
         "2>/dev/null | tail -n 1 | cut -d/ -f1")
_DONE = "msqd_done"
_CLEARED = "msqd_cleared:"
_SCRIPT = (f"home=$({_HOME}); for p in $home {IFLYTEK}; do "
           '[ -n "$p" ] && [ -d "/data/data/$p" ] || continue; '
           f'rm -rf "/data/data/$p/cache/"* && am force-stop "$p" && echo "{_CLEARED}$p"; done; echo {_DONE}')
_NO_ROOT = ("cannot run as root", "root access is disabled", "not allowed")
_ATTEMPTS = 4


def _text(result) -> str:
    return ((getattr(result, "stdout", "") or "") + (getattr(result, "stderr", "") or "")).strip()


def clear(ctx) -> list:
    """Список пакетов, чей кэш очищен. Ошибка — понятным текстом (нет root, магнитола не вернулась)."""
    ctx.log("Очищаю кэш лаунчера — нужны права root (adb root)...")
    root = _text(ctx.adb("root", check=False))
    if any(marker in root.lower() for marker in _NO_ROOT):
        raise RuntimeError(f"Магнитола не дала права root — кэш лаунчера не очистить (adb root: {root})")
    restarted = "already running as root" not in root.lower()
    if restarted:
        ctx.log("adb перезапускается с правами root — жду магнитолу (если на телефоне появится запрос доступа к USB, "
                "разрешите)...")
    output = ""
    for attempt in range(_ATTEMPTS):
        if restarted:
            ctx.sleep(3)
            try:
                ctx.wait_for_device(timeout=60)
            except Exception as exc:  # noqa: BLE001 — разные классы ошибок на ПК и Android
                raise RuntimeError(f"Магнитола не вернулась после adb root: {exc}") from exc
        try:
            output = _text(ctx.shell(_SCRIPT, check=False, timeout=60))
        except Exception:  # noqa: BLE001 — связь ещё не вернулась (adbd перезапускается) — ждём и повторяем
            if attempt == _ATTEMPTS - 1:
                raise
            restarted = True
            continue
        if _DONE in output:
            break
        restarted = True
    cleared = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith(_CLEARED) and line[len(_CLEARED):] not in cleared:
            cleared.append(line[len(_CLEARED):])
    if cleared:
        ctx.log(f"Кэш лаунчера очищен: {', '.join(cleared)}. Лаунчер перезапущен — новые приложения появятся в списке.")
    elif _DONE in output:
        ctx.log("Лаунчер на магнитоле не найден — чистить нечего.")
    else:
        raise RuntimeError(f"Не удалось очистить кэш лаунчера: {output or 'магнитола не ответила'}")
    return cleared
