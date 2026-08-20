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
_APPOPS = {
    "android.permission.SYSTEM_ALERT_WINDOW": "SYSTEM_ALERT_WINDOW",
    "android.permission.WRITE_SETTINGS": "WRITE_SETTINGS",
    "android.permission.PACKAGE_USAGE_STATS": "GET_USAGE_STATS",
}

# Доп. appops без прямого аналога в списке "запрошенных разрешений" из
# манифеста (не permission, а именно op) — выдаются безусловно всем, не
# только по совпадению с requested-permissions, как и _APPOPS выше:
#   REQUEST_INSTALL_PACKAGES — приложению можно ставить APK без диалога
#   "Установка из неизвестных источников" (актуально для магазинов вроде
#   RuStore, которые сами ставят другие приложения).
#   ACTIVATE_VPN — VPN-клиент может поднять соединение без системного
#   диалога подтверждения VPN.
_EXTRA_APPOPS = ["REQUEST_INSTALL_PACKAGES", "ACTIVATE_VPN"]

# WRITE_SECURE_SETTINGS — обычное (не appops) разрешение, но с protection
# level signature|privileged — недоступно приложению через диалог, зато
# выдаётся через adb shell pm grant (shell сам обладает этой привилегией),
# что и есть единственный практический способ дать его сторонним программам.
_WRITE_SECURE_SETTINGS = "android.permission.WRITE_SECURE_SETTINGS"

_REQUESTED_PERMISSIONS_RE = re.compile(r"^requested permissions:\s*$")
_PERMISSION_LINE_RE = re.compile(r"^([\w.]+)(?::.*)?$")
_ACCESSIBILITY_NAME_RE = re.compile(r"name=([\w.]+)")


def list_installed_packages(ctx, third_party_only=True):
    """Список пакетов, установленных на магнитоле (для диалога выбора
    приложения — ctx.ask_choice). third_party_only=True (по умолчанию) —
    без системных пакетов производителя/Android, иначе список из сотен
    записей неудобно листать ради обычно нужных технику сторонних APK."""
    flag = "-3" if third_party_only else ""
    result = ctx.shell(f"pm list packages {flag}".strip(), check=False)
    packages = []
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("package:"):
            packages.append(line[len("package:"):].strip())
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


def _find_accessibility_service(package: str, dumpsys_output: str) -> str | None:
    """Best-effort поиск класса службы спецвозможностей приложения в
    dumpsys package — формат вывода не стандартизован между версиями
    Android/прошивками, поэтому если не нашли, просто не включаем эту
    часть (не критично, остальные разрешения всё равно выдаются)."""
    lines = dumpsys_output.splitlines()
    for i, line in enumerate(lines):
        if "BIND_ACCESSIBILITY_SERVICE" not in line:
            continue
        for back in reversed(lines[max(0, i - 15):i]):
            m = _ACCESSIBILITY_NAME_RE.search(back.strip())
            if m:
                cls = m.group(1)
                if cls.startswith("."):
                    cls = package + cls
                elif "." not in cls:
                    cls = f"{package}.{cls}"
                return f"{package}/{cls}"
    return None


def _enable_accessibility_service(ctx, package: str, dumpsys_output: str) -> None:
    component = _find_accessibility_service(package, dumpsys_output)
    if not component:
        return
    current = (ctx.shell("settings get secure enabled_accessibility_services", check=False).stdout or "").strip()
    existing = [c for c in current.split(":") if c] if current and current != "null" else []
    if component not in existing:
        existing.append(component)
        ctx.shell(f"settings put secure enabled_accessibility_services {':'.join(existing)}", check=False)
    ctx.shell("settings put secure accessibility_enabled 1", check=False)
    ctx.log(f"Служба специальных возможностей включена: {component}")


def grant_all_permissions(ctx, package: str) -> None:
    """Выдаёт приложению все разрешения, которые оно запрашивает в
    манифесте (обычные runtime + WRITE_SECURE_SETTINGS), плюс "спецдоступы"
    через AppOps (показ поверх других окон, изменение системных настроек,
    статистика использования, установка APK без диалога, активация VPN без
    диалога) и, если удалось найти, включает его службу специальных
    возможностей — то, что на большинстве магнитол нельзя сделать штатным
    экраном настроек. Плюс освобождает приложение от ограничений
    энергосбережения (Doze/App Standby) — иначе Android рано или поздно
    убивает его в фоне, частая жалоба именно на магнитолах."""
    ctx.log(f"Выдаю разрешения: {package}")
    dumpsys_output = ctx.shell(f"dumpsys package {package}", check=False).stdout or ""
    requested = _parse_requested_permissions(dumpsys_output)
    if not requested:
        requested = list(_COMMON_DANGEROUS_PERMISSIONS)

    for perm in requested:
        if perm in _APPOPS:
            continue  # выдаётся ниже через appops, не pm grant
        ctx.shell(f"pm grant {package} {perm}", check=False)

    for op in _APPOPS.values():
        ctx.shell(f"appops set {package} {op} allow", check=False)
    for op in _EXTRA_APPOPS:
        ctx.shell(f"appops set {package} {op} allow", check=False)
    ctx.shell(f"pm grant {package} {_WRITE_SECURE_SETTINGS}", check=False)
    ctx.shell(f"dumpsys deviceidle whitelist +{package}", check=False)

    _enable_accessibility_service(ctx, package, dumpsys_output)
    ctx.log("Разрешения выданы.")


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


def disable_app(ctx, package: str) -> None:
    """Отключает приложение (--user 0 — на всех наблюдавшихся магнитолах
    единственный профиль, id 0) — для предустановленных программ, которые
    мешают (конкурирующая навигация, голосовой ассистент и т.п.) и которые
    нельзя просто удалить (системные, pm uninstall для них не работает без
    root). Не путать с pm uninstall — приложение остаётся установленным,
    просто не запускается и пропадает из лаунчера, обратимо через
    enable_app ниже."""
    ctx.log(f"Отключаю приложение: {package}")
    ctx.shell(f"pm disable-user --user 0 {package}", check=False)
    ctx.log("Готово.")


def enable_app(ctx, package: str) -> None:
    """Обратное disable_app — включает ранее отключённое приложение."""
    ctx.log(f"Включаю приложение: {package}")
    ctx.shell(f"pm enable {package}", check=False)
    ctx.log("Готово.")
