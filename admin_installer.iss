; Инсталлятор Magic SQD Admin (Inno Setup) — админ-сборка, отдельно от
; installer.iss (обычный клиент для техников). Собирает установщик из уже
; готовой onedir-сборки в dist\magic_sqd_admin (см. admin.spec) — сначала
; pyinstaller admin.spec, потом этот скрипт.
;
; Свой AppId/DefaultDirName/OutputBaseFilename — чтобы ставился РЯДОМ с
; обычным клиентом на одной машине разработчика, а не поверх него (Windows
; иначе считала бы их одной и той же программой по AppId).
;
; Ставим БЕЗ прав администратора в {localappdata}\Programs — та же причина,
; что и в installer.iss (программа сама пишет рядом с exe: startup.log,
; crash.log, cars/apk через content_sync.py).

#define MyAppName "Magic SQD Admin"
#define MyAppVersion "0.5.1"
#define MyAppPublisher "Magic SQD"
#define MyAppExeName "magic_sqd_admin.exe"

[Setup]
AppId={{FDC125DF-3D4C-4172-9DB1-449DA5CDD9E1}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer_output
OutputBaseFilename=MagicSQDAdmin_Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительные значки:"

[Files]
; apk/ и cars/*/files, cars/*/usb_files сюда не идут — та же причина, что и
; в installer.iss: content_sync.py докачивает их с сервера сам (и APK,
; добавленные через "Добавить APK в общую библиотеку...", создаются прямо в
; уже установленной программе, а не пакуются заранее). cars\_shared\*\* —
; тоже см. installer.iss: подпапки cars/_shared/ (например freetuga/,
; сотни МБ) — тяжёлые payload-наборы техника (StepSpec.usb_shared_folder),
; подтягиваются точечно, а не ставятся заранее; *.py-хелперы ПРЯМО в
; _shared/ (не в подпапке) под это не попадают и по-прежнему ставятся.
Source: "dist\magic_sqd_admin\*"; DestDir: "{app}"; Excludes: "apk,files,usb_files,__pycache__,_shared\*\*"; Flags: ignoreversion recursesubdirs createallsubdirs

; server.json/submit.json — см. installer.iss. admin.json — адрес сервера
; для входа через "Выгрузить на сервер..."/"Добавить APK..." (см.
; app/admin_config.py) — без него админ-сборка работает только с локальными
; cars/apk, без публикации. Ни один из трёх не в git — свои для боевого
; сервера, лежат рядом с installer.iss/admin_installer.iss.
Source: "server.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "submit.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "admin.json"; DestDir: "{app}"; Flags: ignoreversion

; WebView2 Runtime Bootstrapper — см. installer.iss за полным обоснованием
; (без него окно программы открывается пустым белым).
Source: "assets\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{app}\tools"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[UninstallDelete]
; См. installer.iss — та же причина: cars/apk докачиваются с сервера уже
; после установки, логи/состояние тоже пишутся во время работы, Inno Setup
; сам знает только про исходно установленные [Files].
Type: filesandordirs; Name: "{app}\cars"
Type: filesandordirs; Name: "{app}\apk"
Type: filesandordirs; Name: "{app}\debug_logs"
; Резервный Qt-движок отображения — см. installer.iss за полным обоснованием.
Type: filesandordirs; Name: "{app}\_qt_fallback"
; Локальный стейджинг заявок клиентов в очереди на модерацию (см.
; app/pending_submissions.py) — та же причина, что и у _qt_fallback выше.
Type: filesandordirs; Name: "{app}\_pending"
Type: files; Name: "{app}\*.log"
Type: files; Name: "{app}\client_id.txt"
Type: files; Name: "{app}\seen_versions.json"
Type: files; Name: "{app}\DEBUG_LOG_ALL"

[Code]
// См. installer.iss за полным обоснованием.
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
Filename: "{app}\tools\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Устанавливаю компонент WebView2 Runtime..."; Check: not IsWebView2Installed; Flags: waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent
