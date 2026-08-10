; Инсталлятор Magic SQD (Inno Setup).
; Собирает установщик из уже готовой onedir-сборки в dist\magic_sqd
; (см. magic_sqd.spec) - сначала pyinstaller magic_sqd.spec, потом этот скрипт.
;
; Ставим БЕЗ прав администратора в {localappdata}\Programs — приложение само
; пишет рядом с exe (startup.log, crash.log, докачанный контент cars/apk через
; content_sync.py), поэтому Program Files (только с admin) сюда не подходит.

#define MyAppName "Magic SQD"
#define MyAppVersion "0.3.1-alpha"
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
Source: "dist\magic_sqd\*"; DestDir: "{app}"; Excludes: "apk,files,usb_files,__pycache__"; Flags: ignoreversion recursesubdirs createallsubdirs

; Адрес своего сервера (cars/apk) и ключ для "Отправить на проверку" —
; чтобы конечному пользователю не пришлось создавать эти файлы руками
; (см. app/content_config.py, app/submit_config.py, server/README.md §7).
; Не в git — свои для боевого сервера, лежат рядом с installer.iss.
Source: "server.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "submit.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall skipifsilent
