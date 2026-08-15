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
#define MyAppVersion "0.3.7-alpha"
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
; уже установленной программе, а не пакуются заранее).
Source: "dist\magic_sqd_admin\*"; DestDir: "{app}"; Excludes: "apk,files,usb_files,__pycache__"; Flags: ignoreversion recursesubdirs createallsubdirs

; server.json/submit.json — см. installer.iss. admin.json — адрес сервера
; для входа через "Выгрузить на сервер..."/"Добавить APK..." (см.
; app/admin_config.py) — без него админ-сборка работает только с локальными
; cars/apk, без публикации. Ни один из трёх не в git — свои для боевого
; сервера, лежат рядом с installer.iss/admin_installer.iss.
Source: "server.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "submit.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "admin.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent
