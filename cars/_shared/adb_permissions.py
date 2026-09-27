"""Список установленных приложений + выдача разрешений/спецдоступов и
включение/отключение приложений через ADB для "actions"-этапа (см.
app/car_generator.py: StepSpec.actions, ActionSpec.kind ==
"grant_permissions"/"mock_location"/"disable_app"/"enable_app"). На многих
китайских магнитолах нет стандартного экрана "Разрешения приложения" в
настройках — единственный способ дать приложению доступ (камера,
местоположение, показ поверх других окон, спецвозможности и т.п.) или
отключить мешающее предустановленное приложение (конкурирующая навигация,
голосовой ассистент) — руками через adb shell, что и делает этот модуль
вместо техника."""
from __future__ import annotations
import re

# Полный список разрешений на устройстве заранее неизвестен, поэтому
# основной путь — разобрать "requested permissions:" из dumpsys package
# конкретного приложения (что оно само просило в манифесте) и выдать именно
# их. Этот список — запасной вариант на случай, если разбор не сработал
# (нестандартный формат dumpsys на части прошивок) — тогда выдаём разрешения
# "вслепую" из самых частых, pm grant просто молча ничего не сделает для тех,
# что приложение не запрашивало.
_COMMON_DANGEROUS_PERMISSIONS = [
    "android.permission.CAMERA",
    "android.permission.RECORD_AUDIO",
    "android.permission.ACCESS_FINE_LOCATION",
    "android.permission.ACCESS_COARSE_LOCATION",
    "android.permission.ACCESS_BACKGROUND_LOCATION",
    "android.permission.READ_CONTACTS",
    "android.permission.WRITE_CONTACTS",
    "android.permission.READ_CALENDAR",
    "android.permission.WRITE_CALENDAR",
    "android.permission.READ_SMS",
    "android.permission.SEND_SMS",
    "android.permission.RECEIVE_SMS",
    "android.permission.READ_PHONE_STATE",
    "android.permission.READ_PHONE_NUMBERS",
    "android.permission.CALL_PHONE",
    "android.permission.READ_CALL_LOG",
    "android.permission.WRITE_CALL_LOG",
    "android.permission.READ_EXTERNAL_STORAGE",
    "android.permission.WRITE_EXTERNAL_STORAGE",
    "android.permission.READ_MEDIA_IMAGES",
    "android.permission.READ_MEDIA_VIDEO",
    "android.permission.READ_MEDIA_AUDIO",
    "android.permission.BODY_SENSORS",
    "android.permission.ACTIVITY_RECOGNITION",
    "android.permission.POST_NOTIFICATIONS",
    "android.permission.BLUETOOTH_CONNECT",
    "android.permission.BLUETOOTH_SCAN",
    "android.permission.BLUETOOTH_ADVERTISE",
    "android.permission.NEARBY_WIFI_DEVICES",
]

# "Спецдоступы" — НЕ обычные runtime-разрешения (pm grant их не выдаёт),
# управляются через AppOpsManager. android:mock_location — отдельно, см.
# set_mock_location_app ниже (сам по себе бесполезен без выбора приложения).
# MANAGE_EXTERNAL_STORAGE ("доступ ко всем файлам") сюда добавлен не сразу —
# первая версия выдавала его через pm grant вместе с обычными runtime-
# разрешениями (он тоже попадает в "requested permissions:" из dumpsys), и
# это тихо ничего не давало: с Android 11 это appops-доступ, pm grant для
# него не предназначен и код возврата не отражает, что реального эффекта
# не было. Баг всплыл на Geely Cityray/Monji (OneOS, приложения ставятся не
# штатным adb install, а через dex-хелпер — см. apk_install.py:
# dex_shell_install) — файловому менеджеру не давался доступ ко всем
# файлам, хотя лог показывал "разрешения выданы".
_APPOPS = {
    "android.permission.SYSTEM_ALERT_WINDOW": "SYSTEM_ALERT_WINDOW",
    "android.permission.WRITE_SETTINGS": "WRITE_SETTINGS",
    "android.permission.PACKAGE_USAGE_STATS": "GET_USAGE_STATS",
    "android.permission.MANAGE_EXTERNAL_STORAGE": "MANAGE_EXTERNAL_STORAGE",
}

# На части прошивок (подтверждено на Geely Cityray/Monji, OneOS) обычная
# форма "appops set <пакет> MANAGE_EXTERNAL_STORAGE allow" молча не
# применяется именно после установки через dex-хелпер (см. apk_install.py:
# dex_shell_install) — похоже, резолвинг package -> uid для этого op не
# успевает обновиться. Техник вручную нашёл рабочую команду с --uid (не
# стандартный флаг AOSP appops, добавлен в прошивку этого OEM) — она заново
# резолвит uid и реально применяет доступ. Выполняем оба варианта: на
# прошивках без --uid вторая команда просто молча завершится ошибкой
# (check=False), первая уже сработала.
_MANAGE_EXTERNAL_STORAGE_OP = "MANAGE_EXTERNAL_STORAGE"

# Доп. appops без прямого аналога в списке "запрошенных разрешений" из
# манифеста (не permission, а именно op) — выдаются безусловно всем, не
# только по совпадению с requested-permissions, как и _APPOPS выше:
#   REQUEST_INSTALL_PACKAGES — приложению можно ставить APK без диалога
#   "Установка из неизвестных источников" (актуально для магазинов вроде
#   RuStore, которые сами ставят другие приложения).
#   ACTIVATE_VPN — VPN-клиент может поднять соединение без системного
#   диалога подтверждения VPN.
#   ACCESS_RESTRICTED_SETTINGS — с Android 13 система блокирует включение
#   спецвозможностей и доступа к уведомлениям для приложений, поставленных
#   не через "доверенный" магазин (в т.ч. через adb/сторонний sideload) —
#   ровно наш случай. Без этого appops _enable_accessibility_service/
#   _enable_notification_listener ниже пишут нужные settings, но система
#   их не применяет, и в самом приложении провал выглядит как обычный
#   "нужно открыть", будто мы вообще не пытались. Безвредно для приложений,
#   которым это не нужно.
#   SCHEDULE_EXACT_ALARM — точные будильники/таймеры (Android 12+: без
#   этого op приложение не может ставить точные срабатывания по времени).
#   RUN_ANY_IN_BACKGROUND / RUN_IN_BACKGROUND — работа в фоне без
#   ограничений (дополняет белый список Doze ниже: сам Doze-список не
#   снимает "фоновые ограничения" приложения из настроек Android 9+).
#   MANAGE_MEDIA — изменение/удаление медиафайлов без запроса подтверждения
#   (Android 12+). Незнакомый прошивке op просто молча не применится
#   (check=False), остальные сработают.
_EXTRA_APPOPS = ["REQUEST_INSTALL_PACKAGES", "ACTIVATE_VPN", "ACCESS_RESTRICTED_SETTINGS",
                 "SCHEDULE_EXACT_ALARM", "RUN_ANY_IN_BACKGROUND", "RUN_IN_BACKGROUND", "MANAGE_MEDIA"]

# WRITE_SECURE_SETTINGS — обычное (не appops) разрешение, но с protection
# level signature|privileged — недоступно приложению через диалог, зато
# выдаётся через adb shell pm grant (shell сам обладает этой привилегией),
# что и есть единственный практический способ дать его сторонним программам.
_WRITE_SECURE_SETTINGS = "android.permission.WRITE_SECURE_SETTINGS"

_REQUESTED_PERMISSIONS_RE = re.compile(r"^requested permissions:\s*$")
_PERMISSION_LINE_RE = re.compile(r"^([\w.]+)(?::.*)?$")
_COMPONENT_NAME_RE = re.compile(r"name=([\w.]+)")
# Компонент в коротком виде (ComponentName.flattenToShortString) — так он
# стоит в "Service Resolver Table" реального dumpsys package, в той же строке,
# что и разрешение службы:
#   "5c52dd ace.jun.simplecontrol/.service.AccService filter cef2b51 permission
#    android.permission.BIND_ACCESSIBILITY_SERVICE"
_SHORT_COMPONENT_RE = re.compile(r"([\w.]+)/([\w.$]+)")


# Метка «поставлено программой» в /system/app/<пакет>/ (app/install_context.py: SYSTEM_APP_MARKER): одна строка,
# «magicsqd» или «magicsqd mock_location» — приложению нужно фиктивное местоположение (GPS-приложение с пометкой).
_SYSTEM_APP_MARKERS_COMMAND = 'for f in /system/app/*/.magicsqd; do [ -f "$f" ] && echo "$f $(cat "$f")"; done'
_OUR_SYSTEM_APP_RE = re.compile(r"^/system/app/([\w.]+)/\.magicsqd(?:[ \t]+(.*))?$", re.MULTILINE)


def _pm_packages(ctx, flag: str = "") -> list[str]:
    result = ctx.shell(f"pm list packages {flag}".strip(), check=False)
    packages = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("package:"):
            packages.append(line[len("package:"):].strip())
    return packages


def _system_app_markers(ctx) -> dict[str, str]:
    """Приложения, которые программа сама положила в /system/app (способ «в системную папку», BAIC U5 Plus):
    пакет → текст метки. Android считает их системными, но это не штатные приложения магнитолы: их можно
    запускать, выдавать им разрешения, отключать и удалять, как сторонние."""
    result = ctx.shell(_SYSTEM_APP_MARKERS_COMMAND, check=False)
    return {m.group(1): (m.group(2) or "").strip() for m in _OUR_SYSTEM_APP_RE.finditer(result.stdout or "")}


def _system_apps_by_us(ctx) -> set[str]:
    return set(_system_app_markers(ctx))


def grant_system_apps_permissions(ctx) -> None:
    """Все разрешения приложениям, которые программа положила в /system/app, — всем сразу, без выбора. Android
    видит их только после перезагрузки магнитолы, а ADB на BAIC U5 Plus перезагрузку не переживает (владелец,
    2026-09-27): поэтому это отдельный этап — после перезагрузки и повторного включения ADB. Приложению с
    пометкой GPS (метка «mock_location») — ещё и фиктивное местоположение, как после обычной установки."""
    markers = _system_app_markers(ctx)
    if not markers:
        raise _stop("На магнитоле нет приложений, записанных программой в системную папку — сначала этап установки.")
    known = set(_pm_packages(ctx, "-s"))
    waiting = sorted(package for package in markers if package not in known)
    for package in sorted(markers):
        if package in waiting:
            continue
        grant_all_permissions(ctx, package)
        if "mock_location" in markers[package].split():
            set_mock_location_app(ctx, package)
    if waiting:
        raise _stop("Android ещё не видит " + ", ".join(waiting) + " — приложения появятся после перезагрузки "
                    "магнитолы. Перезагрузите её, снова включите ADB в инженерном меню и повторите.")


def _stop(message: str) -> Exception:
    """Понятный итог этапа без «Ошибка установки:» — InstallCancelled программы (ПК); вне её — RuntimeError."""
    try:
        from app.install_context import InstallCancelled
    except ImportError:
        return RuntimeError(message)
    return InstallCancelled(message)


def list_installed_packages(ctx, third_party_only=True):
    """Список пакетов, установленных на магнитоле (для диалога выбора
    приложения — ctx.ask_choice). third_party_only=True (по умолчанию) —
    без системных пакетов производителя/Android, иначе список из сотен
    записей неудобно листать ради обычно нужных технику сторонних APK.
    Поставленные программой в /system/app — в списке (см. _system_apps_by_us),
    если Android их уже видит (после перезагрузки)."""
    packages = _pm_packages(ctx, "-3" if third_party_only else "")
    if third_party_only:
        ours = _system_apps_by_us(ctx)
        if ours:
            packages += sorted((ours & set(_pm_packages(ctx, "-s"))) - set(packages))
    return sorted(packages)


def _parse_requested_permissions(dumpsys_output: str) -> list[str]:
    lines = dumpsys_output.splitlines()
    result = []
    in_block = False
    for raw in lines:
        line = raw.strip()
        if not in_block:
            if _REQUESTED_PERMISSIONS_RE.match(line):
                in_block = True
            continue
        if not line:
            break
        m = _PERMISSION_LINE_RE.match(line)
        if m and "." in m.group(1):
            result.append(m.group(1))
        else:
            break
    return result


def _full_class_name(package: str, cls: str) -> str:
    if cls.startswith("."):
        return package + cls
    if "." not in cls:
        return f"{package}.{cls}"
    return cls


def _find_service_component(package: str, dumpsys_output: str, marker: str) -> str | None:
    """Best-effort поиск класса службы (спецвозможности/слушателя
    уведомлений — marker разный, логика одна) в dumpsys package.

    Основной путь — компонент в той же строке, что и marker (см.
    _SHORT_COMPONENT_RE): так выглядит реальный вывод Android 8+, проверено
    на эмуляторе Android 9 и сверено с исходниками AOSP 12. Раньше здесь был
    только поиск "name=..." в 15 строках ВЫШЕ marker — в реальном dumpsys
    package такого нет, и служба не находилась ни у одного приложения
    (жалоба 2026-09-23, Haval Jolion 2026: Simple Control и оригинальный
    Floating Dock — "Служба спецвозможностей не найдена в dumpsys", хотя
    Simple Control её объявляет). Старый разбор оставлен запасным на случай
    нестандартного формата прошивки. Если не нашли ни так, ни так — просто
    не включаем эту часть (остальные разрешения всё равно выдаются, а
    вызывающие честно логируют, что именно не получилось найти)."""
    lines = dumpsys_output.splitlines()
    for line in lines:
        if marker not in line:
            continue
        for m in _SHORT_COMPONENT_RE.finditer(line):
            if m.group(1) == package:
                return f"{package}/{_full_class_name(package, m.group(2))}"
    for i, line in enumerate(lines):
        if marker not in line:
            continue
        for back in reversed(lines[max(0, i - 15):i]):
            m = _COMPONENT_NAME_RE.search(back.strip())
            if m:
                return f"{package}/{_full_class_name(package, m.group(1))}"
    return None


def _service_declared(dumpsys_output: str, marker: str) -> bool:
    """Объявляет ли приложение такую службу: у службы в dumpsys package строка вида «… filter …
    permission android.permission.BIND_…» (у старых прошивок — «permission=…»). Строка из «requested
    permissions» — не служба."""
    return re.search(rf"\bpermission[\s=]+android\.permission\.{marker}\b", dumpsys_output) is not None


def _enable_accessibility_service(ctx, package: str, dumpsys_output: str) -> None:
    component = _find_service_component(package, dumpsys_output, "BIND_ACCESSIBILITY_SERVICE")
    if not component:
        # У большинства приложений такой службы нет вовсе — это не ошибка, и в лог это не пишется: раньше
        # «Служба … не найдена в dumpsys» шла у каждого приложения дважды и выглядела как сбой (лог #799).
        # Предупреждаем, только если служба объявлена, а определить её не удалось.
        if _service_declared(dumpsys_output, "BIND_ACCESSIBILITY_SERVICE"):
            ctx.log(f"Внимание: у {package} есть служба спецвозможностей, но определить её не удалось — "
                    "включите её вручную (Настройки → Спецвозможности).")
        return
    current = (ctx.shell("settings get secure enabled_accessibility_services", check=False).stdout or "").strip()
    existing = [c for c in current.split(":") if c] if current and current != "null" else []
    if component not in existing:
        existing.append(component)
        ctx.shell(f"settings put secure enabled_accessibility_services {':'.join(existing)}", check=False)
    ctx.shell("settings put secure accessibility_enabled 1", check=False)
    ctx.log(f"Служба специальных возможностей включена: {component}")


def _enable_notification_listener(ctx, package: str, dumpsys_output: str) -> None:
    """Доступ к уведомлениям (NotificationListenerService) — тот же
    принцип, что и спецвозможности выше: обычно даётся только через экран
    настроек, тут выставляем settings secure напрямую. cmd notification
    allow_listener — более новый и надёжный путь (Android 11+), settings
    put secure enabled_notification_listeners ниже — для более старых
    прошивок, где этой команды ещё нет. См. также ACCESS_RESTRICTED_SETTINGS
    в _EXTRA_APPOPS — без него на Android 13+ ни то, ни другое не
    применяется для приложений, поставленных не через "доверенный" магазин."""
    component = _find_service_component(package, dumpsys_output, "BIND_NOTIFICATION_LISTENER_SERVICE")
    if not component:
        # См. _enable_accessibility_service: службы нет — молчим, есть, но не разобрали — предупреждаем.
        if _service_declared(dumpsys_output, "BIND_NOTIFICATION_LISTENER_SERVICE"):
            ctx.log(f"Внимание: у {package} есть служба доступа к уведомлениям, но определить её не удалось — "
                    "включите доступ вручную (Настройки → Уведомления → Доступ к уведомлениям).")
        return
    current = (ctx.shell("settings get secure enabled_notification_listeners", check=False).stdout or "").strip()
    existing = [c for c in current.split(":") if c] if current and current != "null" else []
    if component not in existing:
        existing.append(component)
        ctx.shell(f"settings put secure enabled_notification_listeners {':'.join(existing)}", check=False)
    ctx.shell(f"cmd notification allow_listener {component}", check=False)
    ctx.log(f"Доступ к уведомлениям включён: {component}")


def grant_all_permissions(ctx, package: str) -> None:
    """Выдаёт приложению все разрешения, которые оно запрашивает в
    манифесте (обычные runtime + WRITE_SECURE_SETTINGS), плюс "спецдоступы"
    через AppOps (показ поверх других окон, изменение системных настроек,
    статистика использования, доступ ко всем файлам, установка APK без
    диалога, активация VPN без диалога, снятие ограничения Android 13+ на
    включение спецвозможностей/уведомлений для не-"доверенных" приложений)
    и, если удалось найти, включает его службу специальных возможностей и
    доступ к уведомлениям — то, что на большинстве магнитол нельзя сделать
    штатным экраном настроек. Плюс освобождает приложение от ограничений
    энергосбережения (Doze/App Standby) — иначе Android рано или поздно
    убивает его в фоне, частая жалоба именно на магнитолах."""
    ctx.log(f"Выдаю разрешения: {package}")
    dumpsys_output = ctx.shell(f"dumpsys package {package}", check=False).stdout or ""
    requested = _parse_requested_permissions(dumpsys_output)
    if not requested:
        requested = list(_COMMON_DANGEROUS_PERMISSIONS)

    # pm grant молча ничего не делает для разрешения, которое приложение не
    # запрашивало (обычное дело для запасного списка _COMMON_DANGEROUS_
    # PERMISSIONS) — код возврата это честно отражает, поэтому здесь (в
    # отличие от остальных вызовов ниже) стоит посчитать реальный итог, а
    # не просто отрапортовать "выдано" вслепую.
    granted = failed = 0
    for perm in requested:
        if perm in _APPOPS:
            continue  # выдаётся ниже через appops, не pm grant
        result = ctx.shell(f"pm grant {package} {perm}", check=False)
        if result.returncode == 0:
            granted += 1
        else:
            failed += 1

    for op in _APPOPS.values():
        ctx.shell(f"appops set {package} {op} allow", check=False)
        if op == _MANAGE_EXTERNAL_STORAGE_OP:
            ctx.shell(f"appops set --uid {package} {op} allow", check=False)
    for op in _EXTRA_APPOPS:
        ctx.shell(f"appops set {package} {op} allow", check=False)
    ctx.shell(f"pm grant {package} {_WRITE_SECURE_SETTINGS}", check=False)
    ctx.shell(f"dumpsys deviceidle whitelist +{package}", check=False)

    _enable_accessibility_service(ctx, package, dumpsys_output)
    _enable_notification_listener(ctx, package, dumpsys_output)
    if failed:
        # "Внимание" в начале — сознательно (см. app/web/frontend/js/
        # log_format.js: classifyLogLevel), чтобы эта строка красилась как
        # предупреждение, а не как обычный успех — частичная выдача не
        # обязательно проблема (запасной список permissions включает и то,
        # что приложение не запрашивало), но стоит внимания технику.
        ctx.log(f"Внимание: разрешения выданы частично — {granted} из {granted + failed} "
                f"(остальные приложению не нужны или недоступны на этой прошивке).")
    else:
        ctx.log(f"Все разрешения выданы ({granted}).")


def set_mock_location_app(ctx, package: str) -> None:
    """Назначает приложение "приложением для фиктивных местоположений"
    (имитация GPS, как в Настройки → Для разработчиков → Выбор приложения
    для фиктивных местоположений) и включает саму возможность. appops —
    актуальный механизм (Android 6+); settings put secure mock_location —
    для более старых прошивок, где appops эту операцию не знает."""
    ctx.log(f"Приложение для фиктивных местоположений: {package}")
    ctx.shell(f"appops set {package} android:mock_location allow", check=False)
    ctx.shell("settings put secure mock_location 1", check=False)
    ctx.log("Готово.")


_RESUMED_COMPONENT_RE = re.compile(r"u\d+ (\S+/\S+)")
_DISPLAY_HEADER_RE = re.compile(r"Display #(\d+)")


def _shell_text(ctx, command: str) -> str:
    result = ctx.shell(command, check=False)
    return ((result.stdout or "") + (result.stderr or "")).strip()


def _on_screen(ctx, package: str) -> tuple[int | None, list[str]] | None:
    """Что сейчас на экранах магнитолы: (номер экрана с нашим приложением или None, приложения на основном экране).
    None — магнитола не ответила (нечего проверять). «Display #N» — заголовок экрана, строки mResumedActivity
    (Android 9) / ResumedActivity (10+) — приложение на нём; topResumedActivity не берём — он печатается после
    всех экранов и приписался бы последнему."""
    out = _shell_text(ctx, "dumpsys activity activities | grep -E 'Display #|ResumedActivity'")
    if not out:
        return None
    display, ours, main = 0, None, []
    for line in out.splitlines():
        header = _DISPLAY_HEADER_RE.search(line)
        if header:
            display = int(header.group(1))
            continue
        if "ResumedActivity" not in line or "topResumedActivity" in line:
            continue
        found = _RESUMED_COMPONENT_RE.search(line)
        if not found:
            continue
        component = found.group(1)
        if display == 0 and component not in main:
            main.append(component)
        if ours is None and component.startswith(package + "/"):
            ours = display
    return ours, main


def launch_main_activity(ctx, package: str) -> None:
    """Запускает главную activity приложения (как тап по иконке в лаунчере) —
    "monkey -p ... -c android.intent.category.LAUNCHER 1": сам находит launcher-activity. Раньше здесь всегда
    писалось «Готово.», что бы ни ответила магнитола: у техника на Geely Preface приложения «не запускались»,
    а в журнале — «Готово.» по десять раз (лог #1348: GInputBridge четыре раза подряд; у MicroG значка для
    запуска нет вовсе). Теперь: нет значка — так и пишем; monkey не сработал — запускаем activity напрямую
    (am start); через полторы секунды смотрим, есть ли приложение на экране, и если нет — что там вместо него."""
    ctx.log(f"Запускаю приложение: {package}")
    monkey = _shell_text(ctx, f"monkey -p {package} -c android.intent.category.LAUNCHER 1")
    if "No activities found" in monkey:
        ctx.log("Не удалось запустить: у приложения нет значка для запуска — оно работает в фоне "
                "или открывается из другого приложения.")
        return
    if "Events injected: 1" not in monkey:
        resolved = _shell_text(ctx, "cmd package resolve-activity --brief -a android.intent.action.MAIN "
                                    f"-c android.intent.category.LAUNCHER {package}")
        component = next((line.strip() for line in reversed(resolved.splitlines())
                          if "/" in line and " " not in line.strip()), None)
        if not component:
            answer = (monkey.splitlines() or ["магнитола не ответила"])[-1][:200]
            ctx.log(f"Не удалось запустить: {answer}")
            return
        started = _shell_text(ctx, f"am start -n {component}")
        if "Error" in started:
            ctx.log(f"Не удалось запустить: {started.splitlines()[-1][:200]}")
            return
    ctx.sleep(1.5)
    screen = _on_screen(ctx, package)
    if screen is None or screen[0] == 0:
        ctx.log("Готово.")
    elif screen[0] is not None:
        ctx.log(f"Готово: приложение открылось на дополнительном экране магнитолы (экран {screen[0]}), "
                "а не на основном.")
    else:
        now = f" (на экране: {', '.join(screen[1][:2])})" if screen[1] else ""
        ctx.log(f"Команда запуска прошла, но через полторы секунды приложения на экране нет{now}. Если оно не "
                "открылось — его закрывает прошивка магнитолы или у него нет окна.")


def _is_system_package(ctx, package: str) -> bool:
    """Штатное (системное) приложение магнитолы. Владелец, 2026-09-26: удалять и отключать их через
    программу нельзя — в списке выбора их больше нет (list_installed_packages без third_party_only=False),
    а это — на случай ручного ввода имени («Ввести вручную...») и старых моделей. Раньше техники так
    отключили сам «android» (лог #1013 — pm ответил «new state: disabled-user»)."""
    result = ctx.shell("pm list packages -s", check=False)
    if f"package:{package}" not in {line.strip() for line in (result.stdout or "").splitlines()}:
        return False
    return package not in _system_apps_by_us(ctx)


def _adb_output(ctx, *args) -> str:
    result = ctx.adb(*args, check=False, timeout=60)
    return " ".join(((result.stdout or "") + " " + (result.stderr or "")).split())


def _remove_system_app(ctx, package: str) -> None:
    """Удаляет приложение, поставленное программой в /system/app: pm uninstall системное не удаляет — нужны
    root и запись в системный раздел, как при установке. Android забудет приложение после перезагрузки."""
    root = _adb_output(ctx, "root")
    if "cannot run as root" in root.lower():
        ctx.log(f"Не удалось удалить: магнитола не дала права root ({root}).")
        return
    ctx.wait_for_device(timeout=60)
    remount = _adb_output(ctx, "remount")
    if "remount succeeded" not in remount.lower():
        ctx.log(f"Не удалось удалить: системный раздел не открылся на запись (adb remount: {remount or 'нет ответа'}).")
        return
    ctx.shell(f"rm -rf /system/app/{package}", check=False)
    if "LEFT" in (ctx.shell(f"[ -e /system/app/{package} ] && echo LEFT", check=False).stdout or ""):
        ctx.log("Не удалось удалить: файлы приложения остались в /system/app.")
        return
    ctx.log("Готово: приложение удалено из системной папки — оно исчезнет с магнитолы после перезагрузки.")


def uninstall_app(ctx, package: str) -> None:
    """Удаляет стороннее приложение (pm uninstall) — в отличие от disable_app,
    СТИРАЕТ его с магнитолы полностью. Штатные удалять нельзя (см.
    _is_system_package). Раньше здесь безусловно писалось "Готово."
    независимо от того, что реально ответило устройство (лог #536: техник попробовал
    "pm uninstall android" — ядро системы, заведомо защищено от удаления —
    и всё равно увидел "Готово.", хотя pm команду отклонила). Настоящий
    результат смотрим в тексте, как выводит сама pm ("Success"/"Failure
    [...]") — тот же приём, что и install_context.py:_check_pm_install_result
    на десктопе."""
    ctx.log(f"Удаляю приложение: {package}")
    if package in _system_apps_by_us(ctx):
        _remove_system_app(ctx, package)
        return
    if _is_system_package(ctx, package):
        ctx.log("Не удалось удалить: это штатное приложение магнитолы — удалять его через программу нельзя.")
        return
    result = ctx.shell(f"pm uninstall {package}", check=False)
    text = ((result.stdout or "") + (result.stderr or "")).strip()
    if "success" in text.lower() and "failure" not in text.lower():
        ctx.log("Готово.")
    elif "error: closed" in text.lower():
        # Geely OneOS/Monji, Jetour T2: pm закрыт прошивкой (логи #797, #962) — владелец (2026-09-25):
        # пусть удаляют штатно на самой магнитоле.
        ctx.log("Не удалось удалить: эта магнитола не даёт удалять приложения через программу — "
                "удалите штатно на самой магнитоле (Настройки → Приложения).")
    else:
        ctx.log(f"Не удалось удалить: {text or 'устройство не ответило'}")


def _set_enabled_state(ctx, command: str, fail_verb: str) -> None:
    """Общая часть disable_app/enable_app. Об успехе pm сообщает строкой
    «Package <пакет> new state: disabled-user|enabled»; раньше здесь, как и в
    uninstall_app до лога #536, безусловно писалось «Готово.» — даже когда
    устройство отказало (системный пакет, SecurityException, опечатка в
    имени пакета)."""
    result = ctx.shell(command, check=False)
    text = ((result.stdout or "") + (result.stderr or "")).strip()
    if "new state" in text.lower():
        ctx.log("Готово.")
    else:
        ctx.log(f"Не удалось {fail_verb}: {text or 'устройство не ответило'}")


def disable_app(ctx, package: str) -> None:
    """Отключает стороннее приложение (--user 0 — на всех наблюдавшихся
    магнитолах единственный профиль, id 0). Штатные отключать нельзя (см.
    _is_system_package). Не путать с pm uninstall — приложение остаётся
    установленным, просто не запускается и пропадает из лаунчера, обратимо
    через enable_app ниже."""
    ctx.log(f"Отключаю приложение: {package}")
    if _is_system_package(ctx, package):
        ctx.log("Не удалось отключить: это штатное приложение магнитолы — отключать его через программу нельзя.")
        return
    _set_enabled_state(ctx, f"pm disable-user --user 0 {package}", "отключить")


def enable_app(ctx, package: str) -> None:
    """Обратное disable_app — включает ранее отключённое приложение."""
    ctx.log(f"Включаю приложение: {package}")
    _set_enabled_state(ctx, f"pm enable {package}", "включить")
