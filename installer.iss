; Инсталлятор Magic SQD (Inno Setup).
; Собирает установщик из уже готовой onedir-сборки в dist\magic_sqd
; (см. magic_sqd.spec) - сначала pyinstaller magic_sqd.spec, потом этот скрипт.
;
; Ставим БЕЗ прав администратора в {localappdata}\Programs — приложение само
; пишет рядом с exe (startup.log, crash.log, докачанный контент cars/apk через
; content_sync.py), поэтому Program Files (только с admin) сюда не подходит.

#define MyAppName "Magic SQD"
#define MyAppVersion "0.4.11-alpha"
#define MyAppPublisher "Magic SQD"
#define MyAppExeName "magic_sqd.exe"

[Setup]
AppId={{A89547EA-79F1-4C17-91BE-4E9DD9EFE223}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer_output
OutputBaseFilename=MagicSQD_Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
; Подстраховка для автообновления (см. app/web/api/update_api.py): наш
; процесс сам закрывается перед тем, как спавнить этот инсталлятор, но если
; вдруг не успеет — Restart Manager сам закроет magic_sqd.exe, держащий
; файлы, вместо ошибки "файл занят другим процессом". В /VERYSILENT диалог
; закрытия приложений не показывается, закрывает молча.
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительные значки:"

[Files]
; apk/ (общая библиотека APK) и cars/*/files, cars/*/usb_files (payload
; конкретных моделей — прошивки и т.п.) сюда не идут: content_sync.py
; докачивает их с сервера сам — по кнопке "Скачать" и перед установкой
; модели соответственно (см. app/content_sync.py). Без этого инсталлятор
; весит гигабайты вместо пары десятков мегабайт.
; cars\_shared\*\* — отдельно от files/usb_files: подпапки внутри
; cars/_shared/ (например freetuga/, 444 МБ) — общие payload-наборы
; техника произвольного размера и с произвольным именем (см.
; StepSpec.usb_shared_folder), подтягиваются точечно перед конкретным
; USB-этапом (см. content_sync.py:sync_shared_folder), а НЕ ставятся
; заранее — поэтому в инсталлятор не идут, как и apk/files/usb_files выше.
; Сами *.py-хелперы (load_sibling.py и т.п.) лежат ПРЯМО в _shared/, а не
; в подпапке, поэтому не попадают под "_shared\*\*" (нужны stages.py
; каждой модели ВСЕГДА — их, в отличие от подпапок, ставим как обычно).
; "_shared\*" (один "*") исключил бы и их — проверено отдельно, не ставить
; обратно без такой же проверки.
Source: "dist\magic_sqd\*"; DestDir: "{app}"; Excludes: "apk,files,usb_files,__pycache__,_shared\*\*"; Flags: ignoreversion recursesubdirs createallsubdirs

; Адрес своего сервера (cars/apk) и ключ для "Отправить на проверку" —
; чтобы конечному пользователю не пришлось создавать эти файлы руками
; (см. app/content_config.py, app/submit_config.py, server/README.md §7).
; Не в git — свои для боевого сервера, лежат рядом с installer.iss.
Source: "server.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "submit.json"; DestDir: "{app}"; Flags: ignoreversion

; WebView2 Runtime Bootstrapper (официальный, ~2 МБ, качает подходящую под
; архитектуру машины Evergreen-версию рантайма с серверов Microsoft) — без
; него на части машин (Windows без встроенного/предустановленного WebView2,
; реальный случай — клиент техника с "чистой" Windows 10 без WebView2)
; главное окно открывается ПУСТЫМ БЕЛЫМ (сам pywebview/WinForms успешно
; создаёт окно, а вот встроенный Chromium-рендерер (msedgewebview2.exe) не
; запускается вовсе — ни один JS в этом окне не выполняется, поэтому и
; startup.log, и debug_all.log выглядят чисто, без единой ошибки). В
; tools/, а не во временную папку — сама программа тоже умеет предложить
; поставить WebView2 при следующем запуске (см. app/webview2_check.py,
; main_web.py:_ensure_webview2), если этот шаг установки почему-то не
; сработал (не было интернета и т.п.), и для этого ей нужна своя копия.
Source: "assets\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{app}\tools"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[UninstallDelete]
; Файлы, которые появляются УЖЕ ПОСЛЕ установки (content_sync.py качает
; cars/apk с сервера, программа сама пишет логи/состояние рядом с exe) —
; Inno Setup сам удаляет при uninstall только то, что перечислено в
; [Files] (то есть исходно установленное), про докачанное и порождённое
; во время работы ничего не знает. Без этого списка после удаления
; оставались папки cars/apk (иногда гигабайты) и служебные файлы.
Type: filesandordirs; Name: "{app}\cars"
Type: filesandordirs; Name: "{app}\apk"
Type: filesandordirs; Name: "{app}\debug_logs"
; Резервный Qt-движок отображения (см. app/qt_fallback.py) — качается и
; распаковывается уже во время работы программы, не через [Files], та же
; причина, что и у cars/apk выше.
Type: filesandordirs; Name: "{app}\_qt_fallback"
; Локальный стейджинг заявок клиентов в очереди на модерацию (только
; admin-сборка, см. app/pending_submissions.py) — та же причина, что и у
; _qt_fallback выше.
Type: filesandordirs; Name: "{app}\_pending"
Type: files; Name: "{app}\*.log"
Type: files; Name: "{app}\client_id.txt"
Type: files; Name: "{app}\seen_versions.json"
Type: files; Name: "{app}\DEBUG_LOG_ALL"

[Code]
// См. официальную документацию Microsoft (Detect if a WebView2 Runtime is
// already installed, learn.microsoft.com/microsoft-edge/webview2/concepts/
// distribution) — при per-machine или per-user установке WebView2 версия
// пишется в один из этих двух путей реестра (ключ "pv"); отсутствие ключа
// или значение "" / "0.0.0.0" значит рантайма нет. Проверяем оба варианта
// установки, не только HKLM, — на части машин техников не бывает прав
// администратора, и WebView2 мог встать как per-user.
function IsWebView2Installed(): Boolean;
var
  Version: String;
begin
  Result := False;
  if RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) then
    if (Version <> '') and (Version <> '0.0.0.0') then
      Result := True;
  if not Result then
    if RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) then
      if (Version <> '') and (Version <> '0.0.0.0') then
        Result := True;
end;

[Run]
; Молча ставит WebView2 Runtime, если его ещё нет (см. IsWebView2Installed
; выше и комментарий у Source: MicrosoftEdgeWebview2Setup.exe) — САМЫЙ
; первый Run, до запуска самой программы: без рантайма она всё равно
; откроется пустым белым окном. /silent /install — официальный ключ
; бутстраппера (см. документацию Microsoft), сам решает per-machine или
; per-user исходя из прав, с которыми запущен инсталлятор.
Filename: "{app}\tools\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Устанавливаю компонент WebView2 Runtime..."; Check: not IsWebView2Installed; Flags: waituntilterminated

; БЕЗ skipifsilent (в отличие от admin_installer.iss/installer_debug.iss) —
; автообновление (app/web/api/update_api.py) ставит /VERYSILENT именно
; затем, чтобы программа сама перезапустилась после тихой установки; это
; сейчас единственный сценарий силент-установки в проекте.
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall
